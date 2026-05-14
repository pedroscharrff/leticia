"""
graph_builder.py

Constrói o LangGraph compilado para um tenant específico.

Fluxo do grafo:
  START
    ↓
  load_context  (Redis + PostgreSQL)
    ↓
  orchestrator  (classifica intenção → escolhe skill)
    ↓  (conditional routing por selected_skill)
  [farmaceutico | principio_ativo | genericos | vendedor | recuperador]
    ↓
  analyst       (valida qualidade da resposta)
    ↓  (se analyst_approved=False e retry < max → volta ao skill)
  save_context  (persiste no Redis e PostgreSQL)
    ↓
  END

Importado por: api/workers/celery_app.py
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, END

from agents.state import AgentState

# ── TenantConfig ──────────────────────────────────────────────────────────────

@dataclass
class TenantConfig:
    """Configuração de um tenant: skills ativas + modelos LLM."""

    tenant_id:    str
    schema_name:  str
    callback_url: str
    skills_active: list[str] = field(default_factory=list)

    # Modo LLM
    llm_mode:    str = "credits"          # "credits" | "byok"
    llm_api_key: str | None = None        # chave do tenant (BYOK)
    llm_base_url: str | None = None       # para Ollama

    # Modelos por papel
    orchestrator_provider: str = "anthropic"
    orchestrator_model:    str = "claude-haiku-4-5-20251001"
    analyst_provider:      str = "anthropic"
    analyst_model:         str = "claude-haiku-4-5-20251001"
    default_skill_provider: str = "anthropic"
    default_skill_model:   str = "claude-sonnet-4-6"


# ── LLM factory ───────────────────────────────────────────────────────────────

def _make_llm_factory(cfg: TenantConfig):
    """
    Retorna um callable(role) → BaseChatModel.
    role: "orchestrator" | "analyst" | "skill"
    """
    def _get(role: str) -> BaseChatModel:
        role_map = {
            "orchestrator": (cfg.orchestrator_provider, cfg.orchestrator_model),
            "analyst":      (cfg.analyst_provider,      cfg.analyst_model),
            "skill":        (cfg.default_skill_provider, cfg.default_skill_model),
        }
        provider, model = role_map.get(role, role_map["skill"])

        if cfg.llm_mode == "byok" and cfg.llm_api_key:
            from llm.providers import get_llm_for_tenant
            return get_llm_for_tenant(
                provider=provider,
                model=model,
                api_key=cfg.llm_api_key,
                base_url=cfg.llm_base_url,
            )
        else:
            from llm.providers import get_llm
            return get_llm(provider=provider, model=model)

    return _get


# ── Routing ───────────────────────────────────────────────────────────────────

_SKILL_NODES = {
    "farmaceutico":   "farmaceutico",
    "principio_ativo": "principio_ativo",
    "genericos":      "genericos",
    "vendedor":       "vendedor",
    "recuperador":    "recuperador",
}


def _route_to_skill(state: AgentState) -> str:
    """Edge condicional: orquestra → skill node."""
    skill = state.get("selected_skill", "farmaceutico")
    return _SKILL_NODES.get(skill, "farmaceutico")


def _route_after_analyst(state: AgentState) -> str:
    """Edge condicional: analyst → save_context ou retry ao skill."""
    if not state.get("analyst_approved", True):
        # Volta ao skill original para regenerar resposta
        skill = state.get("selected_skill", "farmaceutico")
        return _SKILL_NODES.get(skill, "farmaceutico")
    return "save_context"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph_for_tenant(cfg: TenantConfig, redis: Any = None):
    """
    Compila e retorna um StateGraph LangGraph para o tenant.

    Args:
        cfg:   Configuração do tenant (skills, modelos, etc.)
        redis: Conexão Redis (injetada mas não usada diretamente aqui —
               os nodes a obtêm via get_redis())

    Returns:
        Grafo compilado com suporte a ainvoke().
    """
    from config import settings
    from agents.nodes.context     import load_context, save_context
    from agents.nodes.orchestrator import orchestrator
    from agents.nodes.analyst      import analyst
    from agents.nodes.skills.farmaceutico   import farmaceutico_node
    from agents.nodes.skills.principio_ativo import principio_ativo_node
    from agents.nodes.skills.genericos      import genericos_node
    from agents.nodes.skills.vendedor       import vendedor_node
    from agents.nodes.skills.recuperador    import recuperador_node

    llm_factory = _make_llm_factory(cfg)
    max_retries = settings.analyst_max_retries

    # Bind llm_factory nos nodes via partial (LangGraph requer callables puros)
    orch_node     = functools.partial(orchestrator,     llm_factory=llm_factory)
    analyst_node  = functools.partial(analyst,          llm_factory=llm_factory, max_retries=max_retries)
    farm_node     = functools.partial(farmaceutico_node,   llm_factory=llm_factory)
    pa_node       = functools.partial(principio_ativo_node, llm_factory=llm_factory)
    gen_node      = functools.partial(genericos_node,   llm_factory=llm_factory)
    vend_node     = functools.partial(vendedor_node,    llm_factory=llm_factory)
    recup_node    = functools.partial(recuperador_node, llm_factory=llm_factory)

    # ── Grafo ─────────────────────────────────────────────────────────────────
    graph = StateGraph(AgentState)

    # Nodes
    graph.add_node("load_context",    load_context)
    graph.add_node("orchestrator",    orch_node)
    graph.add_node("farmaceutico",    farm_node)
    graph.add_node("principio_ativo", pa_node)
    graph.add_node("genericos",       gen_node)
    graph.add_node("vendedor",        vend_node)
    graph.add_node("recuperador",     recup_node)
    graph.add_node("analyst",         analyst_node)
    graph.add_node("save_context",    save_context)

    # Arestas fixas
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "orchestrator")

    # orchestrator → skill (conditional)
    skill_map = {
        "farmaceutico":    "farmaceutico",
        "principio_ativo": "principio_ativo",
        "genericos":       "genericos",
        "vendedor":        "vendedor",
        "recuperador":     "recuperador",
    }
    # Filtra apenas skills ativas do tenant
    active_skill_map = {
        k: v for k, v in skill_map.items() if k in cfg.skills_active
    } or {"farmaceutico": "farmaceutico"}  # fallback mínimo

    graph.add_conditional_edges("orchestrator", _route_to_skill, active_skill_map)

    # skill → analyst
    for skill_node in active_skill_map.values():
        graph.add_edge(skill_node, "analyst")

    # analyst → save_context ou retry ao skill
    retry_map = {**active_skill_map, "save_context": "save_context"}
    graph.add_conditional_edges("analyst", _route_after_analyst, retry_map)

    # save_context → END
    graph.add_edge("save_context", END)

    return graph.compile()
