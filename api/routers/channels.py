"""
Channel management for tenant portal.
GET/POST/PATCH/DELETE tenant channels, manage credentials stored as secrets.

Inclui também a config de transferência ao balcão (handoff_config) por canal —
permite que cada canal nativo (loja A, loja B, WhatsApp principal vs secundário)
tenha sua própria fila de atendentes humanos.
"""
from __future__ import annotations

import json
import secrets
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_tenant_user, TenantUserContext
from services.audit import log_event
from services import secrets as sec_svc

log = structlog.get_logger()

router = APIRouter(prefix="/portal/channels", tags=["portal-channels"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]

SUPPORTED_CHANNELS = ["whatsapp_cloud", "whatsapp_zapi", "telegram", "instagram", "web_widget"]


# ── Schemas ──────────────────────────────────────────────────────────────────

class ChannelOut(BaseModel):
    id: str
    channel_type: str
    display_name: str | None
    active: bool
    config_json: dict
    handoff_config: dict
    handoff_pause_minutes: int = 240
    webhook_url: str  # constructed, not stored


class ChannelCreate(BaseModel):
    channel_type: str
    display_name: str | None = None
    credentials: dict  # will be stored encrypted, not returned
    config_json: dict = Field(default_factory=dict)
    handoff_config: dict = Field(default_factory=dict)


class ChannelUpdate(BaseModel):
    display_name: str | None = None
    credentials: dict | None = None
    active: bool | None = None
    config_json: dict | None = None
    handoff_config: dict | None = None
    handoff_pause_minutes: int | None = Field(default=None, ge=0, le=10080)


class HandoffTestIn(BaseModel):
    """Body para testar a transferência usando a config persistida do canal."""
    phone: str = Field(min_length=4, max_length=20)
    message: str | None = None


def _webhook_url(tenant_id: str, channel_type: str, channel_id: str) -> str:
    return f"/webhook/{channel_type}/{tenant_id}/{channel_id}"


def _row_to_out(tenant_id: str, r: Any) -> ChannelOut:
    return ChannelOut(
        id=str(r["id"]),
        channel_type=r["channel_type"],
        display_name=r["display_name"],
        active=r["active"],
        config_json=r["config_json"] or {},
        handoff_config=r["handoff_config"] or {},
        handoff_pause_minutes=r["handoff_pause_minutes"] or 240,
        webhook_url=_webhook_url(tenant_id, r["channel_type"], str(r["id"])),
    )


def _validate_handoff(cfg: dict | None) -> None:
    """Mesma validação usada em broker.py — se enabled=True, exige campos básicos."""
    if not cfg or not cfg.get("enabled"):
        return
    missing = [k for k in ("base_url", "token", "queue_id") if not cfg.get(k)]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Para ativar transferência ao atendente, preencha: {', '.join(missing)}",
        )


# ── CRUD ─────────────────────────────────────────────────────────────────────

class ChannelCapabilities(BaseModel):
    has_active_channel: bool
    provider:           str | None
    supports_image:     bool
    supports_audio:     bool


@router.get("/capabilities", response_model=ChannelCapabilities)
async def get_capabilities(user: TenantUser) -> ChannelCapabilities:
    """Retorna o que o canal ativo do tenant suporta em saída.

    Hoje deriva do `handoff_config.provider` do PRIMEIRO canal ativo. Usado
    pela UI de Ofertas para avisar se o canal aceita imagem/áudio antes do
    upload.
    """
    from services.outbound import get_active_channel_config
    from services import channel_media as cm

    cfg = await get_active_channel_config(user.tenant_id)
    if not cfg:
        return ChannelCapabilities(
            has_active_channel=False, provider=None,
            supports_image=False, supports_audio=False,
        )
    provider = (cfg.get("provider") or "clickmassa").lower()
    return ChannelCapabilities(
        has_active_channel=True,
        provider=provider,
        supports_image=cm.supports(provider, "image"),
        supports_audio=cm.supports(provider, "audio"),
    )


@router.get("", response_model=list[ChannelOut])
async def list_channels(user: TenantUser) -> list[ChannelOut]:
    try:
        async with get_db_conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM public.tenant_channels WHERE tenant_id = $1 ORDER BY created_at",
                user.tenant_id,
            )
    except Exception:
        return []  # tabela ainda não criada (migration pendente)
    return [_row_to_out(user.tenant_id, r) for r in rows]


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreate, user: TenantUser) -> ChannelOut:
    user.assert_role("manager")
    if body.channel_type not in SUPPORTED_CHANNELS:
        raise HTTPException(status_code=400, detail=f"Canal '{body.channel_type}' não suportado")

    _validate_handoff(body.handoff_config)

    # Validação de plano: features.channels indica quais canais o plano libera.
    features: dict = {}
    try:
        async with get_db_conn() as conn:
            plan_row = await conn.fetchrow(
                """
                SELECT p.features
                FROM public.tenants t
                JOIN public.plans p ON p.plan_name = t.plan
                WHERE t.id = $1
                """,
                user.tenant_id,
            )
        if plan_row and plan_row["features"]:
            f = plan_row["features"]
            features = f if isinstance(f, dict) else {}
    except Exception:
        pass
    allowed = features.get("channels") or SUPPORTED_CHANNELS
    if body.channel_type not in allowed:
        raise HTTPException(status_code=402, detail=f"Canal '{body.channel_type}' requer upgrade de plano")

    webhook_secret = secrets.token_urlsafe(32)
    creds_key = f"channel_creds_{body.channel_type}"

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_channels
                (tenant_id, channel_type, display_name, credentials_ref,
                 webhook_secret, config_json, handoff_config)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            user.tenant_id, body.channel_type, body.display_name,
            creds_key, webhook_secret,
            json.dumps(body.config_json),
            json.dumps(body.handoff_config),
        )

    await sec_svc.set_secret(user.tenant_id, creds_key, json.dumps(body.credentials))
    await log_event("channel.created", user.email, tenant_id=user.tenant_id, target=body.channel_type)
    return _row_to_out(user.tenant_id, row)


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(channel_id: str, body: ChannelUpdate, user: TenantUser) -> ChannelOut:
    user.assert_role("manager")

    if body.handoff_config is not None:
        _validate_handoff(body.handoff_config)

    async with get_db_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM public.tenant_channels WHERE id = $1 AND tenant_id = $2",
            channel_id, user.tenant_id,
        )
    if not existing:
        raise HTTPException(status_code=404, detail="Canal não encontrado")

    if body.credentials:
        creds_key = existing["credentials_ref"] or f"channel_creds_{existing['channel_type']}"
        await sec_svc.set_secret(user.tenant_id, creds_key, json.dumps(body.credentials))

    # Constrói SET parts dinâmicos
    set_parts: list[str] = []
    vals: list[Any] = [channel_id]
    idx = 2
    if body.display_name is not None:
        set_parts.append(f"display_name = ${idx}"); vals.append(body.display_name); idx += 1
    if body.active is not None:
        set_parts.append(f"active = ${idx}"); vals.append(body.active); idx += 1
    if body.config_json is not None:
        set_parts.append(f"config_json = ${idx}::jsonb"); vals.append(json.dumps(body.config_json)); idx += 1
    if body.handoff_config is not None:
        set_parts.append(f"handoff_config = ${idx}::jsonb"); vals.append(json.dumps(body.handoff_config)); idx += 1
    if body.handoff_pause_minutes is not None:
        set_parts.append(f"handoff_pause_minutes = ${idx}"); vals.append(body.handoff_pause_minutes); idx += 1
    set_parts.append("updated_at = NOW()")

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.tenant_channels SET {', '.join(set_parts)} "
            "WHERE id = $1 RETURNING *",
            *vals,
        )

    await log_event("channel.updated", user.email, tenant_id=user.tenant_id, target=channel_id)
    return _row_to_out(user.tenant_id, row)


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_channel(channel_id: str, user: TenantUser) -> None:
    user.assert_role("owner")
    async with get_db_conn() as conn:
        await conn.execute(
            "DELETE FROM public.tenant_channels WHERE id = $1 AND tenant_id = $2",
            channel_id, user.tenant_id,
        )
    await log_event("channel.deleted", user.email, tenant_id=user.tenant_id, target=channel_id)


# ── Test handoff ─────────────────────────────────────────────────────────────

@router.post("/{channel_id}/handoff/test")
async def test_channel_handoff(channel_id: str, body: HandoffTestIn, user: TenantUser):
    """
    Dispara uma transferência de teste usando o handoff_config persistido do canal.
    Útil para validar token / queue_id antes de jogar em produção.
    """
    user.assert_role("manager")

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT handoff_config FROM public.tenant_channels "
            "WHERE id = $1 AND tenant_id = $2",
            channel_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(404, "Canal não encontrado")

    cfg = row["handoff_config"] or {}
    if isinstance(cfg, str):
        try: cfg = json.loads(cfg)
        except Exception: cfg = {}
    if not cfg.get("enabled"):
        raise HTTPException(400, "Transferência está desativada neste canal. Ative e salve antes de testar.")

    from services.handoff import transfer_to_human
    phone_clean = "".join(c for c in body.phone if c.isdigit())
    result = await transfer_to_human(cfg, phone=phone_clean, custom_message=body.message)
    await log_event("channel.handoff.tested", actor_id=user.email,
                    tenant_id=user.tenant_id, target=channel_id,
                    meta={"ok": result.get("ok"), "status_code": result.get("status_code")})
    return result
