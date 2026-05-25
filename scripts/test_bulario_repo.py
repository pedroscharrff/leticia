"""
Teste end-to-end do repo bulario:

  1ª busca (cold)  → bate na ANVISA, faz upsert, busca top-3 details
  2ª busca (warm)  → resolve do cache local
  3ª busca (sinônimo trigram) → confirma fuzzy match no DB

Requer DATABASE_URL configurado (.env) e migrations rodadas.

Uso (dentro de saas-farmacia/):
    python -m scripts.test_bulario_repo
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from db.postgres import init_pool, close_pool  # noqa: E402
from services.bulario_repo import get_or_fetch, search_local  # noqa: E402


def _print_rows(rows: list[dict]) -> None:
    for r in rows[:5]:
        np = r.get("num_processo")
        nome = r.get("nome_produto")
        pa = r.get("principio_ativo") or "—"
        has_det = "✓" if r.get("has_detail") else "✗"
        classes = r.get("classes_terapeuticas") or []
        print(f"    [det:{has_det}] {nome} | pa: {pa} | classes: {classes[:2]} | proc: {np}")


async def main() -> int:
    await init_pool()
    try:
        termo = "dipirona"

        print(f"\n[1] get_or_fetch('{termo}') — cold (esperado: bate ANVISA)")
        t = time.perf_counter()
        rows1 = await get_or_fetch(termo, limit=5)
        dt1 = (time.perf_counter() - t) * 1000
        print(f"    {len(rows1)} resultados em {dt1:.0f}ms")
        _print_rows(rows1)

        print(f"\n[2] get_or_fetch('{termo}') — warm (esperado: cache hit, sub-100ms)")
        t = time.perf_counter()
        rows2 = await get_or_fetch(termo, limit=5)
        dt2 = (time.perf_counter() - t) * 1000
        print(f"    {len(rows2)} resultados em {dt2:.0f}ms")

        print("\n[3] search_local('dipirona monoidratada') — trigram local")
        t = time.perf_counter()
        rows3 = await search_local("dipirona monoidratada", limit=5)
        dt3 = (time.perf_counter() - t) * 1000
        print(f"    {len(rows3)} resultados em {dt3:.0f}ms")
        _print_rows(rows3)

        # Validações simples
        ok = True
        if not rows1:
            print("  FAIL: cold fetch retornou vazio")
            ok = False
        if rows1 and not any(r.get("has_detail") for r in rows1[:3]):
            print("  WARN: nenhum dos top-3 tem detail enriquecido")
        if dt2 >= dt1:
            print(f"  WARN: warm ({dt2:.0f}ms) não foi mais rápido que cold ({dt1:.0f}ms)")
        if not rows3:
            print("  WARN: trigram local não retornou — verifique se pg_trgm está habilitado")

        print("\n" + ("=== OK ===" if ok else "=== FAIL ==="))
        return 0 if ok else 1
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
