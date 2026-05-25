"""
Backfill: pré-popula bula_secoes para os medicamentos mais comuns do Brasil.

Resolve dois problemas vistos em produção:
  • 63% das chamadas de consultar_bula_secao retornavam vazias na 1ª vez
    porque a extração era on-demand (lenta)
  • p95 de latência ~22s no farmaceutico, dominado pelo cold path

Idempotente: pula medicamentos já indexados (bula_secoes não vazia).
Tolerante a falhas: erro em um termo não para o resto.

Uso:
    docker compose exec api python -m scripts.backfill_top_meds
    docker compose exec api python -m scripts.backfill_top_meds --limit 20
    docker compose exec api python -m scripts.backfill_top_meds --termo dipirona
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from db.postgres import init_pool, close_pool, get_db_conn  # noqa: E402
from services.anvisa_client import AnvisaClient  # noqa: E402
from services.bulario_repo import get_or_fetch, ensure_bulas_for_termo  # noqa: E402


# ── Lista curada — top medicamentos mais comuns no varejo BR ─────────────────
# Princípio ativo ou nome popular; o tool usa busca fuzzy, então qualquer
# variação que case via trigram serve. Cobre ~80% das perguntas reais.
TOP_MEDICAMENTOS: list[str] = [
    # Analgésicos / antitérmicos / AINEs
    "paracetamol", "dipirona", "ibuprofeno", "diclofenaco", "cetoprofeno",
    "nimesulida", "naproxeno", "meloxicam", "piroxicam", "celecoxibe",
    "aspirina", "acido acetilsalicilico",
    "tramadol", "codeina",
    # Gastro
    "omeprazol", "pantoprazol", "esomeprazol", "lansoprazol",
    "ranitidina", "famotidina",
    "bromoprida", "metoclopramida", "domperidona", "ondansetrona",
    "loperamida", "simeticona",
    "escopolamina", "hioscina",
    # Cardio / hipertensão
    "losartana", "valsartana",
    "enalapril", "captopril", "ramipril",
    "atenolol", "propranolol", "carvedilol", "bisoprolol", "metoprolol",
    "amlodipina", "nifedipina",
    "hidroclorotiazida", "furosemida", "espironolactona",
    "varfarina", "clopidogrel", "rivaroxabana",
    # Estatinas
    "sinvastatina", "atorvastatina", "rosuvastatina", "pravastatina",
    # Diabetes
    "metformina", "glibenclamida", "gliclazida", "glimepirida",
    "empagliflozina", "dapagliflozina",
    "insulina",
    # Tireoide / hormônios
    "levotiroxina",
    # Psiquiatria
    "fluoxetina", "sertralina", "escitalopram", "paroxetina", "citalopram",
    "venlafaxina", "duloxetina", "bupropiona", "trazodona",
    "clonazepam", "diazepam", "alprazolam", "bromazepam", "lorazepam",
    "zolpidem",
    "risperidona", "quetiapina", "olanzapina",
    # Neuro
    "carbamazepina", "lamotrigina", "topiramato", "gabapentina", "pregabalina",
    "fenitoina", "acido valproico",
    # Antibióticos
    "amoxicilina", "azitromicina", "ciprofloxacino", "cefalexina",
    "claritromicina", "sulfametoxazol", "metronidazol", "nitrofurantoina",
    "doxiciclina", "fosfomicina",
    # Antialérgicos
    "loratadina", "cetirizina", "desloratadina", "fexofenadina",
    "dexclorfeniramina", "ebastina",
    # Respiratório
    "salbutamol", "budesonida", "beclometasona", "formoterol", "fluticasona",
    "ambroxol", "acetilcisteina",
    # Corticoides
    "prednisona", "prednisolona", "dexametasona", "hidrocortisona",
    "betametasona",
    # Contraceptivos / hormônios femininos
    "levonorgestrel", "etinilestradiol", "drospirenona", "ciproterona",
    # Genito-urinário
    "sildenafila", "tadalafila", "tansulosina",
    # Outros frequentes
    "alopurinol", "colchicina",
    "vitamina d", "acido folico", "complexo b",
]


async def is_already_done(termo: str) -> bool:
    """True se já temos bula_secoes para algum medicamento que case o termo."""
    norm = " ".join(termo.lower().split())
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT count(*) AS c
              FROM public.medicamentos_anvisa m
              JOIN public.bula_secoes bs USING (num_processo)
             WHERE m.nome_produto_norm % $1
                OR m.principio_ativo ILIKE '%' || $1 || '%'
            """,
            norm,
        )
    return (row["c"] or 0) >= 1


async def process_one(termo: str, cli: AnvisaClient) -> tuple[str, int, float, str]:
    """
    Retorna (termo, n_bulas_extraidas, segundos, status).
    status: 'skipped' | 'fetched' | 'failed' | 'no_results'
    """
    t0 = time.perf_counter()
    try:
        if await is_already_done(termo):
            return termo, 0, time.perf_counter() - t0, "skipped"

        rows = await get_or_fetch(termo, limit=3, client=cli)
        if not rows:
            return termo, 0, time.perf_counter() - t0, "no_results"

        n = await ensure_bulas_for_termo(termo, top_n=3, client=cli)
        return termo, n, time.perf_counter() - t0, "fetched"
    except Exception as exc:  # noqa: BLE001
        return termo, 0, time.perf_counter() - t0, f"failed: {exc}"


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=None, help="processa só os primeiros N")
    ap.add_argument("--skip", type=int, default=0, help="pula os primeiros N (resume)")
    ap.add_argument("--termo", type=str, default=None, help="processa só esse termo")
    args = ap.parse_args(argv)

    if args.termo:
        targets = [args.termo]
    else:
        targets = TOP_MEDICAMENTOS[args.skip:]
        if args.limit:
            targets = targets[: args.limit]

    print(f"Backfill: {len(targets)} medicamento(s)\n")

    await init_pool()
    cli = AnvisaClient()
    stats = {"skipped": 0, "fetched": 0, "no_results": 0, "failed": 0}
    total_bulas = 0
    t_total = time.perf_counter()

    try:
        for i, termo in enumerate(targets, 1):
            termo_norm, n, dt, status = await process_one(termo, cli)
            short_status = status if not status.startswith("failed") else "failed"
            stats[short_status] = stats.get(short_status, 0) + 1
            total_bulas += n
            mark = {
                "skipped": "·",
                "fetched": "✓",
                "no_results": "∅",
                "failed": "✗",
            }.get(short_status, "?")
            extra = f" {status[8:]}" if status.startswith("failed") else ""
            print(f"  [{i:>3}/{len(targets)}] {mark} {termo:<30} "
                  f"bulas={n} dt={dt:5.1f}s{extra}")
    finally:
        await cli.close()
        await close_pool()

    dt_total = time.perf_counter() - t_total
    print(f"\nTotal: {dt_total:.0f}s — {total_bulas} bulas extraídas")
    print(
        f"Stats: skipped={stats.get('skipped', 0)} "
        f"fetched={stats.get('fetched', 0)} "
        f"no_results={stats.get('no_results', 0)} "
        f"failed={stats.get('failed', 0)}"
    )
    return 0 if stats.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
