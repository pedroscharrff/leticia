"""
agents/router.py

Funções de roteamento condicional do LangGraph.

route_to_skill   → orquestrador decide qual skill node executar
analyst_router   → analyst decide: aprovado / retry / escalar humano
"""
from __future__ import annotations

from agents.state import AgentState

_FALLBACK = "farmaceutico"

# Skills que existem como nodes no grafo
_KNOWN_SKILLS = {
    "farmaceutico",
    "principio_ativo",
    "genericos",
    "vendedor",
    "recuperador",
    "saudacao",     # recepção — ativo em todos os planos
    "guardrails",   # node de segurança — sempre disponível
}


def route_to_skill(state: AgentState) -> str:
    """
    Edge condicional: orchestrator → skill node.

    Regras:
    - "guardrails" sempre é roteado para o node guardrails (independente de
      estar em available_skills) — é o safety net do sistema.
    - Skills desconhecidas ou indisponíveis fazem fallback para "farmaceutico".
    """
    skill            = state.get("selected_skill", _FALLBACK)
    available_skills = set(state.get("available_skills", []))

    # Guardrails é sempre roteado (safety net global)
    if skill == "guardrails":
        return "guardrails"

    # Skill conhecida e ativa para este tenant
    if skill in _KNOWN_SKILLS and skill in available_skills:
        return skill

    # Fallback
    return _FALLBACK


def analyst_router(state: AgentState) -> str:
    """
    Edge condicional: analyst → próximo passo.

    Retorna:
    - "escalate"  → cliente precisa de atendimento humano (prioridade máxima)
    - "approved"  → resposta aprovada, segue para save_context
    - "retry"     → resposta reprovada, volta para o skill regenerar
    """
    if state.get("escalate", False):
        return "escalate"

    if state.get("analyst_approved", True):
        return "approved"

    return "retry"
