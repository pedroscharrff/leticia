"""
Captura de tokens consumidos em cada chamada LLM.

Modelo:
  • Um `TokenUsageCallback` é anexado a TODO model instanciado por
    `llm/providers.py`. LangChain chama `on_llm_end` após cada `ainvoke`,
    expondo `usage_metadata` normalizado (input_tokens, output_tokens) e
    `response_metadata.model_name`.
  • Por turno do agente o caller (workers/celery_app.py) abre um buffer com
    `begin_turn(tenant_id, tenant_name)` antes do `graph.ainvoke`. O callback
    acumula nesse buffer (via ContextVar — isola por task asyncio) E incrementa
    o Counter Prometheus `saas_llm_tokens_total` em tempo real.
  • Ao fim do turno, `agents/nodes/context.py` lê `aggregate_turn_usage()` e
    grava as colunas tokens_in/tokens_out/llm_model em `conversation_logs`.

Por que ContextVar e não state do grafo: o LLM é chamado dentro de skills,
nodes, tools, retries — propagar o contador via state seria invasivo. ContextVar
é herdado automaticamente por tasks asyncio filhas dentro do mesmo turno.

Por que Counter (não Gauge): queremos `rate()`, `increase()` em janelas
arbitrárias no Grafana (custo/min, custo/dia, etc). Counter é monotônico,
sobrevive a restart do scrape.
"""
from __future__ import annotations

import contextvars
from typing import Any, TypedDict

import structlog
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from prometheus_client import Counter

log = structlog.get_logger()


# ── Prometheus Counter ────────────────────────────────────────────────────────
# Definido aqui (não em api/services/metrics_collector.py) porque é incrementado
# no caminho quente do LLM. Mantém llm/ desacoplado de api/.
LLM_TOKENS_TOTAL = Counter(
    "saas_llm_tokens_total",
    "Tokens consumidos por chamada LLM (cumulativo). Use rate()/increase() no Grafana.",
    ["tenant_id", "tenant_name", "llm_model", "direction"],
)


# ── ContextVars (escopo por turno) ────────────────────────────────────────────

class TurnUsageRecord(TypedDict):
    tokens_in: int
    tokens_out: int
    model: str


_turn_usage: contextvars.ContextVar[list[TurnUsageRecord] | None] = contextvars.ContextVar(
    "turn_usage", default=None
)
_turn_tenant: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "turn_tenant", default=None
)


def begin_turn(tenant_id: str, tenant_name: str = "") -> None:
    """Inicia buffer de uso pra este turno. Chamar antes do graph.ainvoke."""
    _turn_usage.set([])
    _turn_tenant.set((tenant_id, tenant_name or ""))


def get_turn_usage() -> list[TurnUsageRecord]:
    """Retorna lista de registros acumulados no turno corrente."""
    return _turn_usage.get() or []


def aggregate_turn_usage() -> dict[str, Any]:
    """Soma o turno e retorna {tokens_in, tokens_out, llm_model}.

    `llm_model` é o último modelo visto no turno (heurística boa-o-bastante:
    a maioria dos turnos chama 1 modelo dominante; um turno multi-LLM perde
    granularidade nessa view per-row de conversation_logs, mas o Counter
    Prometheus mantém detalhe por modelo).
    """
    records = get_turn_usage()
    if not records:
        return {"tokens_in": 0, "tokens_out": 0, "llm_model": None}
    return {
        "tokens_in": sum(r["tokens_in"] for r in records),
        "tokens_out": sum(r["tokens_out"] for r in records),
        "llm_model": records[-1]["model"],
    }


# ── Callback ──────────────────────────────────────────────────────────────────

class TokenUsageCallback(BaseCallbackHandler):
    """Hook em todo `ainvoke`. Stateless — depende de ContextVars do turno."""

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        try:
            self._record(response)
        except Exception as exc:  # noqa: BLE001
            # Captura silenciosa: nunca quebrar o turno por causa de telemetria.
            log.warning("usage_tracking.parse_failed", exc=str(exc))

    def _record(self, response: LLMResult) -> None:
        for gen_list in response.generations or []:
            for gen in gen_list:
                msg = getattr(gen, "message", None)
                if msg is None:
                    continue
                # LangChain 0.3+ expõe usage normalizado em msg.usage_metadata
                # (Anthropic, OpenAI, Google). Fallback pra llm_output.token_usage
                # (OpenAI antigo).
                usage = getattr(msg, "usage_metadata", None) or {}
                if not usage and response.llm_output:
                    tok = response.llm_output.get("token_usage", {})
                    usage = {
                        "input_tokens": tok.get("prompt_tokens", 0),
                        "output_tokens": tok.get("completion_tokens", 0),
                    }
                tokens_in = int(usage.get("input_tokens") or 0)
                tokens_out = int(usage.get("output_tokens") or 0)
                if tokens_in == 0 and tokens_out == 0:
                    continue

                meta = getattr(msg, "response_metadata", {}) or {}
                model = (
                    meta.get("model_name")
                    or meta.get("model")
                    or (response.llm_output or {}).get("model_name")
                    or "unknown"
                )

                # Buffer do turno (lido por context.py no save)
                buf = _turn_usage.get()
                if buf is not None:
                    buf.append({
                        "tokens_in": tokens_in,
                        "tokens_out": tokens_out,
                        "model": model,
                    })

                # Counter Prometheus (incremento imediato, sobrevive ao turno)
                tinfo = _turn_tenant.get()
                if tinfo is not None:
                    tid, tname = tinfo
                    if tokens_in > 0:
                        LLM_TOKENS_TOTAL.labels(tid, tname, model, "in").inc(tokens_in)
                    if tokens_out > 0:
                        LLM_TOKENS_TOTAL.labels(tid, tname, model, "out").inc(tokens_out)
