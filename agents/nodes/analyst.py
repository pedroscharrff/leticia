"""
Node: analyst

Valida a qualidade da resposta gerada pela skill.
Se reprovar, sinaliza para retry (até analyst_max_retries vezes).
"""
from __future__ import annotations

import json
import re
import structlog

from agents.state import AgentState

log = structlog.get_logger()

_SYSTEM = """\
Você é um analista de qualidade de atendimento farmacêutico.

Avalie a resposta do assistente:
1. Está em português claro e amigável?
2. Responde diretamente ao que o cliente perguntou?
3. Não contém informações falsas ou perigosas sobre medicamentos?
4. Tem tamanho adequado (não muito curta nem excessivamente longa)?

Responda APENAS com JSON:
{{"approved": true|false, "reason": "<motivo em 1 frase se reprovar>"}}
"""


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {"approved": True}


async def analyst(state: AgentState, llm_factory, max_retries: int = 1) -> AgentState:
    """
    Valida a resposta. Se reprovar e retry_count < max_retries, sinaliza para reprocessar.
    llm_factory é injetado pelo graph_builder.
    """
    response_text = state.get("final_response", "")
    current_msg   = state.get("current_message", "")
    retry_count   = state.get("retry_count", 0)
    trace         = list(state.get("trace_steps", []))

    # Sem resposta — aprova para evitar loop infinito
    if not response_text:
        return {**state, "analyst_approved": True}

    try:
        llm = llm_factory("analyst")
        from langchain_core.messages import SystemMessage, HumanMessage
        result = await llm.ainvoke([
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=(
                f"Pergunta do cliente: {current_msg}\n\n"
                f"Resposta do assistente:\n{response_text}"
            )),
        ])

        parsed   = _extract_json(result.content)
        approved = bool(parsed.get("approved", True))
        reason   = parsed.get("reason", "")

    except Exception as exc:
        log.warning("analyst.failed", exc=str(exc))
        approved, reason = True, ""

    trace.append(f"analyst={'ok' if approved else 'reprovado'}" + (f": {reason}" if reason else ""))

    if not approved and retry_count < max_retries:
        log.info("analyst.retry", reason=reason, retry_count=retry_count + 1)
        return {
            **state,
            "analyst_approved": False,
            "retry_count":      retry_count + 1,
            "final_response":   "",   # limpa para forçar regeneração
            "trace_steps":      trace,
        }

    return {**state, "analyst_approved": True, "trace_steps": trace}
