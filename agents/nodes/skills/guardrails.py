"""
Skill: guardrails

Node de segurança — intercepta mensagens inapropriadas, fora do escopo
da farmácia ou que requerem atendimento humano.

Quando acionado:
- Recusa educadamente conteúdo inapropriado
- Sinaliza escalate=True para atendimento humano quando necessário
- Sempre disponível (não depende do plano do tenant)
"""
from __future__ import annotations

import structlog
from langchain_core.messages import SystemMessage, HumanMessage

from agents.state import AgentState
from agents.nodes.skills._base import _persona_prefix, _extract_text

log = structlog.get_logger()

_SYSTEM = """\
Você é o sistema de segurança de um assistente de farmácia.

Sua função é avaliar se a mensagem do cliente:
1. Está dentro do escopo da farmácia (medicamentos, saúde, produtos farmacêuticos)
2. É apropriada e não viola diretrizes de uso

Se a mensagem for adequada mas você foi acionado por engano, responda normalmente
como um assistente farmacêutico prestativo.

Se a mensagem for FORA DO ESCOPO (assuntos não relacionados à farmácia):
→ Redirecione gentilmente para o que você pode ajudar.
Exemplo: "Posso ajudar com dúvidas sobre medicamentos e produtos da farmácia.
Como posso te ajudar com isso?"

Se a mensagem indicar EMERGÊNCIA MÉDICA (dor no peito, dificuldade para respirar,
overdose, automutilação, etc.):
→ Responda com urgência e oriente a ligar 192 (SAMU) ou 193 (Bombeiros).
→ Informe que um atendente humano será acionado.

Seja sempre respeitoso, breve e direto.
"""


async def guardrails_node(state: AgentState, llm_factory) -> AgentState:
    """
    Node de segurança — sempre disponível, independente das skills do tenant.
    Pode sinalizar escalate=True para transferência a humano.
    """
    current_message = state.get("current_message", "")
    persona         = state.get("persona", {})
    trace           = list(state.get("trace_steps", []))
    escalate        = False

    # Detecção rápida de emergências (sem chamar LLM para não atrasar)
    emergency_keywords = [
        "infarto", "avc", "derrame", "overdose", "engoli remédio",
        "suicídio", "me matar", "não consigo respirar", "parei de respirar",
        "samu", "emergência", "urgência",
    ]
    msg_lower = current_message.lower()
    if any(kw in msg_lower for kw in emergency_keywords):
        final_response = (
            "🚨 *Isso parece uma emergência médica.*\n\n"
            "Por favor, ligue imediatamente:\n"
            "• *192* — SAMU (Serviço de Atendimento Móvel de Urgência)\n"
            "• *193* — Bombeiros\n\n"
            "Um atendente humano foi notificado para te ajudar agora."
        )
        import time as _time
        trace.append({
            "node": "guardrails",
            "ts_ms": int(_time.time() * 1000),
            "data": {"emergency": True, "escalate": True},
        })
        return {
            **state,
            "final_response": final_response,
            "escalate":       True,
            "trace_steps":    trace,
        }

    # Para outros casos, usa LLM
    parts = []
    persona_txt = _persona_prefix(persona)
    if persona_txt:
        parts.append(persona_txt)
    parts.append(_SYSTEM)
    system_prompt = "\n\n".join(parts)

    try:
        llm = llm_factory("orchestrator")  # usa modelo leve
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=current_message),
        ])
        final_response = _extract_text(response.content)
    except Exception as exc:
        log.error("guardrails.failed", exc=str(exc))
        final_response = "Posso ajudar com informações sobre medicamentos e produtos farmacêuticos. Como posso te ajudar?"

    import time as _time
    trace.append({
        "node": "guardrails",
        "ts_ms": int(_time.time() * 1000),
        "data": {"escalate": escalate},
    })

    return {
        **state,
        "final_response": final_response,
        "escalate":       escalate,
        "trace_steps":    trace,
    }
