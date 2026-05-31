import asyncio
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
    """Cria o pool de conexões com o Postgres (via PgBouncer em transaction mode).

    Idempotente *dentro do mesmo event loop*. Em Celery prefork, cada task
    chama `asyncio.run(...)` — que cria um loop novo e FECHA no final. O pool
    asyncpg fica vinculado ao loop em que foi criado: tentar reusar num loop
    diferente quebra com `RuntimeError: Event loop is closed`. Por isso, se
    detectarmos que o `_pool` pertence a um loop morto/diferente, descartamos
    a referência (a conexão TCP é limpa pelo PgBouncer no idle timeout — sem
    vazamento prático com pool min_size=1).

    `statement_cache_size=0` é OBRIGATÓRIO em transaction pooling: o PgBouncer
    rotaciona conexões de servidor a cada transação, então prepared statements
    cacheados por asyncpg explodem com "prepared statement ... does not exist".

    Pool pequeno por processo (1-3) porque o PgBouncer absorve a multiplexação.
    """
    global _pool
    cur_loop = asyncio.get_running_loop()

    if _pool is not None:
        pool_loop = getattr(_pool, "_loop", None)
        if pool_loop is cur_loop and not _pool._closed:  # type: ignore[attr-defined]
            return  # reuso dentro do MESMO loop — caminho do uvicorn/FastAPI
        # Loop diferente (Celery asyncio.run) ou fechado → descarta sem await
        # (não dá pra fechar cleanly de outro loop; PgBouncer cuida do GC).
        log.info("postgres.pool.discarded_stale",
                 reason="loop_mismatch_or_closed")
        _pool = None

    _pool = await asyncpg.create_pool(
        settings.database_url,
        min_size=1,
        max_size=3,
        command_timeout=30,
        statement_cache_size=0,        # ← obrigatório com PgBouncer transaction
        init=_init_connection,
    )
    log.info("postgres.pool.created", min_size=1, max_size=3)


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
