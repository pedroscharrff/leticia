"""
Envio proativo de mensagens (fora do fluxo turn-by-turn do bot).

Usado por:
  • webhook de pagamento PIX para confirmar pedido pago
  • job de carrinho abandonado
  • job de recompra de medicamento contínuo

Caminho canônico (broker): consulta `public.tenant_integrations` ativa do
tenant, aplica `reply_body_template` no payload (mesma função usada por
`_send_via_broker` no fluxo de resposta normal e por ofertas pré-handoff /
resumo de pedido) e POSTa em `reply_url` com `reply_method`/`reply_headers`
próprios da integração.

Esse é o MESMO caminho que ofertas pré-handoff e resumo de pedido seguem —
invariante de produto. Não inventar caminho novo aqui.

Fallback de compatibilidade: tenants legados sem `tenant_integrations`
configurado ainda usam `tenants.callback_url` com o payload antigo
`{phone, body, proactive, kind}`. Quando todos migrarem para o broker, esse
ramo pode sair.
"""
from __future__ import annotations

import json as _json
from typing import Any

import httpx
import structlog

from db.postgres import get_db_conn
from services import broker as broker_svc

log = structlog.get_logger()


# ── Lookups ─────────────────────────────────────────────────────────────────

async def get_callback_url(tenant_id: str) -> str | None:
    """Callback legado em `public.tenants.callback_url`. Usado só como fallback."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT callback_url FROM public.tenants WHERE id = $1",
            tenant_id,
        )
    if not row:
        return None
    return row["callback_url"]


async def get_active_channel_config(tenant_id: str) -> dict | None:
    """handoff_config do primeiro canal ativo em `tenant_channels`.

    Mantida para resolver provider de envio de mídia (channel_media.py).
    NÃO é o caminho de envio proativo — para isso, use `_get_outbound_integration`.
    """
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT handoff_config
              FROM public.tenant_channels
             WHERE tenant_id = $1
               AND active = TRUE
               AND handoff_config IS NOT NULL
               AND handoff_config != '{}'::jsonb
             ORDER BY created_at
             LIMIT 1
            """,
            tenant_id,
        )
    if not row or not row["handoff_config"]:
        return None
    cfg = row["handoff_config"]
    if isinstance(cfg, str):
        try:
            return _json.loads(cfg) or None
        except Exception:
            return None
    return dict(cfg) if cfg else None


async def _get_outbound_integration(tenant_id: str) -> dict | None:
    """Primeira integração ativa do tenant capaz de receber outbound.

    Critérios: `enabled = TRUE`, `reply_mode = 'forward'` (proativo não tem
    inbound síncrono pra responder), `reply_url` preenchido. Esse é o mesmo
    registro que `_run_broker_flow` carrega ao responder uma mensagem
    normal — então usamos exatamente o mesmo template de envio.
    """
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, slug, reply_url, reply_method, reply_headers,
                   reply_body_template
              FROM public.tenant_integrations
             WHERE tenant_id  = $1
               AND enabled    = TRUE
               AND reply_mode = 'forward'
               AND reply_url IS NOT NULL
               AND reply_url <> ''
             ORDER BY created_at
             LIMIT 1
            """,
            tenant_id,
        )
    if not row:
        return None
    return dict(row)


# ── Senders ─────────────────────────────────────────────────────────────────

def _coerce_jsonb(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = _json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


async def _send_via_broker(
    integration: dict,
    *,
    tenant_id: str,
    phone: str,
    body: str,
    kind: str,
    extra: dict | None,
) -> bool:
    """POSTa para `reply_url` da integração aplicando `reply_body_template`.

    Contrato idêntico ao `_send_via_broker` definido em workers/celery_app.py:
    mesmo `ctx` (campos `reply`, `phone`, `message`, `name`, `session_id`,
    `event_id`) + extras de proativo (`kind`, `proactive`, `extra`). Assim,
    qualquer template já configurado pelo cliente para respostas normais
    continua funcionando aqui sem ajuste — e proativo só precisa adicionar
    a chave `kind` (ou `proactive`) ao template caso queira diferenciar.
    """
    template = _coerce_jsonb(integration.get("reply_body_template"))
    headers_raw = _coerce_jsonb(integration.get("reply_headers"))
    headers = {str(k): str(v) for k, v in headers_raw.items() if k and v}
    method = (integration.get("reply_method") or "POST").upper()
    url = integration["reply_url"]

    ctx = {
        "reply":      body,
        "phone":      phone,
        "message":    "",          # proativo: não há mensagem do cliente
        "name":       None,
        "session_id": phone,       # melhor proxy disponível em proativo
        "event_id":   None,
        "kind":       kind,
        "proactive":  True,
        "extra":      extra or {},
    }
    payload = broker_svc.apply_mapping(template, ctx) if template else {"reply": body}

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.request(method, url, json=payload, headers=headers)
            resp.raise_for_status()
        log.info("outbound.broker.sent",
                 tenant=tenant_id, integration=str(integration.get("id")),
                 kind=kind, phone_prefix=str(phone)[:4])
        return True
    except httpx.HTTPError as exc:
        log.warning("outbound.broker.failed",
                    tenant=tenant_id, integration=str(integration.get("id")),
                    kind=kind, exc=str(exc))
        return False


async def _send_via_callback_legacy(
    callback_url: str,
    *,
    tenant_id: str,
    phone: str,
    body: str,
    kind: str,
    extra: dict | None,
) -> bool:
    """Fallback para tenants que ainda não migraram para tenant_integrations."""
    payload: dict[str, Any] = {
        "phone":     phone,
        "body":      body,
        "proactive": True,
        "kind":      kind,
    }
    if extra:
        payload["extra"] = extra
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(callback_url, json=payload)
            resp.raise_for_status()
        log.info("outbound.callback.sent",
                 tenant=tenant_id, kind=kind, phone_prefix=str(phone)[:4])
        return True
    except httpx.HTTPError as exc:
        log.warning("outbound.callback.failed",
                    tenant=tenant_id, kind=kind, exc=str(exc))
        return False


# ── Public API ──────────────────────────────────────────────────────────────

async def send_proactive_message(
    tenant_id: str,
    phone: str,
    body: str,
    *,
    kind: str = "proactive",
    extra: dict | None = None,
) -> bool:
    """Envia mensagem proativa pelo caminho canônico (broker).

    Args:
        kind: rótulo para o tenant identificar a origem ('payment_confirmed',
              'cart_recovery', 'refill_nudge'). Vai no payload e no log.

    Caminho:
        1) `tenant_integrations` ativa em modo forward → POST com
           reply_body_template aplicado. MESMA rota de ofertas pré-handoff
           e resumo de pedido (invariante de produto).
        2) Fallback legado: `tenants.callback_url` com payload antigo.

    Retorna True se entregue.
    """
    integration = await _get_outbound_integration(tenant_id)
    if integration:
        return await _send_via_broker(
            integration,
            tenant_id=tenant_id, phone=phone, body=body,
            kind=kind, extra=extra,
        )

    callback = await get_callback_url(tenant_id)
    if callback:
        log.info("outbound.fallback_to_callback",
                 tenant=tenant_id, kind=kind,
                 reason="no_active_tenant_integration")
        return await _send_via_callback_legacy(
            callback,
            tenant_id=tenant_id, phone=phone, body=body,
            kind=kind, extra=extra,
        )

    log.warning("outbound.no_route", tenant=tenant_id, kind=kind)
    return False
