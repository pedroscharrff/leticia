"""
Channel management for tenant portal.
GET/POST/PATCH/DELETE tenant channels, manage credentials stored as secrets.
"""
from __future__ import annotations

import secrets
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from db.postgres import get_db_conn
from security import require_tenant_user, TenantUserContext
from services.audit import log_event
from services import secrets as sec_svc

log = structlog.get_logger()

router = APIRouter(prefix="/portal/channels", tags=["portal-channels"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]

SUPPORTED_CHANNELS = ["whatsapp_cloud", "whatsapp_zapi", "telegram", "instagram", "web_widget"]


class ChannelOut(BaseModel):
    id: str
    channel_type: str
    display_name: str | None
    active: bool
    config_json: dict
    webhook_url: str  # constructed, not stored


class ChannelCreate(BaseModel):
    channel_type: str
    display_name: str | None = None
    credentials: dict  # will be stored encrypted, not returned
    config_json: dict = {}


class ChannelUpdate(BaseModel):
    display_name: str | None = None
    credentials: dict | None = None
    active: bool | None = None
    config_json: dict | None = None


def _webhook_url(tenant_id: str, channel_type: str, channel_id: str) -> str:
    return f"/webhook/{channel_type}/{tenant_id}/{channel_id}"


@router.get("", response_model=list[ChannelOut])
async def list_channels(user: TenantUser) -> list[ChannelOut]:
    try:
        async with get_db_conn() as conn:
            rows = await conn.fetch(
                "SELECT * FROM public.tenant_channels WHERE tenant_id = $1 ORDER BY created_at",
                user.tenant_id,
            )
    except Exception:
        return []  # table not yet created (migration pending)
    return [
        ChannelOut(
            id=str(r["id"]),
            channel_type=r["channel_type"],
            display_name=r["display_name"],
            active=r["active"],
            config_json=r["config_json"] or {},
            webhook_url=_webhook_url(user.tenant_id, r["channel_type"], str(r["id"])),
        )
        for r in rows
    ]


@router.post("", response_model=ChannelOut, status_code=status.HTTP_201_CREATED)
async def create_channel(body: ChannelCreate, user: TenantUser) -> ChannelOut:
    user.assert_role("manager")
    if body.channel_type not in SUPPORTED_CHANNELS:
        raise HTTPException(status_code=400, detail=f"Canal '{body.channel_type}' não suportado")

    # Check plan allows this channel type (features column added in migration 003)
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
        pass  # migration 003 not yet applied — allow all channels
    allowed = features.get("channels") or SUPPORTED_CHANNELS
    if body.channel_type not in allowed:
        raise HTTPException(status_code=402, detail=f"Canal '{body.channel_type}' requer upgrade de plano")

    webhook_secret = secrets.token_urlsafe(32)
    creds_key = f"channel_creds_{body.channel_type}"

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_channels
                (tenant_id, channel_type, display_name, credentials_ref, webhook_secret, config_json)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            user.tenant_id, body.channel_type, body.display_name,
            creds_key, webhook_secret, body.config_json,
        )

    # Store credentials encrypted
    import json
    await sec_svc.set_secret(user.tenant_id, creds_key, json.dumps(body.credentials))

    await log_event("channel.created", user.email, tenant_id=user.tenant_id, target=body.channel_type)

    return ChannelOut(
        id=str(row["id"]),
        channel_type=row["channel_type"],
        display_name=row["display_name"],
        active=row["active"],
        config_json=row["config_json"] or {},
        webhook_url=_webhook_url(user.tenant_id, row["channel_type"], str(row["id"])),
    )


@router.patch("/{channel_id}", response_model=ChannelOut)
async def update_channel(channel_id: str, body: ChannelUpdate, user: TenantUser) -> ChannelOut:
    user.assert_role("manager")

    async with get_db_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT * FROM public.tenant_channels WHERE id = $1 AND tenant_id = $2",
            channel_id, user.tenant_id,
        )
    if not existing:
        raise HTTPException(status_code=404, detail="Canal não encontrado")

    if body.credentials:
        import json
        creds_key = existing["credentials_ref"] or f"channel_creds_{existing['channel_type']}"
        await sec_svc.set_secret(user.tenant_id, creds_key, json.dumps(body.credentials))

    updates: dict = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.active is not None:
        updates["active"] = body.active
    if body.config_json is not None:
        updates["config_json"] = body.config_json
    updates["updated_at"] = "NOW()"

    if updates:
        raw_updates = {k: v for k, v in updates.items() if v != "NOW()"}
        set_parts = []
        vals = [channel_id]
        for i, (k, v) in enumerate(raw_updates.items(), start=2):
            set_parts.append(f"{k} = ${i}")
            vals.append(v)
        set_parts.append("updated_at = NOW()")
        async with get_db_conn() as conn:
            row = await conn.fetchrow(
                f"UPDATE public.tenant_channels SET {', '.join(set_parts)} WHERE id = $1 RETURNING *",
                *vals,
            )
    else:
        row = existing

    await log_event("channel.updated", user.email, tenant_id=user.tenant_id, target=channel_id)
    return ChannelOut(
        id=str(row["id"]),
        channel_type=row["channel_type"],
        display_name=row["display_name"],
        active=row["active"],
        config_json=row["config_json"] or {},
        webhook_url=_webhook_url(user.tenant_id, row["channel_type"], str(row["id"])),
    )


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_channel(channel_id: str, user: TenantUser) -> None:
    user.assert_role("owner")
    async with get_db_conn() as conn:
        await conn.execute(
            "DELETE FROM public.tenant_channels WHERE id = $1 AND tenant_id = $2",
            channel_id, user.tenant_id,
        )
    await log_event("channel.deleted", user.email, tenant_id=user.tenant_id, target=channel_id)
