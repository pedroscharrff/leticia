"""
WhatsApp Cloud API adapter (Meta official API).
Credentials expected in tenant secrets:
  - 'wa_cloud_token'   — Bearer token (permanent access token)
  - 'wa_cloud_phone_id'— Phone number ID
  - 'wa_webhook_secret'— Hub verify token / HMAC secret
"""
from __future__ import annotations

import hashlib
import hmac
import structlog
import httpx

from channels.base import ChannelAdapter, InboundMessage, OutboundMessage

log = structlog.get_logger()

_GRAPH_URL = "https://graph.facebook.com/v19.0"


class WhatsAppCloudAdapter(ChannelAdapter):
    channel_type = "whatsapp_cloud"

    def __init__(self, webhook_secret: str = ""):
        self._secret = webhook_secret

    def verify_signature(self, body: bytes, headers: dict) -> bool:
        if not self._secret:
            return True  # no secret configured — allow (should be configured in prod)
        signature = headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(
            self._secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature, expected)

    def parse_inbound(self, payload: dict) -> InboundMessage | None:
        try:
            entry = payload["entry"][0]
            change = entry["changes"][0]["value"]
            msg = change["messages"][0]
            if msg.get("type") != "text":
                return None
            phone = msg["from"]
            text = msg["text"]["body"]
            return InboundMessage(
                phone=phone,
                text=text,
                channel_type=self.channel_type,
                raw=payload,
            )
        except (KeyError, IndexError):
            return None

    async def send_outbound(self, msg: OutboundMessage, credentials: dict) -> None:
        token = credentials.get("wa_cloud_token", "")
        phone_id = credentials.get("wa_cloud_phone_id", "")
        if not token or not phone_id:
            log.warning("whatsapp_cloud.send.missing_credentials")
            return

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{_GRAPH_URL}/{phone_id}/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "messaging_product": "whatsapp",
                    "to": msg.to,
                    "type": "text",
                    "text": {"body": msg.text},
                },
            )
        if resp.status_code >= 400:
            log.error("whatsapp_cloud.send_failed", status=resp.status_code, body=resp.text)
