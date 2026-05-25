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
    message: str = ""
    session_id: str | None = None  # caller may supply; generated if absent
    # Optional media payload (sender included image/audio etc.)
    media_type: str | None = None    # 'image' | 'audio' | 'video' | 'document'
    media_mime: str | None = None
    media_url: str | None = None     # direct URL (public)
    media_b64: str | None = None     # base64 bytes (when caller already downloaded)


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

    # Bypass total se a IA estiver pausada ou o atendimento encerrado.
    # O atendente humano assumiu — não competimos com ele.
    try:
        from services.conversation_state import is_ai_paused
        paused, reason = await is_ai_paused(str(tenant.id), body.phone)
        if paused:
            log.info(
                "webhook.skipped.ai_paused",
                tenant=str(tenant.id),
                phone=body.phone[:4],
                reason=reason,
            )
            return {"accepted": False, "reason": f"ai_paused:{reason}"}
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook.paused_check_failed", exc=str(exc))

    task = process_message.delay(
        tenant_id=str(tenant.id),
        schema_name=tenant.schema_name,
        callback_url=tenant.callback_url,
        phone=body.phone,
        session_id=session_id,
        current_message=body.message,
        media={
            "media_type": body.media_type,
            "media_mime": body.media_mime,
            "media_url":  body.media_url,
            "media_b64":  body.media_b64,
        } if body.media_type else None,
    )

    log.info(
        "webhook.received",
        tenant=str(tenant.id),
        phone=body.phone,
        task_id=task.id,
    )

    return {"accepted": True, "task_id": task.id}
