"""Channel adapter registry — maps channel_type string to adapter class."""
from channels.base import ChannelAdapter
from channels.whatsapp_cloud import WhatsAppCloudAdapter
from channels.whatsapp_zapi import WhatsAppZAPIAdapter
from channels.telegram import TelegramAdapter

CHANNEL_REGISTRY: dict[str, type[ChannelAdapter]] = {
    "whatsapp_cloud": WhatsAppCloudAdapter,
    "whatsapp_zapi": WhatsAppZAPIAdapter,
    "telegram": TelegramAdapter,
}


def get_adapter(channel_type: str, webhook_secret: str = "") -> ChannelAdapter:
    cls = CHANNEL_REGISTRY.get(channel_type)
    if not cls:
        raise ValueError(f"Unknown channel type: {channel_type}")
    try:
        return cls(webhook_secret=webhook_secret)
    except TypeError:
        return cls()
