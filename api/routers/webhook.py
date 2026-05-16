"""
POST /webhook/{webhook_token}

Receives a message from an external WhatsApp gateway (WAHA, Uazapi, etc.).
The webhook_token is the tenant's api_key embedded in the URL — this is the
standard pattern for gateway webhooks where a fixed URL must carry the auth.
"""
import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from db.postgres import get_db_conn
from models.tenant import TenantRow
from workers.celery_app import process_message

log = structlog.get_logger()

router = APIRouter(prefix="/webhook", tags=["webhook"])


class InboundMessage(BaseModel):
    phone: str
    message: str
    session_id: str | None = None  # caller may supply; generated if absent


async def _resolve_tenant_by_token(webhook_token: str) -> TenantRow:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenants WHERE api_key = $1 AND active = TRUE",
            webhook_token,
        )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook token",
        )
    return TenantRow(**dict(row))


@router.post("/{webhook_token}", status_code=status.HTTP_202_ACCEPTED)
async def receive_message(
    webhook_token: str,
    body: InboundMessage,
):
    tenant = await _resolve_tenant_by_token(webhook_token)

    session_id = body.session_id or f"{tenant.id}:{body.phone}"

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
        tenant=str(tenant.id),
        phone=body.phone,
        task_id=task.id,
    )

    return {"accepted": True, "task_id": task.id}
