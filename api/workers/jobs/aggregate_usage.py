"""
Job: aggregate_llm_usage_daily

Roda no beat 1×/dia às 00:05 (após meia-noite, agrega o dia ANTERIOR
inteiro). Para cada tenant ativo, sumariza `conversation_logs` de ontem
agrupado por `llm_model`, calcula custo via `services.pricing` e persiste
em `public.llm_usage_daily` (idempotente via ON CONFLICT).

Por que rodar uma vez por dia (e não streaming): row count em
conversation_logs cresce O(turnos × dias); agregação batch evita milhares
de UPDATEs concorrentes no mesmo row diário. Reprocessar um dia é trivial
(ON CONFLICT atualiza).

Idempotência: chamar 2x no mesmo dia produz o mesmo resultado (substitui
a row pelo agregado mais recente do day).
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

import structlog

from db.postgres import get_db_conn, init_pool
from services.pricing import estimate_cost_usd

log = structlog.get_logger()

_SAFE_SCHEMA = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


async def _aggregate_one_tenant(conn, tenant_id: str, schema: str, target_day: date) -> dict:
    """Agrega 1 dia × tenant em rows agrupadas por llm_model."""
    if not _SAFE_SCHEMA.match(schema):
        return {"rows": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}

    # Filtra só rows assistant — são as que carregam tokens_in/out/llm_model
    # (user rows ficam com 0/0/NULL por design, ver agents/nodes/context.py).
    rows = await conn.fetch(
        f"""
        SELECT COALESCE(llm_model, 'unknown') AS llm_model,
               COALESCE(SUM(tokens_in), 0)   AS tokens_in,
               COALESCE(SUM(tokens_out), 0)  AS tokens_out,
               COUNT(*)                      AS msg_count
          FROM {schema}.conversation_logs
         WHERE role = 'assistant'
           AND created_at >= $1::date
           AND created_at <  ($1::date + INTERVAL '1 day')
           AND (tokens_in > 0 OR tokens_out > 0)
         GROUP BY 1
        """,
        target_day,
    )

    total = {"rows": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    for r in rows:
        model = r["llm_model"]
        tin = int(r["tokens_in"] or 0)
        tout = int(r["tokens_out"] or 0)
        msgs = int(r["msg_count"] or 0)
        cost = estimate_cost_usd(model, tin, tout)

        await conn.execute(
            """
            INSERT INTO public.llm_usage_daily
                (tenant_id, day, llm_model, tokens_in, tokens_out, msg_count, cost_usd, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, NOW())
            ON CONFLICT (tenant_id, day, llm_model) DO UPDATE
            SET tokens_in  = EXCLUDED.tokens_in,
                tokens_out = EXCLUDED.tokens_out,
                msg_count  = EXCLUDED.msg_count,
                cost_usd   = EXCLUDED.cost_usd,
                updated_at = NOW()
            """,
            tenant_id, target_day, model, tin, tout, msgs, cost,
        )
        total["rows"] += 1
        total["tokens_in"] += tin
        total["tokens_out"] += tout
        total["cost_usd"] += cost

    return total


async def _run_for_all_tenants(target_day: date | None = None) -> dict:
    # Default: agrega o dia de ONTEM (job roda 00:05, então UTC date é hoje;
    # subtrai 1 pra fechar o dia anterior).
    target_day = target_day or (date.today() - timedelta(days=1))

    await init_pool()

    async with get_db_conn() as conn:
        tenants = await conn.fetch(
            """
            SELECT id::text AS tenant_id, schema_name
              FROM public.tenants
             WHERE active = TRUE AND schema_name IS NOT NULL
            """
        )

        totals = {"day": target_day.isoformat(), "tenants": 0, "rows": 0,
                  "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "errors": 0}
        for t in tenants:
            totals["tenants"] += 1
            try:
                s = await _aggregate_one_tenant(conn, t["tenant_id"], t["schema_name"], target_day)
                totals["rows"] += s["rows"]
                totals["tokens_in"] += s["tokens_in"]
                totals["tokens_out"] += s["tokens_out"]
                totals["cost_usd"] += s["cost_usd"]
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "aggregate_usage.tenant_failed",
                    tenant=t["tenant_id"], schema=t["schema_name"], exc=str(exc),
                )
                totals["errors"] += 1

    log.info("aggregate_usage.done", **totals)
    return totals


def aggregate_llm_usage_daily_sync(target_day_iso: str | None = None) -> dict:
    """Entrypoint sync para Celery. `target_day_iso` opcional pra re-processar."""
    target = date.fromisoformat(target_day_iso) if target_day_iso else None
    return asyncio.run(_run_for_all_tenants(target))
