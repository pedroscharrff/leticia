"""
agents/tools/flow_control.py

Tools de CONTROLE DE CICLO DE VIDA da conversa — handoff, escalation e fim de
atendimento. Substituem (como caminho PRIMÁRIO) os marcadores de texto que o
LLM gerava antes: `[[HANDOFF:skill:ctx]]`, `[[ESCALATE]]`, `[[END]]`.

Por que tool-call em vez de marcador de texto:
  • O marcador era texto gerado pelo LLM, parseado por regex. Quando o modelo
    erra a sintaxe ou esquece de removê-lo, ele VAZA pro cliente no WhatsApp
    ([[handoff_marker_leak]]). Uma tool-call nunca aparece como texto.
  • Gasta tokens de saída a cada turno.
  • `target_skill` é um `Literal` derivado de `allowed_handoffs` do skill — o
    LLM fica IMPOSSIBILITADO de rotear para um destino inválido (determinismo
    no schema, não no prompt).

⚠️ Estas tools são SINAIS, não efeitos. O `AgentRuntime` (agents/runtime.py)
detecta a chamada em `response.tool_calls`, seta o flag correspondente no
AgentState (`handoff_to` / `escalate` / `end_conversation`) e **NÃO** executa a
tool como uma tool normal (não há side-effect a rodar). O coroutine abaixo só
existe porque `StructuredTool` exige um callable e como guarda caso alguém
execute por engano — retorna um ack inócuo.

Rede de segurança: o parser de marcadores (`_parse_handoff`/`_parse_escalate`/
`_parse_end` em _base.py) permanece ativo. Se o LLM emitir o marcador antigo em
texto (ou for um prompt custom de tenant que ainda ensina markers), o parser
captura. Tool-call vence; marcador é o fallback. Em ambos os casos o texto é
limpo antes de ir ao cliente. NÃO falhamos estritamente quando a tool não é
chamada — princípio 10.1 (cliente nunca vê erro / nunca fica abandonado).
"""
from __future__ import annotations

from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, create_model

# Nomes das tools de fluxo — o runtime usa este conjunto para distinguir SINAL
# de EFEITO ao varrer `response.tool_calls`. Mantenha em sincronia com os
# `name=` das factories abaixo.
HANDOFF_TOOL_NAME = "transferir_para_especialidade"
ESCALATE_TOOL_NAME = "transferir_para_atendente"
END_TOOL_NAME = "encerrar_atendimento"

FLOW_CONTROL_TOOL_NAMES: frozenset[str] = frozenset(
    {HANDOFF_TOOL_NAME, ESCALATE_TOOL_NAME, END_TOOL_NAME}
)


# ── Schemas estáticos ─────────────────────────────────────────────────────────

class _EscalateInput(BaseModel):
    reason: str = Field(
        description=(
            "Motivo curto da transferência para um atendente humano "
            "(ex.: 'cliente pediu atendente', 'emergência médica', "
            "'reclamação fora do escopo'). Uso interno — não vai ao cliente."
        )
    )


class _EndInput(BaseModel):
    # Sem campos: o fim de atendimento não carrega payload. Mantemos um schema
    # vazio explícito para o LLM saber que a tool não recebe argumentos.
    pass


# ── Factories ─────────────────────────────────────────────────────────────────

def make_handoff_tool(allowed_targets: tuple[str, ...]) -> StructuredTool | None:
    """Tool de handoff INTERNO entre especialidades.

    `allowed_targets` vem de `skills_registry.allowed_handoffs_for(skill)`. O
    schema fixa `target_skill` num `Literal` com exatamente esses valores — o
    LLM não consegue inventar destino. Skill sem destinos válidos (lista vazia)
    NÃO recebe a tool (retorna None) — não pode fazer handoff.
    """
    if not allowed_targets:
        return None

    # Literal dinâmico a partir dos destinos permitidos. Literal[tuple(...)] é
    # válido e equivale a Literal["a","b",...]. create_model gera o args_schema
    # com a constraint de enum no JSON Schema que o provider recebe.
    target_type = Literal[allowed_targets]  # type: ignore[valid-type]
    HandoffInput = create_model(
        "HandoffInput",
        target_skill=(
            target_type,
            Field(description=(
                "Especialidade interna que deve assumir/complementar a resposta. "
                "Valores possíveis: " + ", ".join(allowed_targets) + "."
            )),
        ),
        context=(
            str,
            Field(default="", description=(
                "Contexto a passar para a outra especialidade (ex.: nome do "
                "medicamento, sintoma, 'cliente confirmou finalização'). Curto."
            )),
        ),
    )

    async def _run(target_skill: str, context: str = "") -> str:  # noqa: ARG001
        # Sinal — o runtime intercepta antes de executar. Ack inócuo.
        return "HANDOFF_SIGNAL"

    return StructuredTool.from_function(
        coroutine=_run,
        name=HANDOFF_TOOL_NAME,
        description=(
            "Passa a condução da conversa para outra ESPECIALIDADE INTERNA "
            "(invisível ao cliente — para ele você é a mesma pessoa). Use quando "
            "o assunto sai da sua especialidade: ex. o cliente nomeou um produto "
            "para comprar, pediu um genérico, ou confirmou o fechamento do "
            "pedido. NÃO escreva texto de despedida — a outra especialidade "
            "continua a MESMA resposta. Não anuncie a transferência ao cliente."
        ),
        args_schema=HandoffInput,
    )


def make_escalate_tool() -> StructuredTool:
    """Tool de transferência para ATENDENTE HUMANO (escalation)."""

    async def _run(reason: str) -> str:  # noqa: ARG001
        return "ESCALATE_SIGNAL"

    return StructuredTool.from_function(
        coroutine=_run,
        name=ESCALATE_TOOL_NAME,
        description=(
            "Transfere o atendimento para um ATENDENTE HUMANO. Use SOMENTE "
            "quando: o cliente pedir explicitamente ('quero falar com "
            "atendente', 'humano', 'balcão'); houver emergência médica; ou "
            "houver reclamação grave fora do seu escopo. NÃO use para compra/"
            "dúvida normal que você consegue resolver. Antes de chamar, diga ao "
            "cliente UMA frase curta de transição (ex.: 'Vou te passar para um "
            "atendente'). A transferência real acontece pela tool — uma frase "
            "sozinha NÃO transfere nada."
        ),
        args_schema=_EscalateInput,
    )


def make_end_tool() -> StructuredTool:
    """Tool de FIM DE ATENDIMENTO (cliente se despediu, sem pendência)."""

    async def _run() -> str:
        return "END_SIGNAL"

    return StructuredTool.from_function(
        coroutine=_run,
        name=END_TOOL_NAME,
        description=(
            "Encerra o atendimento. Use quando o cliente sinalizar que terminou "
            "e NÃO há pedido pendente para anotar nem nada a transferir ('era só "
            "isso', 'obrigado, mais nada', 'tchau', 'valeu'). Antes de chamar, "
            "escreva uma despedida curta e cordial. NÃO use se há itens não "
            "anotados, se o cliente pediu atendente, ou se ainda há dúvida "
            "aberta."
        ),
        args_schema=_EndInput,
    )


def make_flow_control_tools(allowed_handoffs: tuple[str, ...]) -> list[StructuredTool]:
    """Conjunto de tools de fluxo para um skill.

    Inclui escalate + end sempre; handoff só se o skill tem destinos válidos.
    O AgentRuntime usa `FLOW_CONTROL_TOOL_NAMES` para tratá-las como sinais.
    """
    tools: list[StructuredTool] = [make_escalate_tool(), make_end_tool()]
    handoff = make_handoff_tool(allowed_handoffs)
    if handoff is not None:
        tools.insert(0, handoff)
    return tools
