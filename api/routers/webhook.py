"""
POST /webhook/{tenant_id}

Receives a message from an external WhatsApp gateway (WAHA, Uazapi, etc.),
validates the tenant's API key, and publishes the job to Celery.
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from dependencies import resolve_tenant
from models.tenant import TenantRow
from workers.celery_app import process_message

log = structlog.get_logger()

router = APIRouter(prefix="/webhook", tags=["webhook"])


class InboundMessage(BaseModel):
    phone: str
    message: str
    session_id: str | None = None  # caller may supply; generated if absent


@router.post("/{tenant_id}", status_code=status.HTTP_202_ACCEPTED)
async def receive_message(
    tenant_id: str,
    body: InboundMessage,
    tenant: TenantRow = Depends(resolve_tenant),
):
    if tenant_id != str(tenant.id):
        raise HTTPException(status_code=403, detail="tenant_id mismatch")

    session_id = body.session_id or f"{tenant_id}:{body.phone}"

    task = process_message.delay(
        tenant_id=str(tenant.id),
        schema_name=tenant.schema_name,
        callback_url=tenant.callback_url,
        phone=body.phone,
        session_id=session_id,
        current_message=body.message,
    )

    log.info(
        "webhook.received",
        tenant=tenant_id,
        phone=body.phone,
        task_id=task.id,
    )

    return {"accepted": True, "task_id": task.id}
