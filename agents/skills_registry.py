"""
agents/skills_registry.py

Fonte ÚNICA de metadados de skill.

Antes deste módulo, a mesma informação vivia espalhada e divergia:
  • `_KNOWN_SKILLS`            (agents/router.py)
  • `_VALID_HANDOFF_TARGETS`   (agents/nodes/skills/_base.py)
  • `all_skill_nodes`          (agents/graph_builder.py)
  • descrições por skill       (agents/nodes/orchestrator.py::_build_skills_list)

Adicionar um skill exigia tocar 4+ lugares (checklist CLAUDE.md §6) e era fácil
esquecer um — o resultado era roteamento para um node que não existe, ou um
destino de handoff aceito num lugar e rejeitado no outro.

Agora todos derivam de `SKILLS`. Para evitar import circular
(registry → skill node → _base → registry), o módulo **não importa** os nodes
no load: guarda o caminho `"módulo:função"` e resolve sob demanda em
`SkillDefinition.load_node()`. Assim `router.py`/`_base.py` podem importar o
registry sem puxar a árvore de skills.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Callable


@dataclass(frozen=True)
class SkillDefinition:
    """Metadados declarativos de um skill.

    Campos:
        name:        identificador (igual ao node no grafo e à chave de prompt).
        plan_min:    plano mínimo que habilita o skill ("basic"|"pro"|
                     "enterprise"). "always" = infra sempre presente (guardrails).
        description: frase curta consumida pelo prompt do orchestrator.
        node_path:   "pacote.modulo:funcao" — resolvido preguiçosamente para
                     evitar import circular com _base/skills.
        allowed_handoffs: destinos válidos de handoff a partir deste skill.
                     Alimenta o `Literal` do HandoffTool (o LLM não consegue
                     rotear para fora desta lista) e o parser de fallback.
                     Vazio = skill não faz handoff (não recebe HandoffTool).
        capabilities: capabilities que moldam o comportamento do skill
                     (documental + costura para gating futuro).
        supported_conversation_types: COSTURA FUTURA (Fase 2). Vazio hoje —
                     será preenchido quando o enum ConversationType existir.
        is_safety_net: guardrails — sempre roteável, fora do gating de plano.
        sticky_ownable: pode ser DONO da conversa no sticky ownership? Default
                     True. False para skills TRANSITÓRIAS que não conduzem um
                     fluxo multi-turno — recepção (`saudacao`), reengajamento
                     (`recuperador`) e o safety net (`guardrails`). Esses não
                     têm handoff (beco sem saída): se virassem owner, o sticky
                     prenderia a conversa neles (ex.: cliente cumprimenta →
                     saudacao owna → "tô com dor de cabeça" continua preso no
                     saudacao em vez de ir ao farmaceutico). Consumido em
                     `orchestrator` (não honra owner não-ownable) e em
                     `context.save_context` (não persiste owner não-ownable).
    """
    name: str
    plan_min: str
    description: str
    node_path: str
    allowed_handoffs: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    supported_conversation_types: tuple[str, ...] = ()
    is_safety_net: bool = False
    sticky_ownable: bool = True

    def load_node(self) -> Callable:
        """Importa e retorna a função-node do skill (lazy, sem ciclo)."""
        module_path, fn_name = self.node_path.split(":", 1)
        module = importlib.import_module(module_path)
        return getattr(module, fn_name)


# ── Catálogo ──────────────────────────────────────────────────────────────────
# Ordem reflete a hierarquia de planos (recepção → clínico → comercial → infra).
# allowed_handoffs reflete EXATAMENTE os marcadores que cada prompt emite hoje
# (só farmaceutico e vendedor fazem handoff entre skills na prática).
SKILLS: dict[str, SkillDefinition] = {
    "saudacao": SkillDefinition(
        name="saudacao",
        # ⚠️ NÃO reintroduzir "mensagens ambíguas"/catch-all aqui. Esta description
        # é injetada no prompt do orchestrator (_build_skills_list). Um termo
        # catch-all vira ÍMÃ de misroute: a LLM (sobretudo a fraca) manda toda
        # mensagem que "não tem certeza" pro saudacao — que é BECO SEM SAÍDA
        # (allowed_handoffs vazio), prendendo a conversa. Ambíguo → fallback do
        # _SYSTEM (farmaceutico). Cf. SPEC 02 §saudacao + SPEC 01 §roteamento.
        plan_min="basic",
        description="apenas saudações e primeiro contato sem pedido concreto (oi, bom dia, tudo bem)",
        node_path="agents.nodes.skills.saudacao:saudacao_node",
        # Recepção é transitória e SEM handoff (beco): nunca pode ser sticky owner,
        # senão a conversa fica presa aqui assim que o cliente cumprimenta.
        sticky_ownable=False,
    ),
    "farmaceutico": SkillDefinition(
        name="farmaceutico",
        plan_min="basic",
        description="dúvidas farmacêuticas, bulas, posologia, interações, sintomas",
        node_path="agents.nodes.skills.farmaceutico:farmaceutico_node",
        allowed_handoffs=("vendedor", "genericos", "principio_ativo"),
        capabilities=("sales.stock_check", "inventory.track_stock", "sales.pharmacist_validation"),
    ),
    "principio_ativo": SkillDefinition(
        name="principio_ativo",
        plan_min="pro",
        description="identificar princípio ativo de medicamentos",
        node_path="agents.nodes.skills.principio_ativo:principio_ativo_node",
        # Sem isto era beco sem saída: cliente pergunta disponibilidade/preço e o
        # skill não tinha como repassar ao vendedor (nem checa estoque).
        allowed_handoffs=("vendedor", "farmaceutico"),
    ),
    "genericos": SkillDefinition(
        name="genericos",
        plan_min="pro",
        description="buscar alternativas genéricas / similares",
        node_path="agents.nodes.skills.genericos:genericos_node",
        allowed_handoffs=("vendedor", "farmaceutico"),
    ),
    "vendedor": SkillDefinition(
        name="vendedor",
        plan_min="pro",
        description="compras, preços, consulta de estoque, carrinho, pedidos",
        node_path="agents.nodes.skills.vendedor:vendedor_node",
        allowed_handoffs=("farmaceutico", "genericos", "principio_ativo"),
        capabilities=(
            "sales.stock_check", "sales.cross_sell", "delivery.shipping_by_cep",
            "payments.pix_asaas", "sales.pharmacist_validation",
        ),
    ),
    "recuperador": SkillDefinition(
        name="recuperador",
        plan_min="enterprise",
        description="reengajamento de clientes inativos",
        node_path="agents.nodes.skills.recuperador:recuperador_node",
        # Reengajamento é transitório e sem handoff — não conduz fluxo multi-turno.
        sticky_ownable=False,
    ),
    "guardrails": SkillDefinition(
        name="guardrails",
        plan_min="always",
        description="off-topic, emergências médicas, conteúdo impróprio",
        node_path="agents.nodes.skills.guardrails:guardrails_node",
        is_safety_net=True,
        # Safety net é single-shot — nunca dono da conversa.
        sticky_ownable=False,
    ),
}


# ── Derivações (substituem as constantes espalhadas) ──────────────────────────

#: Todos os skills que existem como node no grafo (≡ antigo _KNOWN_SKILLS).
KNOWN_SKILLS: frozenset[str] = frozenset(SKILLS)

#: Skills selecionáveis pelo plano do tenant (exclui infra como guardrails).
PLAN_GATED_SKILLS: tuple[str, ...] = tuple(
    name for name, d in SKILLS.items() if not d.is_safety_net
)


#: Skills que PODEM ser dono da conversa no sticky ownership (excluí recepção/
#: reengajamento/safety net — transitórios e sem handoff). Ver `sticky_ownable`.
STICKY_OWNABLE_SKILLS: frozenset[str] = frozenset(
    name for name, d in SKILLS.items() if d.sticky_ownable
)


def is_sticky_ownable(skill_name: str | None) -> bool:
    """True se o skill pode ser persistido/honrado como sticky owner.

    Skills transitórios (saudacao, recuperador, guardrails) retornam False:
    são becos sem saída que, se virassem owner, prenderiam a conversa. Skill
    desconhecido → False (defensivo: não fixa owner que não existe no grafo).
    """
    d = SKILLS.get(skill_name or "")
    return bool(d and d.sticky_ownable)


def valid_handoff_targets() -> frozenset[str]:
    """União de todos os destinos de handoff (≡ antigo _VALID_HANDOFF_TARGETS).

    Usado pelo PARSER de fallback em _base.py, que precisa aceitar qualquer
    destino que algum skill possa emitir. O gating por-skill (mais estrito) é
    feito via `allowed_handoffs_for`.
    """
    targets: set[str] = set()
    for d in SKILLS.values():
        targets.update(d.allowed_handoffs)
    return frozenset(targets)


def allowed_handoffs_for(skill_name: str) -> tuple[str, ...]:
    """Destinos válidos a partir de um skill (alimenta o Literal do HandoffTool)."""
    d = SKILLS.get(skill_name)
    return d.allowed_handoffs if d else ()


def skill_descriptions(available: list[str] | None = None) -> dict[str, str]:
    """Mapa skill→descrição para o prompt do orchestrator.

    `available` filtra para os skills ativos do tenant (mantém o comportamento
    de `_build_skills_list`, que só lista o que o tenant tem).
    """
    if available is None:
        return {name: d.description for name, d in SKILLS.items()}
    return {
        name: SKILLS[name].description
        for name in available
        if name in SKILLS
    }


def load_skill_nodes(names: list[str]) -> dict[str, Callable]:
    """Resolve nomes → funções-node (lazy import). Usado pelo graph_builder."""
    return {name: SKILLS[name].load_node() for name in names if name in SKILLS}
