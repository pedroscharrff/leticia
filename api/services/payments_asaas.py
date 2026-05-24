"""
Cliente Asaas focado em cobranças PIX em tempo real (capability `payments.pix_asaas`).

Usado tanto pelo tool `gerar_link_pix` (vendedor) quanto pelo webhook
`/webhook/payments/asaas` (que recebe confirmação de pagamento e atualiza o
pedido + dispara mensagem ao cliente).

Cada tenant pode ter sua própria conta Asaas — o token é armazenado em
`public.tenant_secrets` sob a chave `ASAAS_API_KEY`. Se ausente, faz fallback
para `settings.asaas_api_key` global (modo "Asaas da B4B" para tenants que
não querem gerenciar conta própria).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import structlog

from config import settings
from db.postgres import get_db_conn
from services.secrets import get_secret

log = structlog.get_logger()

_TIMEOUT = 15


async def _resolve_api_key(tenant_id: str) -> str | None:
    """Token do tenant > token global da B4B (fallback)."""
    try:
        tk = await get_secret(tenant_id, "ASAAS_API_KEY")
        if tk:
            return tk
    except Exception as exc:  # noqa: BLE001
        log.warning("payments.asaas.secret_read_failed",
                    tenant=tenant_id, exc=str(exc))
    return settings.asaas_api_key or None


def _headers(token: str) -> dict:
    return {"access_token": token, "Content-Type": "application/json"}


async def _ensure_customer(token: str, *, name: str | None, phone: str,
                            cpf_cnpj: str | None, email: str | None,
                            external_ref: str) -> str | None:
    """Garante que existe um customer no Asaas e devolve seu id.

    Asaas exige CPF/CNPJ para cobranças PIX. Se o cliente não tem CPF
    cadastrado, retornamos None — caller deve pedir ao cliente.
    """
    if not cpf_cnpj:
        return None
    base = settings.asaas_base_url
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        # tenta achar por externalReference (idempotente)
        resp = await client.get(
            f"{base}/customers",
            params={"externalReference": external_ref},
            headers=_headers(token),
        )
        if resp.status_code == 200:
            data = resp.json().get("data") or []
            if data:
                return data[0].get("id")

        resp = await client.post(
            f"{base}/customers",
            headers=_headers(token),
            json={
                "name":              name or f"Cliente {phone[-4:]}",
                "cpfCnpj":           cpf_cnpj,
                "email":             email,
                "phone":             phone,
                "externalReference": external_ref,
            },
        )
        if resp.status_code >= 400:
            log.warning("payments.asaas.customer_create_failed",
                        status=resp.status_code, body=resp.text[:200])
            return None
        return resp.json().get("id")


async def create_pix_charge(
    tenant_id: str,
    *,
    order_id: str,
    schema_name: str,
    phone: str,
    name: str | None,
    cpf_cnpj: str | None,
    email: str | None,
    amount: float,
    description: str,
    expires_minutes: int = 60,
) -> dict:
    """Cria cobrança PIX no Asaas e retorna {qr_code, qr_image_url, payment_url, ...}.

    Persistido em `public.payments_log`. Se falhar, retorna `{"error": "..."}`
    e NÃO levanta — caller decide se mostra mensagem ao cliente.
    """
    token = await _resolve_api_key(tenant_id)
    if not token:
        return {"error": "Asaas não configurado para esta farmácia."}

    customer_id = await _ensure_customer(
        token,
        name=name, phone=phone, cpf_cnpj=cpf_cnpj, email=email,
        external_ref=f"{tenant_id}:{phone}",
    )
    if not customer_id:
        return {"error": "Para gerar o PIX preciso do CPF do cliente. "
                          "Solicite-o antes de fechar o pedido."}

    base = settings.asaas_base_url
    due_date = (datetime.now(timezone.utc).date()
                + timedelta(days=1)).isoformat()

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # 1) Cria cobrança PIX
            charge_resp = await client.post(
                f"{base}/payments",
                headers=_headers(token),
                json={
                    "customer":          customer_id,
                    "billingType":       "PIX",
                    "value":             round(float(amount), 2),
                    "dueDate":           due_date,
                    "description":       description[:500],
                    "externalReference": f"{tenant_id}:{order_id}",
                },
            )
            if charge_resp.status_code >= 400:
                log.warning("payments.asaas.charge_failed",
                            status=charge_resp.status_code,
                            body=charge_resp.text[:200])
                return {"error": "Não consegui gerar o PIX agora."}
            charge = charge_resp.json()
            external_id = charge.get("id")
            invoice_url = charge.get("invoiceUrl")

            # 2) Pega QR code (endpoint separado no Asaas)
            qr_resp = await client.get(
                f"{base}/payments/{external_id}/pixQrCode",
                headers=_headers(token),
            )
            qr_data = qr_resp.json() if qr_resp.status_code == 200 else {}
    except httpx.HTTPError as exc:
        log.warning("payments.asaas.http_error", exc=str(exc))
        return {"error": "Falha de comunicação com o gateway de pagamento."}

    qr_code      = qr_data.get("payload") or ""
    qr_image_url = qr_data.get("encodedImage")  # base64 PNG do Asaas
    expires_at   = datetime.now(timezone.utc) + timedelta(minutes=expires_minutes)

    # Persiste em payments_log
    try:
        async with get_db_conn() as conn:
            await conn.execute(
                """
                INSERT INTO public.payments_log
                    (tenant_id, schema_name, order_id, phone, provider,
                     external_id, amount, status, qr_code, qr_image_url,
                     payment_url, expires_at, raw_payload, created_at)
                VALUES ($1,$2,$3,$4,'asaas',$5,$6,'pending',$7,$8,$9,$10,$11::jsonb, NOW())
                """,
                tenant_id, schema_name, order_id, phone, external_id,
                round(float(amount), 2), qr_code,
                (f"data:image/png;base64,{qr_image_url}" if qr_image_url else None),
                invoice_url, expires_at, json.dumps({"charge": charge, "qr": qr_data}),
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("payments.asaas.persist_failed", exc=str(exc))

    return {
        "external_id":  external_id,
        "qr_code":      qr_code,
        "qr_image_url": (f"data:image/png;base64,{qr_image_url}" if qr_image_url else None),
        "payment_url":  invoice_url,
        "expires_at":   expires_at.isoformat(),
        "amount":       round(float(amount), 2),
    }


async def update_charge_from_webhook(payload: dict) -> dict | None:
    """Processa um evento de webhook Asaas relacionado a PIX.

    Retorna o registro atualizado de payments_log (com tenant_id + schema_name
    + order_id) para que o caller atualize o pedido e mande mensagem ao
    cliente. Retorna None se o evento não for relevante.
    """
    event = payload.get("event", "")
    pay   = payload.get("payment", {}) or {}
    external_id = pay.get("id")
    if not external_id:
        return None

    # Eventos que nos interessam:
    status_map = {
        "PAYMENT_CONFIRMED": "paid",
        "PAYMENT_RECEIVED":  "paid",
        "PAYMENT_REFUNDED":  "refunded",
        "PAYMENT_OVERDUE":   "expired",
        "PAYMENT_DELETED":   "cancelled",
    }
    new_status = status_map.get(event)
    if not new_status:
        return None

    paid_at_clause = ", paid_at = NOW()" if new_status == "paid" else ""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.payments_log
               SET status = $1,
                   raw_payload = COALESCE(raw_payload, '{{}}'::jsonb) || $2::jsonb,
                   updated_at = NOW()
                   {paid_at_clause}
             WHERE external_id = $3
            RETURNING tenant_id, schema_name, order_id, phone, amount, status
            """,
            new_status, json.dumps({"event": event, "payment": pay}), external_id,
        )

    if not row:
        return None
    return dict(row)
