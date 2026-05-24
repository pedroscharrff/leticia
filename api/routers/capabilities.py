"""
Capabilities (feature flags) — endpoints do portal e do admin.

Portal (tenant manager+):
  GET   /portal/capabilities              — lista catálogo com estado atual
  PATCH /portal/capabilities/{key}        — ativa/desativa + atualiza config

Admin (super admin):
  GET   /admin/tenants/{tenant_id}/capabilities
  PATCH /admin/tenants/{tenant_id}/capabilities/{key}
"""
from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from security import (
    require_admin,
    require_role,
    require_tenant_user,
    TenantUserContext,
)
from services import capabilities as cap_svc
from services.audit import log_event

log = structlog.get_logger()


# ── Schemas ──────────────────────────────────────────────────────────────────

class CapabilityUpdate(BaseModel):
    enabled: bool | None = None
    config:  dict[str, Any] | None = None


# ── Portal router ────────────────────────────────────────────────────────────

portal_router = APIRouter(prefix="/portal/capabilities", tags=["portal:capabilities"])


@portal_router.get("")
async def list_my_capabilities(
    user: Annotated[TenantUserContext, Depends(require_tenant_user)],
) -> dict:
    items = await cap_svc.list_for_tenant(user.tenant_id)
    return {"items": items}


@portal_router.patch("/{key}")
async def update_my_capability(
    key: str,
    payload: CapabilityUpdate,
    request: Request,
    # Mexer em capability é decisão de operação → manager mínimo (não viewer/operator)
    user: Annotated[TenantUserContext, Depends(require_role("manager"))],
) -> dict:
    if payload.enabled is None and payload.config is None:
        raise HTTPException(status_code=422, detail="Envie 'enabled' e/ou 'config'.")

    # Quando o caller manda só config (sem enabled), preservamos o estado atual.
    current_enabled = payload.enabled
    if current_enabled is None:
        items = await cap_svc.list_for_tenant(user.tenant_id)
        match = next((i for i in items if i["key"] == key), None)
        if not match:
            raise HTTPException(status_code=404, detail=f"Capacidade '{key}' não existe.")
        current_enabled = match["enabled"]

    updated = await cap_svc.set_enabled(
        tenant_id=user.tenant_id,
        key=key,
        enabled=current_enabled,
        config=payload.config,
        user_id=user.email,
    )

    await log_event(
        action="capability.toggle",
        actor_id=user.email,
        actor_type="user",
        tenant_id=user.tenant_id,
        target=key,
        meta={"enabled": current_enabled, "config": payload.config},
        request=request,
    )
    return updated


# ── Admin router ─────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/admin/tenants", tags=["admin:capabilities"])


@admin_router.get("/{tenant_id}/capabilities")
async def admin_list_capabilities(
    tenant_id: str,
    _admin: Annotated[str, Depends(require_admin)],
) -> dict:
    items = await cap_svc.list_for_tenant(tenant_id)
    return {"tenant_id": tenant_id, "items": items}


@admin_router.patch("/{tenant_id}/capabilities/{key}")
async def admin_update_capability(
    tenant_id: str,
    key: str,
    payload: CapabilityUpdate,
    request: Request,
    admin: Annotated[str, Depends(require_admin)],
) -> dict:
    if payload.enabled is None and payload.config is None:
        raise HTTPException(status_code=422, detail="Envie 'enabled' e/ou 'config'.")

    current_enabled = payload.enabled
    if current_enabled is None:
        items = await cap_svc.list_for_tenant(tenant_id)
        match = next((i for i in items if i["key"] == key), None)
        if not match:
            raise HTTPException(status_code=404, detail=f"Capacidade '{key}' não existe.")
        current_enabled = match["enabled"]

    updated = await cap_svc.set_enabled(
        tenant_id=tenant_id,
        key=key,
        enabled=current_enabled,
        config=payload.config,
        user_id=f"admin:{admin}",
    )

    await log_event(
        action="capability.admin_toggle",
        actor_id=admin,
        actor_type="admin",
        tenant_id=tenant_id,
        target=key,
        meta={"enabled": current_enabled, "config": payload.config},
        request=request,
    )
    return updated
