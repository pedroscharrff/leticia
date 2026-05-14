"""
Per-tenant sales configuration: which customer fields the vendedor agent
must collect before closing an order, retry policy and fallback message.

Portal (tenant manager+):
  GET  /portal/sales-config
  PUT  /portal/sales-config

Admin (super admin only):
  GET  /admin/tenants/{id}/sales-config
  PUT  /admin/tenants/{id}/sales-config
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_admin, require_tenant_user, TenantUserContext
from services.audit import log_event
from services.sales_config import (
    ALLOWED_FIELDS,
    DEFAULT_FALLBACK,
    SALES_CONFIG_DEFAULTS,
    load_sales_config,
)

log = structlog.get_logger()


# ── Schemas ──────────────────────────────────────────────────────────────────

class FieldOption(BaseModel):
    key: str
    label: str


class SalesConfigOut(BaseModel):
    required_fields: list[str]
    max_attempts: int
    fallback_message: str
    available_fields: list[FieldOption]


class SalesConfigUpdate(BaseModel):
    required_fields: list[str] | None = Field(default=None)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    fallback_message: str | None = Field(default=None, max_length=2000)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _validate_required(fields: list[str]) -> list[str]:
    bad = [f for f in fields if f not in ALLOWED_FIELDS]
    if bad:
        raise HTTPException(
            status_code=422,
            detail=f"Campos inválidos: {bad}. Permitidos: {sorted(ALLOWED_FIELDS)}",
        )
    # Dedupe + preserve order
    seen: set[str] = set()
    out: list[str] = []
    for f in fields:
        if f not in seen:
            out.append(f); seen.add(f)
    return out


async def _to_out(tenant_id: str) -> SalesConfigOut:
    cfg = await load_sales_config(tenant_id)
    return SalesConfigOut(
        required_fields=cfg["required_fields"],
        max_attempts=cfg["max_attempts"],
        fallback_message=cfg["fallback_message"],
        available_fields=[
            FieldOption(key=k, label=v["label"]) for k, v in ALLOWED_FIELDS.items()
        ],
    )


async def _update(tenant_id: str, body: SalesConfigUpdate, actor_email: str) -> SalesConfigOut:
    updates: dict = {}
    if body.required_fields is not None:
        updates["required_fields"] = _validate_required(body.required_fields)
    if body.max_attempts is not None:
        updates["max_attempts"] = body.max_attempts
    if body.fallback_message is not None:
        msg = body.fallback_message.strip()
        updates["fallback_message"] = msg or DEFAULT_FALLBACK

    if not updates:
        return await _to_out(tenant_id)

    cols = list(updates.keys())
    set_clauses = ", ".join(f"{c} = ${i+2}" for i, c in enumerate(cols))
    insert_cols = ", ".join(cols)
    placeholders = ", ".join(f"${i+2}" for i in range(len(cols)))

    async with get_db_conn() as conn:
        await conn.execute(
            f"""
            INSERT INTO public.tenant_sales_config (tenant_id, {insert_cols})
            VALUES ($1, {placeholders})
            ON CONFLICT (tenant_id) DO UPDATE
                SET {set_clauses}, updated_at = NOW()
            """,
            tenant_id, *updates.values(),
        )

    await log_event(
        action="sales_config.updated",
        actor_id=actor_email,
        tenant_id=tenant_id,
        target="sales_config",
        meta={"changed_fields": cols},
    )
    return await _to_out(tenant_id)


# ── Portal router ────────────────────────────────────────────────────────────

portal_router = APIRouter(prefix="/portal", tags=["portal-sales-config"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


@portal_router.get("/sales-config", response_model=SalesConfigOut)
async def portal_get_sales_config(user: TenantUser) -> SalesConfigOut:
    return await _to_out(user.tenant_id)


@portal_router.put("/sales-config", response_model=SalesConfigOut)
async def portal_update_sales_config(
    body: SalesConfigUpdate, user: TenantUser,
) -> SalesConfigOut:
    user.assert_role("manager")
    return await _update(user.tenant_id, body, user.email)


# ── Admin router ─────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/admin/tenants", tags=["admin-sales-config"])
AdminUser = Annotated[str, Depends(require_admin)]


@admin_router.get("/{tenant_id}/sales-config", response_model=SalesConfigOut)
async def admin_get_sales_config(tenant_id: str, _admin: AdminUser) -> SalesConfigOut:
    return await _to_out(tenant_id)


@admin_router.put("/{tenant_id}/sales-config", response_model=SalesConfigOut)
async def admin_update_sales_config(
    tenant_id: str, body: SalesConfigUpdate, admin: AdminUser,
) -> SalesConfigOut:
    return await _update(tenant_id, body, f"admin:{admin}")
