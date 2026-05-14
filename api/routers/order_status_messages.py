"""
Per-tenant CRUD for order status notification templates.

Portal (manager+):
  GET  /portal/order-status-messages
  PUT  /portal/order-status-messages/{status}
  POST /portal/order-status-messages/{status}/preview
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from security import require_tenant_user, TenantUserContext
from services.audit import log_event
from services.order_status import (
    VALID_STATUSES,
    list_status_messages,
    upsert_status_message,
    render_template,
)

log = structlog.get_logger()
router = APIRouter(prefix="/portal/order-status-messages", tags=["portal-order-status-messages"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


class StatusMessage(BaseModel):
    status: str
    enabled: bool
    template: str


class StatusMessageUpdate(BaseModel):
    enabled: bool
    template: str


class PreviewIn(BaseModel):
    template: str | None = None       # if omitted, uses the saved one
    customer_name: str = "Maria"
    pharmacy_name: str | None = None


class PreviewOut(BaseModel):
    rendered: str


@router.get("", response_model=list[StatusMessage])
async def list_messages(user: TenantUser) -> list[StatusMessage]:
    rows = await list_status_messages(user.tenant_id)
    return [StatusMessage(**r) for r in rows]


@router.put("/{status}", response_model=StatusMessage)
async def update_message(status: str, body: StatusMessageUpdate, user: TenantUser) -> StatusMessage:
    user.assert_role("manager")
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status inválido: {status}")
    if not body.template.strip():
        raise HTTPException(status_code=422, detail="Template não pode ficar vazio")

    row = await upsert_status_message(
        user.tenant_id, status,
        enabled=body.enabled, template=body.template, actor_email=user.email,
    )
    await log_event(
        action="order_status_message.updated",
        actor_id=user.email, tenant_id=user.tenant_id, target=status,
        meta={"enabled": body.enabled},
    )
    return StatusMessage(status=status, **{k: row[k] for k in ("enabled", "template")})


@router.post("/{status}/preview", response_model=PreviewOut)
async def preview_message(status: str, body: PreviewIn, user: TenantUser) -> PreviewOut:
    if status not in VALID_STATUSES:
        raise HTTPException(status_code=422, detail=f"status inválido: {status}")

    template = body.template
    if template is None:
        rows = await list_status_messages(user.tenant_id)
        template = next((r["template"] for r in rows if r["status"] == status), "")

    sample_items = [
        {"qty": 1, "name": "Dipirona 500mg"},
        {"qty": 2, "name": "Vitamina C 1g"},
    ]
    rendered = render_template(template, {
        "order_id": "abc12345",
        "customer_name": body.customer_name,
        "total": 47.50,
        "items": sample_items,
        "pharmacy_name": body.pharmacy_name,
    })
    return PreviewOut(rendered=rendered)
