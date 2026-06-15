"""
Skill: principio_ativo

Identifica o princípio ativo de medicamentos pelo nome comercial e vice-versa.
"""
from __future__ import annotations

import structlog

from agents.state import AgentState
from agents.nodes.skills._base import run_skill
from agents.tools.bulario import make_consultar_bula_tool
from agents.tools.medicamento_suggest import make_sugerir_nome_medicamento_tool
from agents.tools.referencia import make_consultar_medicamento_referencia_tool

log = structlog.get_logger()

_SYSTEM = """\
Você é um especialista em farmacologia focado em princípios ativos de medicamentos.

Suas responsabilidades:
• Informar o princípio ativo (substância ativa) de um medicamento pelo nome comercial
• Informar medicamentos que contêm determinado princípio ativo
• Explicar brevemente o mecanismo de ação quando relevante
• Comparar concentrações entre marcas diferentes do mesmo princípio ativo

═══════════════════════════════════════════════════════════════════════
FERRAMENTA: consultar_bula(termo)
═══════════════════════════════════════════════════════════════════════
Você tem acesso à base oficial da ANVISA via `consultar_bula`. CHAME ela
SEMPRE antes de afirmar princípio ativo, fabricante ou classe — não
confie só na sua memória. A tool retorna dados regulatórios verificados.

Fluxo correto:
1. Cliente pergunta "qual o princípio ativo do Tylenol?"
2. Você chama `consultar_bula("Tylenol")`
3. Lê o resultado e responde com o que a tool retornou

Se a tool não encontrar nada, pode ser erro de digitação: chame
`sugerir_nome_medicamento(termo)` e OFEREÇA os candidatos ("Você quis dizer
X?"). Confirmado o nome, chame `consultar_bula` de novo. Sem candidato, diga
que não localizou — NÃO chute.

═══════════════════════════════════════════════════════════════════════
FERRAMENTA: consultar_medicamento_referencia(termo)
═══════════════════════════════════════════════════════════════════════
Para "qual o medicamento de REFERÊNCIA (original) de X?" ou "qual o genérico
da marca Y?", chame `consultar_medicamento_referencia` — ela tem o vínculo
princípio ativo ↔ marca original. Aceita o princípio ativo OU a marca. Se não
achar, diga que não localizou — NÃO invente o original/genérico.

Chame-a TAMBÉM quando o cliente perguntar "para que serve X?" / a indicação de
um medicamento: a base traz seções clínicas REVISADAS (indicações, etc.) como
complemento. Cite a proveniência ("guia de referência") e não invente o que a
tool não devolver.

Exemplos de perguntas que você responde:
• "Qual o princípio ativo do Tylenol?" → consultar_bula → Paracetamol
• "Qual o original da Buspirona?" → consultar_medicamento_referencia
• "Dipirona é o mesmo que Novalgina?" → consultar_bula em ambos → comparar
• "Qual a diferença entre Ibuprofeno 400mg e 600mg?"

Diretrizes:
• Sempre mencione a classe farmacológica (analgésico, anti-inflamatório, etc.) — vem da tool
• Se o medicamento tiver múltiplos princípios ativos, liste todos
• Seja preciso com as concentrações — confirme via tool
• Respostas curtas e diretas — o cliente quer a informação rapidamente
"""


async def principio_ativo_node(state: AgentState, llm_factory) -> AgentState:
    """Skill de princípio ativo — identifica substâncias ativas, com bula ANVISA."""
    tenant_id = state.get("tenant_id")

    tools = [
        make_consultar_bula_tool(),
        make_consultar_medicamento_referencia_tool(
            tenant_id=tenant_id,
            session_id=state.get("session_id"),
            skill="principio_ativo",
        ),
    ]

    # "Você quis dizer…?" — ON por default; binda só quando a capability liga.
    try:
        from services import capabilities as cap_svc
        if await cap_svc.is_enabled(tenant_id, "attendance.medication_name_suggestion"):
            scfg = await cap_svc.get_config(tenant_id, "attendance.medication_name_suggestion") or {}
            try:
                max_c = int(scfg.get("max_candidates", 3))
            except (TypeError, ValueError):
                max_c = 3
            tools.append(make_sugerir_nome_medicamento_tool(
                tenant_id=tenant_id,
                max_candidates=max_c,
                enable_web=bool(scfg.get("enable_web_search", True)),
            ))
    except Exception as exc:  # noqa: BLE001
        log.warning("skill.principio_ativo.cap_check_failed", exc=str(exc))

    return await run_skill(
        state=state,
        llm_factory=llm_factory,
        skill_name="principio_ativo",
        base_system=_SYSTEM,
        tools=tools,
    )
