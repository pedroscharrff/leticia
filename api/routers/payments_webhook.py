"""
Webhook do Asaas para confirmação de PIX (capability payments.pix_asaas).

POST /webhook/payments/asaas
  body: payload Asaas: {event, payment: {id, ...}}

Quando o evento é PAYMENT_CONFIRMED/RECEIVED para uma cobrança PIX:
  1) Atualiza public.payments_log → status='paid'
  2) Atualiza {schema}.orders → status='confirmed' (se ainda pending)
  3) Dispara mensagem proativa ao cliente via callback do tenant

Webhooks Asaas vêm sem assinatura específica de cobrança; protegemos com um
token no header (`asaas-access-token`) configurado no painel Asaas — mas
preferimos NÃO bloquear o webhook (Asaas pode retentar): retornamos 200 mesmo
quando o payload não bate, para evitar fila de retry infinita no provedor.
"""
from __future__ import annotations

import structlog
from fastapi import APIRouter, Request

from db.postgres import get_db_conn
from services.outbound import send_proactive_message
from services.payments_asaas import update_charge_from_webhook

log = structlog.get_logger()
router = APIRouter(prefix="/webhook/payments", tags=["webhook:payments"])


@router.post("/asaas")
async def asaas_payment_webhook(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return {"ok": True, "ignored": "invalid_json"}

    record = await update_charge_from_webhook(payload)
    if not record:
        return {"ok": True, "ignored": payload.get("event")}

    # Se foi confirmação de pagamento → marca o pedido e avisa o cliente
    if record["status"] == "paid":
        order_id    = record.get("order_id")
        schema      = record.get("schema_name")
        tenant_id   = str(record["tenant_id"])
        phone       = record.get("phone")
        amount      = float(record.get("amount") or 0)

        if order_id and schema:
            try:
                async with get_db_conn() as conn:
                    await conn.execute(f"SET search_path = {schema}, public")
                    await conn.execute(
                        "UPDATE orders SET status = 'confirmed', updated_at = NOW() "
                        "WHERE id = $1 AND status = 'pending'",
                        order_id,
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("payments.order_update_failed",
                            order=order_id, exc=str(exc))

        if phone:
            short = str(order_id)[:8] if order_id else ""
            body = (
                f"✅ Recebemos seu PIX de R$ {amount:.2f} para o pedido "
                f"#{short}. Já estou preparando tudo por aqui — em breve "
                "te aviso quando estiver pronto. Obrigado!"
            )
            await send_proactive_message(
                tenant_id, phone, body,
                kind="payment_confirmed",
                extra={"order_id": str(order_id) if order_id else None,
                       "amount": amount},
            )

    log.info("payments.webhook_processed",
             event=payload.get("event"), status=record["status"])
    return {"ok": True, "status": record["status"]}
