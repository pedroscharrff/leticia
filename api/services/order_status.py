"""
Per-tenant order-status notification templates.

When an operator changes an order's status in the panel, the API:
  1. loads the template for the new status,
  2. renders it with the order/customer context,
  3. sends through *every* tenant_channel that is active AND has the
     `config_json.notify_order_status` flag enabled — using the same
     ChannelAdapter pipeline that delivers agent replies and webhooks.
  4. If the tenant has no native channels configured (legacy setup),
     falls back to a POST on `tenants.callback_url` — same payload format
     used by workers/celery_app._deliver_response.
"""
from __future__ import annotations

import json

import httpx
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential

from channels.base import OutboundMessage
from channels.registry import CHANNEL_REGISTRY, get_adapter
from db.postgres import get_db_conn
from services import secrets as sec_svc

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


def _coerce_jsonb(raw) -> dict:
    """tenant_channels.config_json may come back as dict OR str depending on driver."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw) or {}
    except Exception:
        return {}


async def _eligible_channels(tenant_id: str) -> list[dict]:
    """Native channels (whatsapp_cloud / zapi / telegram) com notify_order_status ligado."""
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, channel_type, credentials_ref, webhook_secret, config_json
              FROM public.tenant_channels
             WHERE tenant_id = $1
               AND active = TRUE
             ORDER BY created_at
            """,
            tenant_id,
        )
    out: list[dict] = []
    for r in rows:
        cfg = _coerce_jsonb(r["config_json"])
        if not bool(cfg.get("notify_order_status")):
            continue
        out.append({
            "id": str(r["id"]),
            "channel_type": r["channel_type"],
            "credentials_ref": r["credentials_ref"],
            "webhook_secret": r["webhook_secret"] or "",
        })
    return out


async def _eligible_integrations(tenant_id: str) -> list[dict]:
    """
    Integrações broker prontas para enviar notificação de status.

    Critérios:
      - enabled = TRUE
      - config_json.notify_order_status = TRUE
      - provider = 'clickmassa' (do config_json ou do handoff_config)
      - base_url + token preenchidos (em config_json OU em handoff_config —
        a aba 'Transferir p/ atendente' já guarda esses campos, então
        reutilizamos quando o usuário não duplica)
    """
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, slug, name, direction, config_json, handoff_config
              FROM public.tenant_integrations
             WHERE tenant_id = $1
               AND enabled = TRUE
            """,
            tenant_id,
        )
    out: list[dict] = []
    for r in rows:
        cfg = _coerce_jsonb(r["config_json"])
        if not bool(cfg.get("notify_order_status")):
            continue

        handoff = _coerce_jsonb(r["handoff_config"])
        provider = (cfg.get("provider") or handoff.get("provider") or "clickmassa").lower()
        if provider != "clickmassa":
            continue

        base_url = cfg.get("base_url") or handoff.get("base_url")
        token    = cfg.get("token")    or handoff.get("token")
        if not base_url or not token:
            log.warning("order_status.integration_missing_creds",
                        tenant=tenant_id, integration_id=str(r["id"]))
            continue

        out.append({
            "id": str(r["id"]),
            "slug": r["slug"],
            "provider": provider,
            "config": {
                "base_url": base_url,
                "token": token,
                "external_key": cfg.get("external_key") or "123456",
            },
        })
    return out


async def _send_via_clickmassa_integration(
    *, tenant_id: str, integration: dict, phone: str, text: str,
) -> bool:
    from services.handoff import send_clickmassa_message
    phone_clean = "".join(c for c in (phone or "") if c.isdigit())
    cfg = integration["config"]
    res = await send_clickmassa_message(
        {"base_url": cfg.get("base_url"), "token": cfg.get("token")},
        phone=phone_clean, body=text,
        external_key=str(cfg.get("external_key") or "123456"),
    )
    if res.get("ok"):
        log.info("order_status.sent_via_clickmassa",
                 tenant=tenant_id, integration_id=integration["id"],
                 slug=integration["slug"])
        return True
    log.warning("order_status.clickmassa_send_failed",
                tenant=tenant_id, integration_id=integration["id"],
                error=res.get("error"), status=res.get("status_code"))
    return False


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
async def _post_callback(callback_url: str, payload: dict) -> None:
    """Same retry profile as workers/celery_app._deliver_response."""
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(callback_url, json=payload)
        resp.raise_for_status()


async def _send_via_adapter(
    *, tenant_id: str, channel: dict, phone: str, text: str,
) -> bool:
    creds_key = channel["credentials_ref"] or f"channel_creds_{channel['channel_type']}"
    raw = await sec_svc.get_secret(tenant_id, creds_key)
    if not raw:
        log.warning("order_status.no_credentials",
                    tenant=tenant_id, channel_id=channel["id"])
        return False
    try:
        credentials = json.loads(raw)
    except Exception:
        log.warning("order_status.bad_credentials_json",
                    tenant=tenant_id, channel_id=channel["id"])
        return False

    adapter = get_adapter(channel["channel_type"], webhook_secret=channel["webhook_secret"])
    try:
        await adapter.send_outbound(OutboundMessage(to=phone, text=text), credentials)
        log.info("order_status.sent_via_channel",
                 tenant=tenant_id, channel_id=channel["id"],
                 channel_type=channel["channel_type"])
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("order_status.channel_send_failed",
                    tenant=tenant_id, channel_id=channel["id"], error=str(exc))
        return False


async def send_status_notification(
    *,
    tenant_id: str,
    callback_url: str,
    phone: str,
    new_status: str,
    order_ctx: dict,
) -> bool:
    """
    Send the order-status notification to the customer.

    Routing precedence:
      1. Every active tenant_channel with `config_json.notify_order_status = true`
         delivers via its ChannelAdapter (same path as agent replies on that channel).
      2. If none match AND `callback_url` is set, POSTs there using the same
         payload shape as `_deliver_response` — keeps legacy gateway tenants working.

    Returns True if AT LEAST ONE delivery attempt succeeded.
    """
    cfg = await get_status_message(tenant_id, new_status)
    if not cfg or not cfg.get("enabled"):
        log.info("order_status.template_disabled", tenant=tenant_id, status=new_status)
        return False
    text = render_template(cfg["template"], order_ctx).strip()
    if not text:
        log.info("order_status.empty_render", tenant=tenant_id, status=new_status)
        return False

    if not phone:
        log.warning("order_status.no_phone", tenant=tenant_id)
        return False

    delivered = False

    # 1) Integrações do broker (ex.: ClickMassa) com notify_order_status ligado.
    integrations = await _eligible_integrations(tenant_id)
    for integ in integrations:
        if integ["provider"] == "clickmassa":
            ok = await _send_via_clickmassa_integration(
                tenant_id=tenant_id, integration=integ, phone=phone, text=text,
            )
            delivered = delivered or ok

    # 2) Canais nativos (whatsapp_cloud / zapi / telegram) com notify ligado.
    channels = await _eligible_channels(tenant_id)
    for ch in channels:
        if ch["channel_type"] in CHANNEL_REGISTRY:
            ok = await _send_via_adapter(
                tenant_id=tenant_id, channel=ch, phone=phone, text=text,
            )
            delivered = delivered or ok

    if delivered:
        return True

    # Legacy fallback: tenants without integrations/channels usam callback_url.
    if not callback_url:
        if not integrations and not channels:
            log.warning("order_status.no_eligible_target",
                        tenant=tenant_id, status=new_status)
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
        await _post_callback(callback_url, payload)
        log.info("order_status.sent_via_callback",
                 tenant=tenant_id, status=new_status)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("order_status.callback_send_failed",
                    tenant=tenant_id, error=str(exc))
        return False
