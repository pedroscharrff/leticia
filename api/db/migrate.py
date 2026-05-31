"""
Auto-migration helper — runs pending SQL migrations at startup.
Safe to call multiple times (idempotent via _migrations tracking table).

IMPORTANTE: usa conexão DIRETA no Postgres (porta 5432), nunca o pool do
PgBouncer. DDL multi-statement (que é o típico de arquivos de migration)
NÃO funciona em transaction pooling — o bouncer mata a conexão no meio
da execução com "connection was closed in the middle of operation".

A marca em `_migrations` é inserida na MESMA transação do CREATE/ALTER pra
garantir atomicidade: se a migration falha no meio, nada é commitado e a
próxima tentativa vê o arquivo ainda como pendente.
"""
from pathlib import Path
import asyncpg
import structlog

from config import settings

log = structlog.get_logger()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def auto_migrate() -> None:
    # Conexão DIRETA (bypassa PgBouncer) — DDL/migrations não podem ir por
    # transaction pooling.
    db_url = settings.database_url_direct or settings.database_url
    if "pgbouncer" in db_url:
        log.warning("migrations.skipped_pgbouncer_only",
                    reason="DATABASE_URL_DIRECT não configurado — "
                           "rode scripts/run_migrations.py manualmente.")
        return

    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS public._migrations (
                filename   TEXT PRIMARY KEY,
                applied_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        applied = {
            r["filename"]
            for r in await conn.fetch("SELECT filename FROM public._migrations")
        }

        files = sorted(MIGRATIONS_DIR.glob("*.sql"))
        for f in files:
            if f.name in applied:
                continue
            log.info("migration.applying", file=f.name)
            sql = f.read_text(encoding="utf-8")
            # Atomicidade: arquivo + marca em uma transação só.
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO public._migrations (filename) VALUES ($1)",
                    f.name,
                )
            log.info("migration.applied", file=f.name)
    finally:
        await conn.close()
