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
            mtype = msg.get("type")
            phone = msg["from"]

            if mtype == "text":
                return InboundMessage(
                    phone=phone,
                    text=msg["text"]["body"],
                    channel_type=self.channel_type,
                    raw=payload,
                )

            # Media: image | audio | voice | video | document
            # Meta sends only a media_id; we resolve to a short-lived URL later
            # (needs the tenant's access token, so it happens in the worker, not here).
            if mtype in ("image", "audio", "voice", "video", "document"):
                media = msg.get(mtype, {}) or {}
                caption = media.get("caption", "") or ""
                # Normalize "voice" → "audio" downstream
                norm_type = "audio" if mtype == "voice" else mtype
                return InboundMessage(
                    phone=phone,
                    text=caption,
                    channel_type=self.channel_type,
                    raw=payload,
                    media_type=norm_type,
                    media_mime=media.get("mime_type"),
                    media_id=media.get("id"),
                )

            return None
        except (KeyError, IndexError):
            return None

    @staticmethod
    async def resolve_media_url(media_id: str, token: str) -> tuple[str, str] | None:
        """
        Resolve a WhatsApp media_id into a short-lived download URL.
        Returns (url, mime_type) or None on failure.
        """
        if not media_id or not token:
            return None
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{_GRAPH_URL}/{media_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code >= 400:
            log.warning("whatsapp_cloud.media.resolve_failed",
                        media_id=media_id, status=resp.status_code)
            return None
        data = resp.json()
        return data.get("url", ""), data.get("mime_type", "")

    @staticmethod
    async def download_media(url: str, token: str) -> bytes | None:
        """Download bytes from a resolved WhatsApp media URL (token required)."""
        if not url or not token:
            return None
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            log.warning("whatsapp_cloud.media.download_failed", status=resp.status_code)
            return None
        return resp.content

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
