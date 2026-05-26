"""
Envio proativo de mensagens (fora do fluxo turn-by-turn do bot).

Usado por:
  • webhook de pagamento PIX para confirmar pedido pago
  • job de carrinho abandonado
  • job de recompra de medicamento contínuo

Estratégia: postar no `callback_url` do tenant com `{phone, body, proactive: true}`.
O gateway do tenant (Evolution, Z-API, etc.) entrega ao WhatsApp.
"""
from __future__ import annotations

import httpx
import structlog

from db.postgres import get_db_conn

log = structlog.get_logger()


async def get_callback_url(tenant_id: str) -> str | None:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT callback_url FROM public.tenants WHERE id = $1",
            tenant_id,
        )
    if not row:
        return None
    return row["callback_url"]


async def get_active_channel_config(tenant_id: str) -> dict | None:
    """Retorna o handoff_config (canal ativo + provider) do PRIMEIRO canal
    ativo do tenant. None se não houver. Usado para resolver provider de
    envio de mídia em uso PROATIVO (jobs).

    Dentro do worker per-message, prefira usar a integração já carregada
    em escopo (já tem o handoff_config). Esta função evita um SELECT extra.
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
        import json as _json
        try:
            return _json.loads(cfg) or None
        except Exception:
            return None
    return dict(cfg) if cfg else None


async def send_proactive_message(
    tenant_id: str,
    phone: str,
    body: str,
    *,
    kind: str = "proactive",
    extra: dict | None = None,
) -> bool:
    """Envia mensagem proativa via callback_url do tenant. Retorna True se entregue.

    Args:
        kind: rótulo para o tenant identificar a origem ('payment_confirmed',
              'cart_recovery', 'refill_nudge'). Útil para auditoria/relatório.
    """
    callback = await get_callback_url(tenant_id)
    if not callback:
        log.warning("outbound.no_callback", tenant=tenant_id)
        return False

    payload = {
        "phone":     phone,
        "body":      body,
        "proactive": True,
        "kind":      kind,
    }
    if extra:
        payload["extra"] = extra

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(callback, json=payload)
            resp.raise_for_status()
            return True
    except httpx.HTTPError as exc:
        log.warning("outbound.delivery_failed",
                    tenant=tenant_id, phone=phone, exc=str(exc))
        return False
