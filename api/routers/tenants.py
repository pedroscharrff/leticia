"""
Admin routes for tenant management — protected by JWT Bearer token.
"""
import re
import secrets
import structlog
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status

from db.postgres import get_db_conn
from models.tenant import TenantCreate, TenantResponse
from security import require_admin

log = structlog.get_logger()
router = APIRouter(prefix="/admin/tenants", tags=["admin"])

AdminUser = Annotated[str, Depends(require_admin)]


def _make_schema_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", name.lower())[:40]
    return f"tenant_{slug}"


@router.post("", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
async def create_tenant(body: TenantCreate, _admin: AdminUser) -> TenantResponse:
    api_key = secrets.token_urlsafe(32)
    schema_name = _make_schema_name(body.name)

    async with get_db_conn() as conn:
        existing = await conn.fetchrow(
            "SELECT id FROM public.tenants WHERE schema_name = $1", schema_name
        )
        if existing:
            raise HTTPException(status_code=409, detail="Tenant com esse nome já existe")

        row = await conn.fetchrow(
            """
            INSERT INTO public.tenants (name, api_key, callback_url, plan, schema_name)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING *
            """,
            body.name, api_key, body.callback_url, body.plan, schema_name,
        )
        await conn.execute("SELECT create_tenant_schema($1)", schema_name)

    log.info("tenant.created", tenant=str(row["id"]), schema=schema_name)
    return TenantResponse.from_row(row)


@router.get("", response_model=list[TenantResponse])
async def list_tenants(_admin: AdminUser) -> list[TenantResponse]:
    async with get_db_conn() as conn:
        rows = await conn.fetch("SELECT * FROM public.tenants ORDER BY created_at DESC")
    return [TenantResponse.from_row(r) for r in rows]


@router.get("/{tenant_id}", response_model=TenantResponse)
async def get_tenant(tenant_id: str, _admin: AdminUser) -> TenantResponse:
    async with get_db_conn() as conn:
        row = await conn.fetchrow("SELECT * FROM public.tenants WHERE id = $1", tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    return TenantResponse.from_row(row)


@router.patch("/{tenant_id}", response_model=TenantResponse)
async def update_tenant(tenant_id: str, body: TenantCreate, _admin: AdminUser) -> TenantResponse:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.tenants
            SET name = $2, callback_url = $3, plan = $4
            WHERE id = $1
            RETURNING *
            """,
            tenant_id, body.name, body.callback_url, body.plan,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    return TenantResponse.from_row(row)


@router.patch("/{tenant_id}/toggle", response_model=TenantResponse)
async def toggle_tenant(tenant_id: str, _admin: AdminUser) -> TenantResponse:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "UPDATE public.tenants SET active = NOT active WHERE id = $1 RETURNING *",
            tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")
    log.info("tenant.toggled", tenant=tenant_id, active=row["active"])
    return TenantResponse.from_row(row)


@router.delete("/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_tenant(tenant_id: str, _admin: AdminUser) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.tenants SET active = FALSE WHERE id = $1", tenant_id
        )
    log.info("tenant.deactivated", tenant=tenant_id)
