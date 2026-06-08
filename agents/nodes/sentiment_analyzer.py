"""
Node: sentiment_analyzer

Classifica o sentimento do cliente ANTES do orquestrador e injeta uma orientação
de adaptação (bloco VOLÁTIL) que o próprio skill usa para responder no tom certo.
Opcionalmente dispara o fluxo de transferência humana JÁ EXISTENTE (reusa o flag
`escalate`) quando há frustração acima de um limiar.

Capability: intelligence.sentiment_analysis (opt-in).
  - OFF  → early-return: turno idêntico ao comportamento atual (zero custo).
  - ON   → 1 chamada do modelo leve (Haiku por padrão, configurável).

Princípios (CLAUDE.md §10.1): cliente NUNCA vê erro técnico. Todo o nó é
defensivo — qualquer falha cai em passthrough (retorna o state inalterado).
"""
from __future__ import annotations

import json
import re
import time as _time

import structlog

from agents.state import AgentState

log = structlog.get_logger()

_CAP_KEY = "intelligence.sentiment_analysis"

# Orientações-base por rótulo canônico. Rótulos "positivos/neutros" não geram
# diretiva (nenhuma adaptação necessária → bloco volátil vazio). Rótulos não
# mapeados caem em _DIRECTIVE_GENERIC quando indicam insatisfação.
_DIRECTIVE_BY_LABEL: dict[str, str] = {
    "negativo": (
        "[CONTEXTO EMOCIONAL — não visível ao cliente]\n"
        "O cliente parece insatisfeito. Reconheça a situação com empatia, seja "
        "objetivo e foque em resolver o problema dele neste turno."
    ),
    "frustrado": (
        "[CONTEXTO EMOCIONAL — não visível ao cliente]\n"
        "O cliente demonstra frustração. Priorize empatia, seja conciso, vá "
        "direto à solução e, se fizer sentido, ofereça falar com um atendente humano. "
        "Evite respostas longas ou genéricas."
    ),
    "irritado": (
        "[CONTEXTO EMOCIONAL — não visível ao cliente]\n"
        "O cliente está irritado. Acolha o incômodo com calma e sem defensividade, "
        "não repita o que já foi dito, resolva ou encaminhe para um humano o quanto "
        "antes. Nada de emojis comemorativos."
    ),
}
_DIRECTIVE_GENERIC = (
    "[CONTEXTO EMOCIONAL — não visível ao cliente]\n"
    "O cliente parece insatisfeito. Responda com mais empatia e foco na solução."
)
# Rótulos que NUNCA geram diretiva (sentimento neutro/positivo).
_NO_DIRECTIVE = {"positivo", "neutro", "neutral", "positive"}


def _extract_json(text: str) -> dict:
    """Mesmo padrão tolerante do orchestrator/analyst."""
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


def _csv_set(value: str | None) -> set[str]:
    if not value:
        return set()
    return {p.strip().lower() for p in str(value).split(",") if p.strip()}


def _build_directive(label: str) -> str:
    norm = (label or "").strip().lower()
    if not norm or norm in _NO_DIRECTIVE:
        return ""
    return _DIRECTIVE_BY_LABEL.get(norm, _DIRECTIVE_GENERIC)


def _build_system_prompt(labels: str, analyst_instructions: str) -> str:
    labels_txt = labels or "positivo, neutro, negativo, frustrado, irritado"
    extra = (analyst_instructions or "").strip()
    extra_block = f"\n\nInstruções extras da farmácia:\n{extra}" if extra else ""
    return (
        "Você é um classificador de sentimento de atendimento de farmácia. "
        "Leia o HISTÓRICO e a NOVA MENSAGEM e classifique o sentimento ATUAL do "
        "cliente em UM dos rótulos abaixo (use exatamente o rótulo, em minúsculas):\n"
        f"{labels_txt}\n"
        "Considere o contexto: mensagens curtas ('não', 'de novo isso') devem ser "
        "lidas à luz do histórico, não isoladamente."
        f"{extra_block}\n\n"
        'Responda APENAS com JSON válido: '
        '{"sentiment": "<rótulo>", "score": <0.0-1.0>}'
    )


async def sentiment_analyzer(state: AgentState, llm_factory) -> AgentState:
    """Classifica sentimento e injeta diretiva volátil. llm_factory é injetado
    pelo graph_builder. No-op quando a capability está OFF ou em qualquer erro."""
    tenant_id = state.get("tenant_id")

    # ── Gate: capability OFF → passthrough total (zero custo/latência) ─────────
    try:
        from services import capabilities as cap_svc
        if not await cap_svc.is_enabled(tenant_id, _CAP_KEY):
            return state
        config = await cap_svc.get_config(tenant_id, _CAP_KEY)
    except Exception as exc:  # noqa: BLE001
        log.warning("sentiment.gate.failed", tenant=tenant_id, exc=str(exc))
        return state

    current_message = (state.get("current_message", "") or "").strip()
    if not current_message:
        return state

    # ── Config ─────────────────────────────────────────────────────────────────
    # provider_model é um único dropdown no portal no formato "provider|model"
    # (ex.: "anthropic|claude-haiku-4-5-20251001"). Eliminar a chance do
    # operador combinar provider/model incompatíveis. Enum oficial vive no
    # config_schema da capability (mig 063), espelhando llm/providers.py.
    provider, model = None, None
    pm = (config.get("provider_model") or "").strip()
    if "|" in pm:
        p, m = pm.split("|", 1)
        provider = p.strip().lower() or None
        model    = m.strip()         or None
    labels       = config.get("labels") or "positivo, neutro, negativo, frustrado, irritado"
    instructions = config.get("analyst_instructions") or ""
    try:
        history_turns = int(config.get("history_turns", 3) or 3)
    except (TypeError, ValueError):
        history_turns = 3
    history_turns = max(1, min(history_turns, 6))

    # Histórico recente para dar contexto ao classificador
    history_lines = []
    for m in (state.get("messages", []) or [])[-(history_turns * 2):]:
        role = "Cliente" if m.get("role") == "user" else "Atendente"
        content = (m.get("content") or "").strip()
        if content:
            history_lines.append(f"{role}: {content}")
    history_text = "\n".join(history_lines) if history_lines else "(sem histórico)"

    trace = list(state.get("trace_steps", []))
    _node_error: dict | None = None
    sentiment = ""
    score = 0.0

    try:
        # provider/model vêm da config da capability (anthropic | openai | google
        # | ollama). Quando ausentes, factory cai no default do role 'sentiment'
        # (Haiku). BYOK é respeitado pelo factory transparentemente.
        try:
            llm = llm_factory("sentiment", provider=provider, model=model)
        except TypeError:
            # Compatibilidade defensiva caso o factory antigo não aceite kwargs.
            llm = llm_factory("sentiment")

        from langchain_core.messages import SystemMessage, HumanMessage
        from llm.retry import llm_retry

        system_prompt = _build_system_prompt(labels, instructions)
        user_content = (
            f"=== HISTÓRICO ===\n{history_text}\n\n"
            f"=== NOVA MENSAGEM DO CLIENTE ===\n{current_message}"
        )
        async for attempt in llm_retry():
            with attempt:
                response = await llm.ainvoke([
                    SystemMessage(content=system_prompt),
                    HumanMessage(content=user_content),
                ])

        content = response.content
        if not isinstance(content, str):
            content = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in (content or [])
            )
        parsed = _extract_json(content)
        sentiment = str(parsed.get("sentiment", "")).strip().lower()
        try:
            score = float(parsed.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        score = max(0.0, min(score, 1.0))
    except Exception as exc:  # noqa: BLE001
        import traceback as _tb
        _node_error = {
            "type": type(exc).__name__,
            "msg": str(exc),
            "stack": _tb.format_exc()[-1500:],
        }
        log.warning("sentiment.classify.failed", tenant=tenant_id, exc=str(exc))
        # Passthrough: não bloqueia o turno.
        trace.append({
            "node": "sentiment_analyzer",
            "ts_ms": int(_time.time() * 1000),
            "data": {"error": _node_error},
        })
        return {**state, "trace_steps": trace}

    directive = _build_directive(sentiment)

    # ── Escalonamento opcional (reusa o flag `escalate` existente) ──────────────
    escalate_now = False
    if bool(config.get("escalate_on_frustration", False)):
        esc_labels = _csv_set(config.get("escalation_labels") or "frustrado, irritado")
        try:
            threshold = float(config.get("escalation_threshold", 0.7) or 0.7)
        except (TypeError, ValueError):
            threshold = 0.7
        if sentiment in esc_labels and score >= threshold:
            escalate_now = True

    log.info(
        "sentiment.classified",
        tenant=tenant_id,
        sentiment=sentiment,
        score=round(score, 2),
        escalate=escalate_now,
    )

    trace.append({
        "node": "sentiment_analyzer",
        "ts_ms": int(_time.time() * 1000),
        "data": {
            "sentiment": sentiment,
            "score": round(score, 2),
            "directive": bool(directive),
            "escalate": escalate_now,
        },
    })

    updates: dict = {
        "sentiment": sentiment,
        "sentiment_score": score,
        "sentiment_directive": directive,
        "trace_steps": trace,
    }
    if escalate_now:
        # Reusa o flag existente — os portões de handoff (handoff_config.enabled,
        # etc.) ainda valem downstream. NÃO criamos caminho de transferência novo.
        updates["escalate"] = True
        # Rótulo da ORIGEM da escalação. O worker usa isso para escolher uma
        # mensagem de transferência específica (config `transfer_message` desta
        # capability) sem afetar os outros gatilhos (skill [[ESCALATE]], keyword,
        # order_finalized continuam com o comportamento atual).
        updates["escalate_reason"] = "sentiment"

    return {**state, **updates}
