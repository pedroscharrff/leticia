"""
Auto-migration helper — runs pending SQL migrations at startup.
Safe to call multiple times (idempotent via _migrations tracking table).
"""
from pathlib import Path
import structlog

from db.postgres import get_db_conn

log = structlog.get_logger()

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def auto_migrate() -> None:
    async with get_db_conn() as conn:
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
            await conn.execute(sql)
            await conn.execute(
                "INSERT INTO public._migrations (filename) VALUES ($1)", f.name
            )
            log.info("migration.applied", file=f.name)
