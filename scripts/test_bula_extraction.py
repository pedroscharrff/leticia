"""
Teste end-to-end da extração + busca FTS de bulas:

  1. get_or_fetch('paracetamol') — força cold path, baixa PDFs, extrai
  2. has_bula em cada num_processo
  3. search_bula com pergunta clínica — confirma FTS retorna trecho relevante

Uso (dentro de saas-farmacia/):
    docker compose exec api python -m scripts.test_bula_extraction
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from db.postgres import init_pool, close_pool  # noqa: E402
from services.bulario_repo import get_or_fetch  # noqa: E402
from services.bula_repo import has_bula, search_bula  # noqa: E402


async def main() -> int:
    await init_pool()
    try:
        termo = "paracetamol"
        print(f"\n[1] get_or_fetch('{termo}') — cold path, espera extração de PDF")
        rows = await get_or_fetch(termo, limit=5)
        print(f"    {len(rows)} medicamentos retornados")
        for r in rows[:5]:
            np = r["num_processo"]
            hb = await has_bula(np)
            mark = "✓" if hb else "✗"
            print(f"    [{mark}] {r['nome_produto']} (proc {np})")

        # Queries clínicas reais
        queries = [
            ("paracetamol", "dose maxima adulto"),
            ("paracetamol", "gravidez"),
            ("paracetamol", "alcool"),
            ("paracetamol", "criança peso"),
        ]
        for med, perg in queries:
            print(f"\n[2] search_bula('{med}', '{perg}')")
            results = await search_bula(med, perg, limit=2)
            if not results:
                print("    (nenhum trecho retornado)")
                continue
            for r in results:
                trecho = r["trecho"].replace("\n", " ")[:300]
                print(f"    secao={r['secao']:18} rank={r['rank']:.3f}  {r['nome_produto']}")
                print(f"      {trecho}")

        print("\n=== Done ===")
        return 0
    finally:
        await close_pool()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
