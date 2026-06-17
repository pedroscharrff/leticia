"""
agents/runtime.py

`AgentRuntime` — o tool-loop COMPARTILHADO dos skills.

Antes, dois lugares reimplementavam o mesmo loop de tool-calling:
  • `_base.py::_invoke_with_tools`  (skills via run_skill)
  • `vendedor.py`                    (loop inline + force-call + fallback)
Duplicação = bug arrumado num lado só. Aqui mora a lógica comum:

  1. tool-loop até `max_iters` (executa tools de DOMÍNIO, encadeia ToolMessages)
  2. detecção das tools de FLUXO (handoff/escalate/end) — SINAIS, não efeitos:
     não executa side-effect, captura o destino/flag e encerra o turno
  3. empty-text fallback (força resposta textual se o LLM só fez tool call)
  4. captura de erro → dict pro trace step
  5. `post_loop_hook` opcional — onde o vendedor pluga force-call e draft-fallback

Hibridismo handoff: este runtime captura o sinal vindo da TOOL de fluxo. O
parser de marcadores (`_parse_*` em _base.py) continua rodando DEPOIS, no skill,
como rede de segurança (LLM que ainda emite `[[HANDOFF]]` em texto, ou prompt
custom de tenant). Tool-call vence; marcador é fallback. Nunca falha estrito.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable

import structlog
from langchain_core.messages import HumanMessage, ToolMessage

from agents.nodes.skills._base import _extract_text
from agents.tools.flow_control import (
    FLOW_CONTROL_TOOL_NAMES,
    HANDOFF_TOOL_NAME,
    ESCALATE_TOOL_NAME,
    END_TOOL_NAME,
)

log = structlog.get_logger()


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass
class RuntimeResult:
    """Saída do tool-loop. O skill monta o AgentState a partir disto."""
    final_text: str = ""
    tool_calls_trace: list[dict] = field(default_factory=list)
    iters_used: int = 0
    last_tool_result: str = ""
    # Sinais capturados das TOOLS de fluxo (None/False quando não chamadas).
    handoff_to: str | None = None
    handoff_context: str = ""
    escalate: bool = False
    escalate_reason: str | None = None
    end_conversation: bool = False
    # Erro capturado (vai pro trace step do skill).
    node_error: dict | None = None

    def called_tool(self, name: str) -> bool:
        """True se uma tool de DOMÍNIO `name` rodou (qualquer iteração)."""
        return any(tc.get("name") == name for tc in self.tool_calls_trace)


# Assinatura do hook pós-loop: recebe (lc_messages, llm, llm_with_tools, tools,
# result) e pode mutar `result` (ex.: force-call). Retorna None.
PostLoopHook = Callable[..., Awaitable[None]]


# ── Helpers de tool execution ─────────────────────────────────────────────────

def _split_tool_calls(tool_calls: list[dict]) -> tuple[list[dict], list[dict]]:
    """Separa tool_calls em (domínio, fluxo). Fluxo = tools de ciclo de vida."""
    domain, flow = [], []
    for tc in tool_calls:
        (flow if tc.get("name") in FLOW_CONTROL_TOOL_NAMES else domain).append(tc)
    return domain, flow


def _apply_flow_signal(tc: dict, result: RuntimeResult) -> None:
    """Traduz uma chamada de tool de fluxo em sinal no RuntimeResult."""
    name = tc.get("name")
    args = tc.get("args") or {}
    if name == HANDOFF_TOOL_NAME:
        result.handoff_to = (args.get("target_skill") or "").strip() or None
        result.handoff_context = (args.get("context") or "").strip()
    elif name == ESCALATE_TOOL_NAME:
        result.escalate = True
        result.escalate_reason = "flow_tool_escalate"
    elif name == END_TOOL_NAME:
        result.end_conversation = True


async def _execute_domain_tool(
    tc: dict, tool_map: dict, lc_messages: list, result: RuntimeResult, iter_label,
) -> None:
    """Executa UMA tool de domínio e encadeia o ToolMessage. Tolerante a falha."""
    rec: dict = {"iter": iter_label, "name": tc.get("name"), "args": tc.get("args")}
    tool = tool_map.get(tc["name"])
    if not tool:
        rec["error"] = "tool_not_found"
        result.tool_calls_trace.append(rec)
        return
    try:
        out = await tool.ainvoke(tc["args"])
        result.last_tool_result = str(out)
        rec["result_preview"] = result.last_tool_result[:300]
        lc_messages.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))
    except Exception as exc:  # noqa: BLE001
        rec["error"] = str(exc)
        log.warning("runtime.tool_failed", name=tc.get("name"), exc=str(exc))
    result.tool_calls_trace.append(rec)


def _ack_flow_tool(tc: dict, lc_messages: list, result: RuntimeResult, iter_label) -> None:
    """Tools de fluxo são SINAIS — não executam. Mas o provider exige um
    tool_result para cada tool_use no histórico; anexamos um ack inócuo e
    registramos no trace."""
    lc_messages.append(ToolMessage(content="ok", tool_call_id=tc["id"]))
    result.tool_calls_trace.append({
        "iter": iter_label, "name": tc.get("name"),
        "args": tc.get("args"), "flow_signal": True,
    })


# ── Loop principal ────────────────────────────────────────────────────────────

async def run_tool_loop(
    llm,
    lc_messages: list,
    tools: list,
    max_iters: int,
    *,
    empty_text_fallback: bool = True,
    post_loop_hook: PostLoopHook | None = None,
    defer_premature_flow: bool = False,
) -> RuntimeResult:
    """Roda o tool-loop e devolve um RuntimeResult.

    `tools` deve incluir tanto as tools de domínio quanto as de fluxo
    (handoff/escalate/end) já bindadas. O loop distingue pelos nomes.

    `empty_text_fallback` força uma resposta textual se o turno terminou só com
    tool calls (comportamento histórico de _invoke_with_tools).

    `post_loop_hook` roda APÓS o loop e ANTES do empty-text fallback — é onde o
    vendedor pluga o force-call de `anotar_pedido_balcao` e a extração de
    rascunho. Pode mutar o RuntimeResult.

    `defer_premature_flow` (andaime p/ modelos fracos em tool-calling, ex. Gemini):
    quando o modelo dispara uma tool de fluxo (handoff/escalate/end) JUNTO com
    tools de domínio no MESMO turno, ele quase sempre errou — buscou a info E
    pediu transferência ao mesmo tempo. Honrar o fluxo aqui descartaria o
    resultado recém-buscado e encerraria o turno com texto genérico (sintoma
    real medido em prod com Gemini). Com a flag ON, nesse caso o sinal de fluxo é
    ADIADO: executa as tools de domínio, dá ack inócuo no tool_use de fluxo e
    CONTINUA o loop, devolvendo o resultado pro modelo responder. O fluxo só é
    honrado quando vier SOZINHO (intenção real). Default False = comportamento
    histórico (Claude/OpenAI não precisam). Gated por
    `llm.model_tier.needs_tool_scaffolding`.
    """
    result = RuntimeResult()
    try:
        llm_with_tools = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        broke_on_signal = False
        for i in range(max_iters):
            result.iters_used = i + 1
            response = await llm_with_tools.ainvoke(lc_messages)

            if not response.tool_calls:
                result.final_text = _extract_text(response.content)
                break

            # Anexa a resposta do assistant (com as tool_calls) ANTES de
            # encadear os ToolMessages — o provider precisa ver suas próprias
            # tool_calls no histórico. (SPEC 02 §Não fazer.)
            lc_messages.append(response)
            domain, flow = _split_tool_calls(response.tool_calls)

            for tc in domain:
                await _execute_domain_tool(tc, tool_map, lc_messages, result, result.iters_used)

            # Guarda anti-confusão (Gemini/weak): fluxo + domínio no mesmo turno →
            # adia o fluxo, deixa o modelo responder com o que buscou.
            if defer_premature_flow and domain and flow:
                for tc in flow:
                    _ack_flow_tool(tc, lc_messages, result, result.iters_used)
                    result.tool_calls_trace[-1]["deferred_flow"] = True
                log.info("runtime.flow_deferred",
                         flow=[tc.get("name") for tc in flow],
                         domain=[tc.get("name") for tc in domain])
                continue  # próximo turno: modelo responde usando o resultado

            for tc in flow:
                _apply_flow_signal(tc, result)
                _ack_flow_tool(tc, lc_messages, result, result.iters_used)

            # Sinal de fluxo encerra o turno: o texto desta resposta é a fala do
            # skill (handoff: a outra especialidade complementa; escalate/end:
            # despedida/transição). Não continuamos o loop.
            if flow:
                result.final_text = _extract_text(response.content)
                broke_on_signal = True
                break
        else:
            # Excedeu max_iters — força resposta sem tools (resposta parcial).
            response = await llm.ainvoke(lc_messages)
            result.final_text = _extract_text(response.content)

        # Hook específico do skill (force-call, draft-fallback) — pode mutar result.
        if post_loop_hook is not None:
            await post_loop_hook(
                lc_messages=lc_messages, llm=llm,
                llm_with_tools=llm_with_tools, tools=tools, result=result,
            )

        # Empty-text fallback: se o turno terminou só com tool call e sem texto,
        # força uma fala curta ao cliente. Pulamos quando há handoff (a outra
        # especialidade fala) — espelha o comportamento histórico.
        need_text = not (result.final_text or "").strip()
        if empty_text_fallback and need_text and not result.handoff_to and not broke_on_signal:
            lc_messages.append(HumanMessage(content=(
                "Responda agora em texto curto (1-3 frases) ao cliente, usando as "
                "informações que você acabou de consultar. Termine com UMA pergunta."
            )))
            response = await llm.ainvoke(lc_messages)
            result.final_text = _extract_text(response.content)

    except Exception as exc:  # noqa: BLE001
        import traceback as _tb
        result.node_error = {
            "type": type(exc).__name__,
            "msg": str(exc),
            "stack": _tb.format_exc()[-1500:],
        }
        log.error("runtime.failed", exc=str(exc), error_type=type(exc).__name__)
        if not (result.final_text or "").strip():
            err = str(exc).lower()
            if "rate" in err or "429" in err or "overload" in err:
                result.final_text = (
                    "Estou com muita demanda nesse momento. Pode me mandar de novo "
                    "em alguns segundos?"
                )
            else:
                result.final_text = (
                    "Desculpe, tive uma dificuldade técnica agora. Pode repetir sua "
                    "última mensagem? Estou aqui para ajudar."
                )

    return result
