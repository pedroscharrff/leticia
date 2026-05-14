"""
Node: orchestrator

Classifica a intenção do cliente e decide qual skill usar.
Usa o modelo leve (Haiku/Flash) para baixa latência.
"""
from __future__ import annotations

import json
import re
import structlog

from agents.state import AgentState

log = structlog.get_logger()

_SYSTEM = """\
Você é o orquestrador de um assistente de farmácia. Sua única função é classificar a intenção do cliente e escolher a skill correta.

Skills disponíveis:
{skills_list}

Regras de classificação:
- farmaceutico  → dúvidas sobre medicamentos, interações, posologia, reações adversas, contraindicações, bulas
- principio_ativo → "qual o princípio ativo de X?", "X tem [substância]?"
- genericos     → "tem genérico de X?", "qual o genérico mais barato?", substituições de marca
- vendedor      → comprar, preço, disponibilidade, adicionar ao carrinho, pedido, entrega
- recuperador   → cliente sumiu/não comprou, reengajamento, recuperação de carrinho abandonado

Responda APENAS com JSON válido, sem explicações:
{{"skill": "<skill_name>", "confidence": <0.0-1.0>, "intent": "<resumo da intenção em português>"}}

Se nenhuma skill disponível atender, use "farmaceutico" como fallback.\
"""

_FALLBACK_SKILL = "farmaceutico"


def _build_skills_list(available: list[str]) -> str:
    descriptions = {
        "farmaceutico":   "dúvidas farmacêuticas, bulas, posologia, interações",
        "principio_ativo": "identificar princípio ativo de medicamentos",
        "genericos":       "buscar alternativas genéricas / similares",
        "vendedor":        "compras, preços, carrinho, pedidos",
        "recuperador":     "reengajamento de clientes inativos",
    }
    lines = []
    for skill in available:
        desc = descriptions.get(skill, skill)
        lines.append(f"- {skill}: {desc}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Tenta extrair JSON do texto mesmo com texto extra ao redor."""
    # Tenta direto
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Tenta extrair bloco JSON com regex
    match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


async def orchestrator(state: AgentState, llm_factory) -> AgentState:
    """
    Classifica a intenção e seleciona a skill.
    llm_factory é injetado pelo graph_builder via functools.partial.
    """
    available_skills = state.get("available_skills", [_FALLBACK_SKILL])
    current_message  = state.get("current_message", "")
    messages         = state.get("messages", [])

    if not available_skills:
        available_skills = [_FALLBACK_SKILL]

    # Monta contexto resumido (últimas 4 trocas)
    history_text = ""
    for msg in messages[-8:]:
        role = "Cliente" if msg["role"] == "user" else "Assistente"
        history_text += f"{role}: {msg['content']}\n"

    system_prompt = _SYSTEM.format(skills_list=_build_skills_list(available_skills))

    user_content = ""
    if history_text:
        user_content = f"Histórico recente:\n{history_text}\n"
    user_content += f"Nova mensagem do cliente: {current_message}"

    try:
        llm = llm_factory("orchestrator")
        from langchain_core.messages import SystemMessage, HumanMessage
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ])

        parsed = _extract_json(response.content)
        skill      = parsed.get("skill", _FALLBACK_SKILL)
        confidence = float(parsed.get("confidence", 0.5))
        intent     = parsed.get("intent", current_message[:100])

        # Garante que a skill está disponível
        if skill not in available_skills:
            log.warning(
                "orchestrator.skill_unavailable",
                skill=skill,
                available=available_skills,
            )
            skill = _FALLBACK_SKILL

    except Exception as exc:
        log.error("orchestrator.failed", exc=str(exc))
        skill, confidence, intent = _FALLBACK_SKILL, 0.0, current_message[:100]

    log.info(
        "orchestrator.routed",
        skill=skill,
        confidence=round(confidence, 2),
        intent=intent[:60],
    )

    trace = list(state.get("trace_steps", []))
    trace.append(f"orchestrator→{skill} ({confidence:.0%}): {intent[:60]}")

    return {
        **state,
        "selected_skill": skill,
        "confidence":     confidence,
        "intent":         intent,
        "trace_steps":    trace,
    }
