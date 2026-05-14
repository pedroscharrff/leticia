from contextlib import asynccontextmanager
from typing import AsyncIterator
import json

import asyncpg
import structlog

from config import settings

log = structlog.get_logger()

_pool: asyncpg.Pool | None = None


def _json_encoder(value: dict) -> str:
    return json.dumps(value)


def _json_decoder(value: str) -> dict:
    return json.loads(value)


async def _init_connection(conn: asyncpg.Connection) -> None:
    """Register JSON/JSONB codecs so asyncpg returns dicts, not strings."""
    await conn.set_type_codec("jsonb", encoder=_json_encoder, decoder=_json_decoder, schema="pg_catalog")
    await conn.set_type_codec("json",  encoder=_json_encoder, decoder=_json_decoder, schema="pg_catalog")


async def init_pool() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=5,
        max_size=20,
        command_timeout=30,
        init=_init_connection,
    )
    log.info("postgres.pool.created")


async def close_pool() -> None:
    global _pool
    if _pool:
        await _pool.close()
        log.info("postgres.pool.closed")


@asynccontextmanager
async def get_db_conn() -> AsyncIterator[asyncpg.Connection]:
    assert _pool is not None, "DB pool not initialized"
    async with _pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def tenant_conn(schema_name: str) -> AsyncIterator[asyncpg.Connection]:
    """Acquire a connection scoped to a tenant schema."""
    assert _pool is not None, "DB pool not initialized"
    async with _pool.acquire() as conn:
        await conn.execute(f"SET search_path = {schema_name}, public")
        yield conn
