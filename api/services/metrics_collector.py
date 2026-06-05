"""
Métricas de negócio para Prometheus — refresh em background a partir do Postgres.

Modelo:
  • Gauges/Counters globais (prometheus_client default registry).
  • Uma asyncio.Task de fundo chama `refresh_business_metrics()` em loop fixo
    (default 30s). Roda no MESMO event loop do FastAPI (necessário porque o
    pool asyncpg está vinculado a esse loop — chamadas de outros loops falham
    com `Event loop is closed`).
  • Scrape do Prometheus lê o snapshot mais recente direto dos Gauges — barato
    e nunca bloqueia o request.

Decisão deliberada: NÃO usar custom Collector (Collector.collect chama síncrono;
disparar asyncpg em outro loop quebra). Refresh em background é mais simples e
desacopla a latência do scrape do estado do DB.

Métricas:
  saas_tenants_total{active}
  saas_tenant_tickets_open{tenant_id,tenant_name,plan}
  saas_tenant_tickets_paused{tenant_id,tenant_name,plan}
  saas_tenant_messages_24h{tenant_id,tenant_name,role}
  saas_tenant_messages_total{tenant_id,tenant_name,role}       (snapshot, não delta)
  saas_tenant_carts_open{tenant_id,tenant_name}
  saas_tenant_orders_total{tenant_id,tenant_name,status}       (snapshot)
  saas_tenant_orders_24h{tenant_id,tenant_name,status}
  saas_tenant_agent_errors_24h{tenant_id,tenant_name}
  saas_tenant_tokens_24h{tenant_id,tenant_name,direction}
  saas_business_metrics_refresh_seconds                        (histograma do próprio refresh)
  saas_business_metrics_refresh_errors_total                   (counter de falhas)
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import structlog
from prometheus_client import Counter, Gauge, Histogram

from db.postgres import get_db_conn

log = structlog.get_logger()

REFRESH_INTERVAL_SECONDS = 30.0

_SAFE_SCHEMA = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")

# ── Métricas (registradas globalmente no default registry) ────────────────────

TENANTS_TOTAL = Gauge(
    "saas_tenants_total",
    "Total de tenants cadastrados (rotulado por active)",
    ["active"],
)
TICKETS_OPEN = Gauge(
    "saas_tenant_tickets_open",
    "Tickets/conversas abertas (conversation_state.closed_at IS NULL)",
    ["tenant_id", "tenant_name", "plan"],
)
TICKETS_PAUSED = Gauge(
    "saas_tenant_tickets_paused",
    "Tickets com IA pausada (atendente humano ativo)",
    ["tenant_id", "tenant_name", "plan"],
)
MESSAGES_TOTAL = Gauge(
    "saas_tenant_messages_total",
    "Snapshot do total de mensagens em conversation_logs (all-time)",
    ["tenant_id", "tenant_name", "role"],
)
MESSAGES_24H = Gauge(
    "saas_tenant_messages_24h",
    "Mensagens nas últimas 24h por role",
    ["tenant_id", "tenant_name", "role"],
)
CARTS_OPEN = Gauge(
    "saas_tenant_carts_open",
    "Carrinhos abertos (items > 0)",
    ["tenant_id", "tenant_name"],
)
ORDERS_TOTAL = Gauge(
    "saas_tenant_orders_total",
    "Snapshot de pedidos por status (all-time)",
    ["tenant_id", "tenant_name", "status"],
)
ORDERS_24H = Gauge(
    "saas_tenant_orders_24h",
    "Pedidos criados nas últimas 24h por status",
    ["tenant_id", "tenant_name", "status"],
)
AGENT_ERRORS_24H = Gauge(
    "saas_tenant_agent_errors_24h",
    "Erros em agent_traces (node_error NOT NULL) nas últimas 24h",
    ["tenant_id", "tenant_name"],
)
TOKENS_24H = Gauge(
    "saas_tenant_tokens_24h",
    "Tokens consumidos (in/out) nas últimas 24h",
    ["tenant_id", "tenant_name", "direction"],
)
REFRESH_LATENCY = Histogram(
    "saas_business_metrics_refresh_seconds",
    "Tempo gasto refrescando métricas de negócio",
)
REFRESH_ERRORS = Counter(
    "saas_business_metrics_refresh_errors_total",
    "Falhas ao refrescar métricas de negócio (tenant ou global)",
    ["scope"],
)


_task: Optional[asyncio.Task] = None


def start_refresher() -> None:
    """Sobe a task de fundo. Idempotente."""
    global _task
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(), name="business-metrics-refresher")
    log.info("metrics.refresher.started", interval_s=REFRESH_INTERVAL_SECONDS)


async def stop_refresher() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _task = None


async def _loop() -> None:
    while True:
        try:
            await refresh_business_metrics()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            REFRESH_ERRORS.labels(scope="loop").inc()
            log.warning("metrics.refresh_loop_error", exc=str(exc))
        await asyncio.sleep(REFRESH_INTERVAL_SECONDS)


async def refresh_business_metrics() -> None:
    """Faz um snapshot completo das métricas de negócio.

    Limpa todos os Gauges per-tenant antes de repopular: tenant desativado /
    deletado some dos labels (evita séries fantasma crescendo pra sempre).
    """
    t0 = time.monotonic()
    try:
        # Reset dos Gauges com labels per-tenant (evita stale series).
        for g in (
            TICKETS_OPEN, TICKETS_PAUSED,
            MESSAGES_TOTAL, MESSAGES_24H,
            CARTS_OPEN, ORDERS_TOTAL, ORDERS_24H,
            AGENT_ERRORS_24H, TOKENS_24H,
        ):
            g.clear()

        async with get_db_conn() as conn:
            await _refresh_global(conn)
            await _refresh_per_tenant(conn)
    finally:
        REFRESH_LATENCY.observe(time.monotonic() - t0)


async def _refresh_global(conn) -> None:
    row = await conn.fetchrow(
        """
        SELECT COUNT(*) FILTER (WHERE active = TRUE)  AS active,
               COUNT(*) FILTER (WHERE active = FALSE) AS inactive
          FROM public.tenants
        """
    )
    TENANTS_TOTAL.labels(active="true").set(int(row["active"] or 0))
    TENANTS_TOTAL.labels(active="false").set(int(row["inactive"] or 0))


async def _refresh_per_tenant(conn) -> None:
    tenants = await conn.fetch(
        """
        SELECT id::text AS id, name, schema_name, plan
          FROM public.tenants
         WHERE active = TRUE
         ORDER BY created_at
        """
    )

    # tickets (tabela global) — um round-trip agrega tudo
    state_rows = await conn.fetch(
        """
        SELECT tenant_id::text AS tenant_id,
               COUNT(*) FILTER (WHERE closed_at IS NULL) AS open,
               COUNT(*) FILTER (
                    WHERE closed_at IS NULL
                      AND ai_paused = TRUE
                      AND (paused_until IS NULL OR paused_until > NOW())
               ) AS paused
          FROM public.conversation_state
         GROUP BY tenant_id
        """
    )
    state_by_tenant = {r["tenant_id"]: r for r in state_rows}

    for t in tenants:
        tid, tname, schema, plan = t["id"], t["name"], t["schema_name"], (t["plan"] or "basic")
        labels_t = (tid, tname, plan)

        row = state_by_tenant.get(tid)
        TICKETS_OPEN.labels(*labels_t).set(float(row["open"]) if row else 0.0)
        TICKETS_PAUSED.labels(*labels_t).set(float(row["paused"]) if row else 0.0)

        if not _SAFE_SCHEMA.match(schema):
            log.warning("metrics.unsafe_schema_skipped", schema=schema)
            continue

        try:
            await _refresh_one_tenant_schema(conn, tid, tname, schema)
        except Exception as exc:  # noqa: BLE001
            REFRESH_ERRORS.labels(scope="tenant").inc()
            log.warning(
                "metrics.tenant_refresh_failed",
                tenant_id=tid, schema=schema, exc=str(exc),
            )


async def _refresh_one_tenant_schema(conn, tid: str, tname: str, schema: str) -> None:
    # ── conversation_logs: por role, total + 24h + tokens 24h ──────────────
    rows = await conn.fetch(
        f"""
        SELECT role,
               COUNT(*)                                                       AS total,
               COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS last24,
               COALESCE(SUM(tokens_in)  FILTER (WHERE created_at > NOW() - INTERVAL '24 hours'), 0) AS toks_in_24h,
               COALESCE(SUM(tokens_out) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours'), 0) AS toks_out_24h
          FROM {schema}.conversation_logs
         GROUP BY role
        """
    )
    toks_in_24h = 0
    toks_out_24h = 0
    seen_roles: set[str] = set()
    for r in rows:
        role = (r["role"] or "unknown")[:32]
        seen_roles.add(role)
        MESSAGES_TOTAL.labels(tid, tname, role).set(float(r["total"] or 0))
        MESSAGES_24H.labels(tid, tname, role).set(float(r["last24"] or 0))
        toks_in_24h  += int(r["toks_in_24h"] or 0)
        toks_out_24h += int(r["toks_out_24h"] or 0)

    # Mantém séries comuns (user/assistant) presentes mesmo zeradas — facilita
    # agregação em dashboards com sum by (role).
    for role in ("user", "assistant"):
        if role not in seen_roles:
            MESSAGES_TOTAL.labels(tid, tname, role).set(0.0)
            MESSAGES_24H.labels(tid, tname, role).set(0.0)

    TOKENS_24H.labels(tid, tname, "in").set(float(toks_in_24h))
    TOKENS_24H.labels(tid, tname, "out").set(float(toks_out_24h))

    # ── carrinhos abertos ──────────────────────────────────────────────────
    cart_row = await conn.fetchrow(
        f"""
        SELECT COUNT(*) AS n
          FROM {schema}.cart
         WHERE jsonb_typeof(items) = 'array'
           AND jsonb_array_length(items) > 0
        """
    )
    CARTS_OPEN.labels(tid, tname).set(float(cart_row["n"] or 0))

    # ── orders por status (all-time + 24h) ────────────────────────────────
    ord_rows = await conn.fetch(
        f"""
        SELECT COALESCE(status, 'unknown') AS status,
               COUNT(*)                                                   AS total,
               COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '24 hours') AS last24
          FROM {schema}.orders
         GROUP BY 1
        """
    )
    for r in ord_rows:
        status = str(r["status"])[:32]
        ORDERS_TOTAL.labels(tid, tname, status).set(float(r["total"] or 0))
        ORDERS_24H.labels(tid, tname, status).set(float(r["last24"] or 0))

    # ── agent_traces: erros reais nas últimas 24h ─────────────────────────
    # Erro vive em DOIS lugares (ver agents/nodes/skills/_base.py):
    #   • coluna `error` (text) — erro top-level do turno (final state)
    #   • `steps[i].error`     — erro estruturado por node (dict {type,msg,stack})
    # Conta o trace se tiver qualquer um dos dois. DISTINCT pra não dobrar
    # quando o mesmo trace tem múltiplos nodes com erro.
    err_row = await conn.fetchrow(
        f"""
        SELECT COUNT(DISTINCT t.id) AS n
          FROM {schema}.agent_traces t
     LEFT JOIN LATERAL jsonb_array_elements(t.steps) AS step ON TRUE
         WHERE t.created_at > NOW() - INTERVAL '24 hours'
           AND (t.error IS NOT NULL OR step ? 'error')
        """
    )
    AGENT_ERRORS_24H.labels(tid, tname).set(float((err_row or {"n": 0})["n"] or 0))
