"""
ChannelAdapter interface — all channel adapters implement this protocol.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class InboundMessage:
    phone: str          # sender's phone / user identifier
    text: str           # message body (caption for media, or transcript placeholder)
    channel_type: str   # e.g. 'whatsapp_cloud'
    raw: dict           # original payload for debugging
    # Media payload (None for plain text). Either media_url OR media_bytes is set.
    media_type: str | None = None   # 'image' | 'audio' | 'voice' | 'video' | 'document'
    media_mime: str | None = None   # e.g. 'audio/ogg; codecs=opus', 'image/jpeg'
    media_url: str | None = None    # direct URL (Z-API) or fetched short-lived URL (WA Cloud)
    media_id: str | None = None     # provider-side id (WA Cloud media_id)


@dataclass
class OutboundMessage:
    to: str             # recipient phone / user identifier
    text: str


class ChannelAdapter(ABC):
    channel_type: str = ""

    @abstractmethod
    def verify_signature(self, body: bytes, headers: dict) -> bool:
        """Return True if the webhook signature is valid."""
        ...

    @abstractmethod
    def parse_inbound(self, payload: dict) -> InboundMessage | None:
        """Parse raw webhook payload. Return None if not an inbound text message."""
        ...

    @abstractmethod
    async def send_outbound(self, msg: OutboundMessage, credentials: dict) -> None:
        """Send a reply message via the channel's API."""
        ...
