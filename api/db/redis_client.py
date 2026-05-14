import redis.asyncio as aioredis
import structlog

from config import settings

log = structlog.get_logger()

_redis: aioredis.Redis | None = None


async def init_redis() -> None:
    global _redis
    _redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=5,
    )
    await _redis.ping()
    log.info("redis.connected")


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        log.info("redis.disconnected")


def get_redis() -> aioredis.Redis:
    assert _redis is not None, "Redis not initialized"
    return _redis
