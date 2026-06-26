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

# Papéis LEVES absorvidos pela PLATAFORMA (orquestração híbrida): roteamento +
# análise + sentimento são classificação por-turno, baratos, e a qualidade do
# roteamento é onde o produto inteiro depende. Rodam SEMPRE no modelo forte/barato
# da plataforma (Anthropic Haiku via `settings.default_orchestrator_*`/`anthropic_api_key`),
# IGNORANDO o BYOK do tenant — mesmo quando o tenant usa um modelo fraco/barato
# (DeepSeek/Gemini) nas skills. Assim o cliente paga só os agentes (skills) e a
# plataforma absorve o roteador. As SKILLS continuam no provider BYOK do tenant.
_PLATFORM_ROLES = frozenset({"orchestrator", "analyst", "sentiment"})


def _make_llm_factory(cfg: TenantConfig):
    """
    Retorna callable(role) → BaseChatModel.

    role pode ser:
    - "orchestrator" | "analyst" | "skill"  → papéis fixos
    - nome de skill (ex: "farmaceutico")    → usa SkillOverride se disponível

    Orquestração HÍBRIDA: papéis em `_PLATFORM_ROLES` rodam sempre na chave da
    plataforma (`get_llm`), independente do modo BYOK do tenant. Ver `_PLATFORM_ROLES`.
    """
    def _resolve(role: str, provider: str | None = None, model: str | None = None) -> tuple[str, str]:
        """Resolve o par (provider, model) para um role SEM construir o LLM.

        Mesma precedência do `_get`: override ad-hoc do caller > SkillOverride do
        tenant > default do role. Exposto como `llm_factory.resolve(role)` para
        que skills/runtime descubram o tier do modelo (llm.model_tier) e decidam
        sobre andaime — sem instanciar o cliente nem duplicar a lógica."""
        role_map = {
            "orchestrator": (cfg.orchestrator_provider, cfg.orchestrator_model),
            "analyst":      (cfg.analyst_provider,      cfg.analyst_model),
            # Classificador de sentimento — reusa o modelo leve do orchestrator
            # (Haiku). O nó pode sobrepor o modelo via config da capability.
            "sentiment":    (cfg.orchestrator_provider, cfg.orchestrator_model),
            "skill":        (cfg.default_skill_provider, cfg.default_skill_model),
        }

        # Verifica se há override específico para este skill
        if role in cfg.skill_overrides:
            override = cfg.skill_overrides[role]
            base_provider = override.llm_provider or cfg.default_skill_provider
            base_model    = override.llm_model    or cfg.default_skill_model
        else:
            base_provider, base_model = role_map.get(role, role_map["skill"])

        # Override ad-hoc do caller vence sobre o default do role
        return (provider or base_provider, model or base_model)

    def _get(role: str, provider: str | None = None, model: str | None = None) -> BaseChatModel:
        """Retorna LLM para um role. `provider`/`model` opcionais permitem que
        nodes (ex.: sentiment_analyzer com config própria) sobreponham o par
        sem perder o caminho BYOK gerenciado aqui."""
        provider, model = _resolve(role, provider, model)

        # Papéis leves (orquestrador/analista/sentimento) → SEMPRE plataforma,
        # ignorando o BYOK do tenant. `_resolve` já devolve o par da plataforma
        # para esses papéis (load_tenant_llm_config não aplica override neles).
        if role in _PLATFORM_ROLES:
            from llm.providers import get_llm
            return get_llm(provider=provider, model=model)

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

    # Expõe a resolução (sem construir o LLM) para quem precisa do tier do modelo.
    _get.resolve = _resolve  # type: ignore[attr-defined]
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
    from agents.nodes.context       import load_context, save_context
    from agents.nodes.ingest_media  import ingest_media
    from agents.nodes.orchestrator import orchestrator
    from agents.nodes.sentiment_analyzer import sentiment_analyzer
    from agents.nodes.analyst      import analyst
    from agents.nodes.safety_guard import safety_guard
    from agents.skills_registry import PLAN_GATED_SKILLS, SKILLS, load_skill_nodes

    llm_factory = _make_llm_factory(cfg)
    max_retries = settings.analyst_max_retries

    # Observabilidade do provider/modelo resolvido — UMA linha por turno (o grafo
    # é montado por turno). Permite confirmar em `docker compose logs -f worker`
    # qual LLM está de fato rodando, sobretudo após o tenant trocar pra BYOK
    # (Gemini/OpenAI). Não loga a api_key. Grep: `| grep llm.resolved`.
    import structlog
    structlog.get_logger().info(
        "llm.resolved",
        tenant=cfg.tenant_id,
        mode=cfg.llm_mode,
        byok=bool(cfg.llm_api_key),
        orchestrator=f"{cfg.orchestrator_provider}:{cfg.orchestrator_model}",
        analyst=f"{cfg.analyst_provider}:{cfg.analyst_model}",
        skill=f"{cfg.default_skill_provider}:{cfg.default_skill_model}",
    )

    # Bind llm_factory nos nodes fixos via partial
    orch_node    = functools.partial(orchestrator,         llm_factory=llm_factory)
    sentiment_node = functools.partial(sentiment_analyzer, llm_factory=llm_factory)
    analyst_node = functools.partial(analyst,              llm_factory=llm_factory, max_retries=max_retries)
    # guardrails é infra (safety net) — sempre presente, fora do gating de plano.
    guard_node   = functools.partial(SKILLS["guardrails"].load_node(), llm_factory=llm_factory)

    # ── Mapa de skills disponíveis para este tenant ───────────────────────────
    # DERIVADO do skills_registry (fonte única): resolve os nodes plan-gated e
    # binda llm_factory em cada um. Adicionar skill = só editar o registry.
    all_skill_nodes = {
        name: functools.partial(fn, llm_factory=llm_factory)
        for name, fn in load_skill_nodes(list(PLAN_GATED_SKILLS)).items()
    }
    # Filtra apenas skills ativas + garante fallback mínimo
    active_skills = [s for s in cfg.skills_active if s in all_skill_nodes]
    if not active_skills:
        active_skills = ["farmaceutico"]

    active_skill_nodes = {s: all_skill_nodes[s] for s in active_skills}

    # Skill de fallback dinâmico — usado pelos roteadores quando o destino
    # configurado não existe no grafo deste tenant (ex.: tenant tem só
    # "vendedor" ativo, mas o roteador antigo apontava p/ "farmaceutico").
    # Preferimos "farmaceutico" quando disponível (agente coringa); senão
    # caímos para o primeiro skill ativo configurado pelo tenant.
    fallback_skill = "farmaceutico" if "farmaceutico" in active_skills else active_skills[0]

    # ── Grafo ─────────────────────────────────────────────────────────────────
    graph = StateGraph(AgentState)

    # Nodes fixos (sempre presentes)
    graph.add_node("load_context", load_context)
    graph.add_node("ingest_media", ingest_media)
    graph.add_node("orchestrator", orch_node)
    # Classificador de sentimento — sempre no grafo; faz early-return quando a
    # capability intelligence.sentiment_analysis está OFF (gate runtime cacheado).
    graph.add_node("sentiment_analyzer", sentiment_node)
    graph.add_node("analyst",      analyst_node)
    graph.add_node("save_context", save_context)
    graph.add_node("guardrails",   guard_node)   # sempre presente (safety net)
    # Umbrella de validators pós-LLM (availability + price + prescription +
    # delivery, todos capability-gated; passthrough total em pré-atendimento
    # via `sales.stock_check` OFF / sem catálogo — sempre seguro estar no grafo).
    graph.add_node("safety_guard", safety_guard)

    # Nodes de skill (apenas os ativos do tenant)
    for skill_name, node_fn in active_skill_nodes.items():
        graph.add_node(skill_name, node_fn)

    # ── Arestas fixas ─────────────────────────────────────────────────────────
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "ingest_media")
    # ingest_media → sentiment_analyzer → orchestrator
    # (sentiment_analyzer é passthrough quando a capability está OFF)
    graph.add_edge("ingest_media", "sentiment_analyzer")
    graph.add_edge("sentiment_analyzer", "orchestrator")

    # orchestrator → skill (via route_to_skill)
    routing_map = {**{s: s for s in active_skills}, "guardrails": "guardrails"}
    graph.add_conditional_edges("orchestrator", route_to_skill, routing_map)

    # skill → handoff_router → [outro skill | safety_guard → analyst]
    # Cada skill pode passar a bola para outro skill via marcador [[HANDOFF:skill:ctx]]
    # "analyst" sai do router → passa pelo safety_guard antes (passthrough em
    # pré-atendimento ou se nenhuma capability safety.* está ON) → analyst real.
    handoff_map = {
        **{s: s for s in active_skills},
        "guardrails": "guardrails",
        "analyst":    "safety_guard",
    }
    for skill_name in list(active_skill_nodes.keys()) + ["guardrails"]:
        graph.add_conditional_edges(skill_name, handoff_router, handoff_map)

    # safety_guard → analyst (edge fixa — guard sempre delega pro analyst)
    graph.add_edge("safety_guard", "analyst")

    # analyst → approved / retry / escalate (via analyst_router)
    # "retry" volta para o skill original; "approved" e "escalate" vão para save_context
    retry_map = {s: s for s in active_skill_nodes.keys()}
    analyst_routing = {
        "approved": "save_context",
        "escalate": "save_context",   # salva e o callback entrega com escalate=True
        "retry":    fallback_skill,   # fallback para retry — usa um skill que EXISTE neste grafo
        **retry_map,                  # retry vai para o skill que gerou a resposta
    }
    graph.add_conditional_edges("analyst", analyst_router, analyst_routing)

    # save_context → END
    graph.add_edge("save_context", END)

    return graph.compile()
