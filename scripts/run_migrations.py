"""
Run all pending SQL migrations in order.
Usage: python scripts/run_migrations.py
"""
import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "api"))

import asyncpg
from config import settings

MIGRATIONS_DIR = Path(__file__).parent.parent / "api" / "db" / "migrations"


async def run():
    conn = await asyncpg.connect(settings.database_url)

    await conn.execute("""
        CREATE TABLE IF NOT EXISTS public._migrations (
            filename TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    applied = {r["filename"] for r in await conn.fetch("SELECT filename FROM public._migrations")}

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for f in files:
        if f.name in applied:
            print(f"  skip  {f.name}")
            continue
        print(f"  apply {f.name} ...", end=" ", flush=True)
        sql = f.read_text(encoding="utf-8")
        try:
            await conn.execute(sql)
            await conn.execute("INSERT INTO public._migrations (filename) VALUES ($1)", f.name)
            print("OK")
        except Exception as e:
            print(f"ERRO: {e}")
            await conn.close()
            sys.exit(1)

    await conn.close()
    print("Migrations concluídas.")


if __name__ == "__main__":
    asyncio.run(run())
