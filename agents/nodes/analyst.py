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

A resposta faz parte de uma conversa em andamento — leia o HISTÓRICO para
entender o contexto antes de julgar. Mensagens curtas do cliente como "Não",
"Sim", "pode ser", "Não não" são respostas a perguntas anteriores do agente
e devem ser avaliadas À LUZ DO HISTÓRICO, não isoladamente.

Avalie a resposta do assistente:
1. Está em português claro e amigável?
2. Faz sentido no fluxo da conversa (considere o histórico)?
3. Não contém informações falsas ou perigosas sobre medicamentos?
4. É curta e foca em UMA etapa (3-4 frases, uma pergunta) — respostas LONGAS
   demais ou que misturam várias etapas devem ser REPROVADAS.

Aprove com generosidade se a resposta é adequada ao contexto.

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
    messages      = state.get("messages", [])

    # Monta histórico para dar contexto ao analyst
    history_lines = []
    for m in messages[-6:]:  # últimas 6 trocas
        role = "Cliente" if m.get("role") == "user" else "Atendente"
        history_lines.append(f"{role}: {m.get('content', '')}")
    history_text = "\n".join(history_lines) if history_lines else "(sem histórico anterior)"

    # Sem resposta — gera fallback amigável e aprova p/ não travar o fluxo
    if not response_text or not response_text.strip():
        import time as _t
        trace.append({
            "node": "analyst",
            "ts_ms": int(_t.time() * 1000),
            "data": {"approved": True, "reason": "empty_response_fallback"},
        })
        log.warning("analyst.empty_response", session=state.get("session_id"))
        return {
            **state,
            "analyst_approved": True,
            "final_response": "Pronto! Posso te ajudar em algo mais?",
            "trace_steps":    trace,
        }

    try:
        llm = llm_factory("analyst")
        from langchain_core.messages import SystemMessage, HumanMessage
        result = await llm.ainvoke([
            SystemMessage(content=_SYSTEM),
            HumanMessage(content=(
                f"=== HISTÓRICO DA CONVERSA ===\n{history_text}\n\n"
                f"=== NOVA MENSAGEM DO CLIENTE ===\n{current_msg}\n\n"
                f"=== RESPOSTA DO ASSISTENTE PARA AVALIAR ===\n{response_text}"
            )),
        ])

        # Garante string (response.content pode ser lista)
        result_text = result.content if isinstance(result.content, str) else \
            "".join(b.get("text", "") if isinstance(b, dict) else str(b)
                    for b in (result.content or []))
        parsed   = _extract_json(result_text)
        approved = bool(parsed.get("approved", True))
        reason   = parsed.get("reason", "")

    except Exception as exc:
        log.warning("analyst.failed", exc=str(exc))
        approved, reason = True, ""

    import time as _time
    trace.append({
        "node": "analyst",
        "ts_ms": int(_time.time() * 1000),
        "data": {"approved": approved, "reason": reason},
    })

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
