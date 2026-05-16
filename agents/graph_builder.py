"""
graph_builder.py

Constrói o LangGraph compilado para um tenant específico.

Fluxo do grafo:
  START
    ↓
  load_context    (Redis + PostgreSQL)
    ↓
  orchestrator    (classifica intenção → escolhe skill)
    ↓  route_to_skill()
  [farmaceutico | principio_ativo | genericos | vendedor | recuperador | guardrails]
    ↓
  analyst         (valida qualidade da resposta)
    ↓  analyst_router()
  ┌─ "approved"  → save_context → END
  ├─ "retry"     → skill (novamente, até max_retries)
  └─ "escalate"  → save_context → END  (callback com flag escalate=True)

Importado por: api/workers/celery_app.py
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any

from langchain_core.language_models import BaseChatModel
from langgraph.graph import StateGraph, END

from agents.state import AgentState
from agents.router import route_to_skill, analyst_router, handoff_router


# ── SkillOverride ─────────────────────────────────────────────────────────────

@dataclass
class SkillOverride:
    """
    Configuração por-skill que sobrescreve os defaults do tenant.

    Campos:
        llm_model:      modelo LLM específico para este skill (ex: "claude-sonnet-4-6")
        llm_provider:   provider específico (ex: "anthropic", "openai", "google")
        prompt_version: versão do prompt a usar (ex: "v1", "v2")
        config_json:    configurações extras em JSON (livre para cada skill)
    """
    llm_model:      str | None = None
    llm_provider:   str | None = None
    prompt_version: str = "v1"
    config_json:    dict = field(default_factory=dict)


# ── TenantConfig ──────────────────────────────────────────────────────────────

@dataclass
class TenantConfig:
    """Configuração de um tenant: skills ativas + modelos LLM."""

    tenant_id:    str
    schema_name:  str
    callback_url: str
    skills_active: list[str] = field(default_factory=list)

    # Plano do tenant (basic | pro | enterprise)
    plan: str = "basic"

    # Overrides por skill (model/provider específicos por skill)
    skill_overrides: dict[str, SkillOverride] = field(default_factory=dict)

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
    Retorna callable(role) → BaseChatModel.

    role pode ser:
    - "orchestrator" | "analyst" | "skill"  → papéis fixos
    - nome de skill (ex: "farmaceutico")    → usa SkillOverride se disponível
    """
    def _get(role: str) -> BaseChatModel:
        role_map = {
            "orchestrator": (cfg.orchestrator_provider, cfg.orchestrator_model),
            "analyst":      (cfg.analyst_provider,      cfg.analyst_model),
            "skill":        (cfg.default_skill_provider, cfg.default_skill_model),
        }

        # Verifica se há override específico para este skill
        if role in cfg.skill_overrides:
            override = cfg.skill_overrides[role]
            provider = override.llm_provider or cfg.default_skill_provider
            model    = override.llm_model    or cfg.default_skill_model
        else:
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


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_graph_for_tenant(cfg: TenantConfig, redis: Any = None):
    """
    Compila e retorna um StateGraph LangGraph para o tenant.

    Args:
        cfg:   Configuração do tenant (skills, modelos, etc.)
        redis: Conexão Redis (os nodes a obtêm via get_redis())

    Returns:
        Grafo compilado com suporte a ainvoke().
    """
    from config import settings
    from agents.nodes.context      import load_context, save_context
    from agents.nodes.orchestrator import orchestrator
    from agents.nodes.analyst      import analyst
    from agents.nodes.skills.farmaceutico    import farmaceutico_node
    from agents.nodes.skills.principio_ativo import principio_ativo_node
    from agents.nodes.skills.genericos       import genericos_node
    from agents.nodes.skills.vendedor        import vendedor_node
    from agents.nodes.skills.recuperador     import recuperador_node
    from agents.nodes.skills.guardrails      import guardrails_node
    from agents.nodes.skills.saudacao        import saudacao_node

    llm_factory = _make_llm_factory(cfg)
    max_retries = settings.analyst_max_retries

    # Bind llm_factory nos nodes via partial
    orch_node    = functools.partial(orchestrator,         llm_factory=llm_factory)
    analyst_node = functools.partial(analyst,              llm_factory=llm_factory, max_retries=max_retries)
    farm_node    = functools.partial(farmaceutico_node,    llm_factory=llm_factory)
    pa_node      = functools.partial(principio_ativo_node, llm_factory=llm_factory)
    gen_node     = functools.partial(genericos_node,       llm_factory=llm_factory)
    vend_node    = functools.partial(vendedor_node,        llm_factory=llm_factory)
    recup_node   = functools.partial(recuperador_node,     llm_factory=llm_factory)
    guard_node   = functools.partial(guardrails_node,      llm_factory=llm_factory)
    sauda_node   = functools.partial(saudacao_node,        llm_factory=llm_factory)

    # ── Mapa de skills disponíveis para este tenant ───────────────────────────
    all_skill_nodes = {
        "saudacao":        sauda_node,   # recepção — basic+
        "farmaceutico":    farm_node,    # dúvidas — basic+
        "principio_ativo": pa_node,      # substâncias — pro+
        "genericos":       gen_node,     # genéricos — pro+
        "vendedor":        vend_node,    # compras — pro+
        "recuperador":     recup_node,   # reengajamento — enterprise
    }
    # Filtra apenas skills ativas + garante fallback mínimo
    active_skills = [s for s in cfg.skills_active if s in all_skill_nodes]
    if not active_skills:
        active_skills = ["farmaceutico"]

    active_skill_nodes = {s: all_skill_nodes[s] for s in active_skills}

    # ── Grafo ─────────────────────────────────────────────────────────────────
    graph = StateGraph(AgentState)

    # Nodes fixos (sempre presentes)
    graph.add_node("load_context", load_context)
    graph.add_node("orchestrator", orch_node)
    graph.add_node("analyst",      analyst_node)
    graph.add_node("save_context", save_context)
    graph.add_node("guardrails",   guard_node)   # sempre presente (safety net)

    # Nodes de skill (apenas os ativos do tenant)
    for skill_name, node_fn in active_skill_nodes.items():
        graph.add_node(skill_name, node_fn)

    # ── Arestas fixas ─────────────────────────────────────────────────────────
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "orchestrator")

    # orchestrator → skill (via route_to_skill)
    routing_map = {**{s: s for s in active_skills}, "guardrails": "guardrails"}
    graph.add_conditional_edges("orchestrator", route_to_skill, routing_map)

    # skill → handoff_router → [outro skill | analyst]
    # Cada skill pode passar a bola para outro skill via marcador [[HANDOFF:skill:ctx]]
    handoff_map = {**{s: s for s in active_skills}, "guardrails": "guardrails", "analyst": "analyst"}
    for skill_name in list(active_skill_nodes.keys()) + ["guardrails"]:
        graph.add_conditional_edges(skill_name, handoff_router, handoff_map)

    # analyst → approved / retry / escalate (via analyst_router)
    # "retry" volta para o skill original; "approved" e "escalate" vão para save_context
    retry_map = {s: s for s in active_skill_nodes.keys()}
    analyst_routing = {
        "approved": "save_context",
        "escalate": "save_context",   # salva e o callback entrega com escalate=True
        "retry":    "farmaceutico",   # fallback para retry no skill principal
        **retry_map,                  # retry vai para o skill que gerou a resposta
    }
    graph.add_conditional_edges("analyst", analyst_router, analyst_routing)

    # save_context → END
    graph.add_edge("save_context", END)

    return graph.compile()
