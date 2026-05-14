"""
Z-API WhatsApp adapter (popular BR provider).
Credentials in tenant secrets: 'zapi_instance_id', 'zapi_token', 'zapi_client_token'
"""
from __future__ import annotations

import structlog
import httpx

from channels.base import ChannelAdapter, InboundMessage, OutboundMessage

log = structlog.get_logger()

_BASE = "https://api.z-api.io/instances"


class WhatsAppZAPIAdapter(ChannelAdapter):
    channel_type = "whatsapp_zapi"

    def verify_signature(self, body: bytes, headers: dict) -> bool:
        return True  # Z-API doesn't use HMAC; rely on API key + IP allowlist

    def parse_inbound(self, payload: dict) -> InboundMessage | None:
        try:
            if payload.get("type") != "ReceivedCallback":
                return None
            phone = payload["phone"]
            text = payload.get("text", {}).get("message", "")
            if not text:
                return None
            return InboundMessage(
                phone=phone,
                text=text,
                channel_type=self.channel_type,
                raw=payload,
            )
        except (KeyError, TypeError):
            return None

    async def send_outbound(self, msg: OutboundMessage, credentials: dict) -> None:
        instance_id = credentials.get("zapi_instance_id", "")
        token = credentials.get("zapi_token", "")
        client_token = credentials.get("zapi_client_token", "")
        if not instance_id or not token:
            log.warning("whatsapp_zapi.send.missing_credentials")
            return

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_BASE}/{instance_id}/token/{token}/send-text",
                headers={"Client-Token": client_token},
                json={"phone": msg.to, "message": msg.text},
            )
        if resp.status_code >= 400:
            log.error("whatsapp_zapi.send_failed", status=resp.status_code, body=resp.text)
