"""
agents/router.py

Funções de roteamento condicional do LangGraph.

route_to_skill   → orquestrador decide qual skill node executar
analyst_router   → analyst decide: aprovado / retry / escalar humano
"""
from __future__ import annotations

from agents.state import AgentState
from agents.skills_registry import KNOWN_SKILLS as _KNOWN_SKILLS

_HARD_FALLBACK = "farmaceutico"

# _KNOWN_SKILLS (skills que existem como nodes no grafo) agora é DERIVADO do
# skills_registry — fonte única. Inclui saudacao + guardrails.


def _resolve_fallback(state: AgentState) -> str:
    """
    Resolve o skill de fallback olhando para `available_skills` do tenant.

    Regra: se o tenant tem só UM agente ativo (ex.: só `vendedor`), o fallback
    DEVE ser esse agente — senão o LangGraph tenta rotear para um node que não
    existe e quebra o atendimento.

    Ordem de preferência:
      1. "farmaceutico" (se ativo) — agente coringa, lida com qualquer dúvida.
      2. Primeiro skill da lista de disponíveis (ordem do tenant config).
      3. "farmaceutico" como último recurso (caso a lista venha vazia, o
         graph_builder garante que esse node exista).
    """
    available = state.get("available_skills") or []
    if _HARD_FALLBACK in available:
        return _HARD_FALLBACK
    if available:
        return available[0]
    return _HARD_FALLBACK


def route_to_skill(state: AgentState) -> str:
    """
    Edge condicional: orchestrator → skill node.

    Regras:
    - "guardrails" sempre é roteado para o node guardrails (independente de
      estar em available_skills) — é o safety net do sistema.
    - Skills desconhecidas ou indisponíveis fazem fallback para o primeiro
      skill ativo do tenant (ver `_resolve_fallback`).
    """
    skill            = state.get("selected_skill") or ""
    available_skills = set(state.get("available_skills", []))

    # Guardrails é sempre roteado (safety net global)
    if skill == "guardrails":
        return "guardrails"

    # Skill conhecida e ativa para este tenant
    if skill in _KNOWN_SKILLS and skill in available_skills:
        return skill

    # Fallback dinâmico — respeita o que está realmente disponível
    return _resolve_fallback(state)


_MAX_HANDOFFS_PER_TURN = 2  # farmaceutico → vendedor já cobre o caso comum


def handoff_router(state: AgentState) -> str:
    """
    Edge condicional: skill → (outro skill | analyst).

    Quando uma skill emite [[HANDOFF:X:contexto]], rotamos para a skill X NO
    MESMO TURNO para que a resposta final seja CONCATENADA (farmaceutico
    recomenda + vendedor consulta preço, em uma única mensagem ao cliente).

    Limites para evitar loop:
      • `handoff_count` capado em _MAX_HANDOFFS_PER_TURN
      • destino precisa estar em `available_skills`
      • destino não pode ser igual ao último skill executado (anti-loop)
    """
    handoff_to       = state.get("handoff_to")
    available        = set(state.get("available_skills", []))
    handoff_count    = state.get("handoff_count", 0)
    skill_history    = state.get("skill_history", [])
    last_skill       = skill_history[-1] if skill_history else None

    if not handoff_to:
        return "analyst"
    if handoff_count > _MAX_HANDOFFS_PER_TURN:
        return "analyst"
    if handoff_to not in available and handoff_to not in {"guardrails"}:
        return "analyst"
    if handoff_to == last_skill:
        return "analyst"

    return handoff_to


def analyst_router(state: AgentState) -> str:
    """
    Edge condicional: analyst → próximo passo.

    Retorna:
    - "escalate"  → cliente precisa de atendimento humano (prioridade máxima)
    - "approved"  → resposta aprovada, segue para save_context
    - "<skill>"   → resposta reprovada, volta para o ÚLTIMO skill executado
                    para regenerar (mapeado em analyst_routing no graph_builder).
                    Fallback "retry" → "farmaceutico" se não houver histórico.
    """
    if state.get("escalate", False):
        return "escalate"

    if state.get("analyst_approved", True):
        return "approved"

    history = state.get("skill_history") or []
    available = set(state.get("available_skills", []))
    if history:
        last_skill = history[-1]
        # Só repete o último skill se ele ainda for válido + ativo no tenant.
        if last_skill in _KNOWN_SKILLS and last_skill in available:
            return last_skill

    return "retry"
