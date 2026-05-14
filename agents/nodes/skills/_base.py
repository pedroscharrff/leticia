"""
Base helper para todos os skill nodes.

Cada skill:
  1. Monta o system prompt (persona + prompt customizado da DB)
  2. Reconstrói as mensagens (histórico + mensagem atual)
  3. Invoca o LLM com retry
  4. Retorna AgentState com final_response atualizado
"""
from __future__ import annotations

import structlog
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from agents.state import AgentState

log = structlog.get_logger()


def _persona_prefix(persona: dict) -> str:
    """Monta instrução de persona a partir do dicionário carregado do DB."""
    if not persona:
        return ""
    name  = persona.get("name", "Assistente")
    tone  = persona.get("tone", "profissional e amigável")
    lang  = persona.get("language", "português brasileiro")
    extra = persona.get("extra_instructions", "")
    text = (
        f"Você se chama {name}. "
        f"Seu tom de comunicação é {tone}. "
        f"Responda sempre em {lang}."
    )
    if extra:
        text += f" {extra}"
    return text


def _build_messages(state: AgentState, system_prompt: str) -> list:
    """Constrói lista de mensagens LangChain com histórico."""
    lc_messages = [SystemMessage(content=system_prompt)]

    for msg in state.get("messages", []):
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))

    lc_messages.append(HumanMessage(content=state.get("current_message", "")))
    return lc_messages


async def run_skill(
    state: AgentState,
    llm_factory,
    skill_name: str,
    base_system: str,
) -> AgentState:
    """
    Executa um skill genérico.

    Args:
        state:        Estado atual do grafo.
        llm_factory:  Callable(skill_name) → LLM — injetado pelo graph_builder.
        skill_name:   Nome do skill para lookup de prompt customizado.
        base_system:  System prompt padrão do skill.
    """
    persona       = state.get("persona", {})
    skill_prompts = state.get("skill_prompts", {})
    trace         = list(state.get("trace_steps", []))

    # Monta system prompt: persona + prompt customizado (se houver) + base
    parts = []
    persona_txt = _persona_prefix(persona)
    if persona_txt:
        parts.append(persona_txt)

    custom_prompt = skill_prompts.get(skill_name, "")
    if custom_prompt:
        parts.append(custom_prompt)
    else:
        parts.append(base_system)

    system_prompt = "\n\n".join(parts)
    messages = _build_messages(state, system_prompt)

    try:
        llm = llm_factory("skill")
        from llm.retry import llm_retry
        async for attempt in llm_retry():
            with attempt:
                response = await llm.ainvoke(messages)
        final_response = response.content

    except Exception as exc:
        log.error("skill.failed", skill=skill_name, exc=str(exc))
        final_response = (
            "Desculpe, tive uma dificuldade técnica agora. "
            "Pode repetir sua pergunta? Estou aqui para ajudar."
        )

    trace.append(f"skill:{skill_name} → resposta gerada")

    return {
        **state,
        "final_response": final_response,
        "trace_steps":    trace,
    }
