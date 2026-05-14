"""
Telegram Bot adapter.
Credentials in tenant secrets: 'telegram_bot_token'
"""
from __future__ import annotations

import hashlib
import hmac
import json
import structlog
import httpx

from channels.base import ChannelAdapter, InboundMessage, OutboundMessage

log = structlog.get_logger()


class TelegramAdapter(ChannelAdapter):
    channel_type = "telegram"

    def __init__(self, webhook_secret: str = ""):
        self._secret = webhook_secret

    def verify_signature(self, body: bytes, headers: dict) -> bool:
        if not self._secret:
            return True
        token = headers.get("x-telegram-bot-api-secret-token", "")
        return hmac.compare_digest(token, self._secret)

    def parse_inbound(self, payload: dict) -> InboundMessage | None:
        try:
            msg = payload["message"]
            text = msg.get("text", "")
            if not text:
                return None
            chat_id = str(msg["chat"]["id"])
            return InboundMessage(
                phone=chat_id,
                text=text,
                channel_type=self.channel_type,
                raw=payload,
            )
        except (KeyError, TypeError):
            return None

    async def send_outbound(self, msg: OutboundMessage, credentials: dict) -> None:
        token = credentials.get("telegram_bot_token", "")
        if not token:
            log.warning("telegram.send.missing_credentials")
            return

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": msg.to, "text": msg.text},
            )
        if resp.status_code >= 400:
            log.error("telegram.send_failed", status=resp.status_code, body=resp.text)
