"""
Per-tenant order-status notification templates.

When an operator changes an order's status in the panel, the API loads the
template for the new status, formats it with the order/customer context,
and POSTs the message to the tenant's `callback_url` (the same channel
already used for agent replies).
"""
from __future__ import annotations

import structlog
import httpx

from db.postgres import get_db_conn

log = structlog.get_logger()


VALID_STATUSES = ["pending", "confirmed", "processing", "shipped", "delivered", "cancelled"]


DEFAULT_TEMPLATES: dict[str, dict] = {
    "pending":    {"enabled": False, "template": "Olá {nome}! Recebi seu pedido #{numero_pedido} no valor de {total}. Já encaminhei para nossa equipe."},
    "confirmed":  {"enabled": True,  "template": "Boas notícias, {nome}! Seu pedido #{numero_pedido} foi confirmado e já estamos preparando."},
    "processing": {"enabled": True,  "template": "{nome}, seu pedido #{numero_pedido} está sendo separado pela nossa equipe."},
    "shipped":    {"enabled": True,  "template": "{nome}, seu pedido #{numero_pedido} saiu para entrega! Em instantes você recebe."},
    "delivered":  {"enabled": True,  "template": "{nome}, seu pedido #{numero_pedido} foi entregue. Obrigado pela preferência!"},
    "cancelled":  {"enabled": True,  "template": "{nome}, seu pedido #{numero_pedido} foi cancelado. Se foi engano, é só nos avisar."},
}


async def list_status_messages(tenant_id: str) -> list[dict]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT status, enabled, template FROM public.tenant_order_status_messages "
            "WHERE tenant_id = $1",
            tenant_id,
        )
    by_status = {r["status"]: dict(r) for r in rows}
    out: list[dict] = []
    for s in VALID_STATUSES:
        row = by_status.get(s)
        out.append({
            "status": s,
            "enabled": bool(row["enabled"]) if row else DEFAULT_TEMPLATES[s]["enabled"],
            "template": row["template"] if row else DEFAULT_TEMPLATES[s]["template"],
        })
    return out


async def upsert_status_message(
    tenant_id: str, status: str, *, enabled: bool, template: str, actor_email: str,
) -> dict:
    if status not in VALID_STATUSES:
        raise ValueError(f"status inválido: {status}")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_order_status_messages
                (tenant_id, status, enabled, template, updated_by, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (tenant_id, status) DO UPDATE
                SET enabled    = EXCLUDED.enabled,
                    template   = EXCLUDED.template,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
            RETURNING status, enabled, template
            """,
            tenant_id, status, enabled, template, actor_email,
        )
    return dict(row)


async def get_status_message(tenant_id: str, status: str) -> dict | None:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT enabled, template FROM public.tenant_order_status_messages "
            "WHERE tenant_id = $1 AND status = $2",
            tenant_id, status,
        )
    if row:
        return {"enabled": bool(row["enabled"]), "template": row["template"]}
    fallback = DEFAULT_TEMPLATES.get(status)
    return dict(fallback) if fallback else None


def _format_money(v: float | int | None) -> str:
    try:
        return f"R$ {float(v or 0):.2f}".replace(".", ",")
    except Exception:
        return "R$ 0,00"


def render_template(template: str, ctx: dict) -> str:
    """Safely substitute placeholders without crashing on missing keys."""
    items_block = "\n".join(
        f"- {it.get('qty', 1)}x {it.get('name', '?')}"
        for it in (ctx.get("items") or [])
    )
    values = {
        "nome":          (ctx.get("customer_name") or "").strip() or "olá",
        "numero_pedido": str(ctx.get("order_id") or "")[:8],
        "total":         _format_money(ctx.get("total")),
        "itens":         items_block,
        "farmacia":      ctx.get("pharmacy_name") or "",
    }
    out = template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


async def send_status_notification(
    *,
    tenant_id: str,
    callback_url: str,
    phone: str,
    new_status: str,
    order_ctx: dict,
) -> bool:
    """
    Loads the tenant's template for `new_status`, renders it, and POSTs
    to the tenant's callback. Returns True on send, False if disabled or
    not configured.
    """
    cfg = await get_status_message(tenant_id, new_status)
    if not cfg or not cfg.get("enabled"):
        return False
    text = render_template(cfg["template"], order_ctx).strip()
    if not text:
        return False

    if not callback_url:
        log.warning("order_status.no_callback", tenant=tenant_id)
        return False

    session_id = order_ctx.get("session_key") or f"{tenant_id}:{phone}"
    payload = {
        "phone": phone,
        "session_id": session_id,
        "message": text,
        "tenant_id": tenant_id,
        "type": "order_status_update",
        "status": new_status,
        "order_id": order_ctx.get("order_id"),
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(callback_url, json=payload)
            resp.raise_for_status()
        log.info("order_status.sent", tenant=tenant_id, status=new_status, phone=phone)
        return True
    except Exception as exc:
        log.warning("order_status.send_failed", tenant=tenant_id, error=str(exc))
        return False
