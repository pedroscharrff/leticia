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


@dataclass
class StockRecall:
    """Config do andaime de força-busca (force-recall) para LLM fraca em modo
    ERP (`inventory.track_stock` ON). Cf. SPEC 10 §força-busca de estoque.

    Problema que resolve: a LLM fraca afirma "temos esse remédio" SEM chamar
    `buscar_produto` neste turno. O `availability_guard` NÃO pega esse caso —
    ele cruza a afirmação contra `_search_results_this_turn`, que fica VAZIO
    quando a tool nunca rodou (curto-circuita como "limpo"). Aqui o runtime
    detecta a afirmação não-verificada e FORÇA a busca antes de o cliente ver.

    `search_tool`: nome da tool de catálogo (`buscar_produto`).
    `suppress_tools`: tools de carrinho/pedido que, se rodaram no turno, indicam
        que o item JÁ foi validado antes — não re-forçamos (evita atrito em
        reafirmação de fechamento). Escolha do produto: suprimir nesse caso.
    """
    search_tool: str = "buscar_produto"
    suppress_tools: tuple[str, ...] = ()


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
    gate=None,
) -> None:
    """Executa UMA tool de domínio e encadeia o ToolMessage. Tolerante a falha.

    `gate` (opcional): `async (tc) -> str | None`. Permite VETAR/transformar a
    execução de uma tool ANTES de rodá-la — usado pelo gate determinístico de
    confirmação de pedido (vendedor). Quando retorna string, ela vira o
    tool_result (correção pro modelo) e a tool NÃO executa. None = executa normal.
    """
    rec: dict = {"iter": iter_label, "name": tc.get("name"), "args": tc.get("args")}
    tool = tool_map.get(tc["name"])
    if not tool:
        rec["error"] = "tool_not_found"
        result.tool_calls_trace.append(rec)
        return
    if gate is not None:
        try:
            blocked = await gate(tc)
        except Exception as exc:  # noqa: BLE001
            blocked = None
            log.warning("runtime.tool_gate_failed", name=tc.get("name"), exc=str(exc))
        if blocked is not None:
            rec["gated"] = True
            rec["result_preview"] = str(blocked)[:300]
            lc_messages.append(ToolMessage(content=str(blocked), tool_call_id=tc["id"]))
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


async def _maybe_force_stock_search(
    stock_recall: StockRecall,
    lc_messages: list,
    llm,
    llm_with_tools,
    tools: list,
    result: RuntimeResult,
) -> None:
    """Andaime weak-LLM (ERP): se o modelo AFIRMOU disponibilidade sem ter
    chamado `buscar_produto` neste turno, FORÇA a busca e regenera a resposta
    a partir do resultado real. Roda só quando `stock_recall` é fornecido (o
    skill só fornece quando weak + `inventory.track_stock` ON). Cf. SPEC 10.

    Defense-in-depth: após a busca forçada, `_search_results_this_turn` fica
    populado, então o `safety_guard` downstream ainda cobre o caso de o modelo
    insistir em afirmar um produto que voltou sem match.
    """
    from services.availability_guard import has_unverified_affirmation

    txt = result.final_text or ""
    if not txt.strip() or not has_unverified_affirmation(txt):
        return

    called = {tc.get("name") for tc in result.tool_calls_trace}
    if stock_recall.search_tool in called:
        return  # já buscou neste turno → guard determinístico cobre o resto
    if called & set(stock_recall.suppress_tools):
        return  # item já validado (carrinho/pedido) → afirmação legítima

    log.warning("runtime.stock_affirmation_without_search",
                search_tool=stock_recall.search_tool,
                final_preview=txt[:200])

    lc_messages.append(HumanMessage(content=(
        "[INSTRUÇÃO INTERNA DO SISTEMA — não é o cliente falando]\n"
        "⚠️ Você afirmou que a farmácia TEM um produto sem chamar "
        f"`{stock_recall.search_tool}` neste turno. Em modo de estoque "
        "autoritativo você NÃO pode afirmar disponibilidade sem consultar o "
        "catálogo — o estoque pode ter mudado.\n\n"
        f"AGORA: chame `{stock_recall.search_tool}` para CADA item/medicamento "
        "que você afirmou ter. Use o nome base (sem dosagem). NÃO escreva texto "
        "antes — só a(s) tool call(s)."
    )))
    response2 = await llm_with_tools.ainvoke(lc_messages)

    searched = any(
        tc.get("name") == stock_recall.search_tool
        for tc in (response2.tool_calls or [])
    )
    if not searched:
        # Modelo não buscou nem forçado → não deixamos a afirmação vazar.
        log.error("runtime.stock_recall_failed",
                  tool_calls=[tc.get("name") for tc in (response2.tool_calls or [])])
        result.final_text = (
            "Deixa eu confirmar a disponibilidade certinho antes de te garantir. "
            "Pode me dizer o nome do medicamento que você precisa?"
        )
        return

    lc_messages.append(response2)
    tool_map = {t.name: t for t in tools}
    for tc in response2.tool_calls:
        rec: dict = {"iter": "stock_recall", "name": tc.get("name"), "args": tc.get("args")}
        # Só executamos a tool de busca; qualquer outra (fluxo etc.) recebe ack
        # inócuo — o provider exige um tool_result para cada tool_use.
        if tc.get("name") != stock_recall.search_tool:
            lc_messages.append(ToolMessage(content="ok", tool_call_id=tc["id"]))
            rec["skipped"] = True
            result.tool_calls_trace.append(rec)
            continue
        tool = tool_map.get(tc["name"])
        if tool is None:
            rec["error"] = "tool_not_found"
            lc_messages.append(ToolMessage(content="ok", tool_call_id=tc["id"]))
            result.tool_calls_trace.append(rec)
            continue
        try:
            out = await tool.ainvoke(tc["args"])
            result.last_tool_result = str(out)
            rec["result_preview"] = str(out)[:300]
            lc_messages.append(ToolMessage(content=str(out), tool_call_id=tc["id"]))
        except Exception as exc:  # noqa: BLE001
            rec["error"] = str(exc)
            lc_messages.append(ToolMessage(content="erro ao buscar", tool_call_id=tc["id"]))
            log.warning("runtime.stock_recall_tool_failed", exc=str(exc))
        result.tool_calls_trace.append(rec)

    lc_messages.append(HumanMessage(content=(
        "Agora responda ao cliente em 1-3 frases usando APENAS o que "
        f"`{stock_recall.search_tool}` retornou. Se algum item voltou SEM "
        "resultado, diga honestamente que vai confirmar com o atendente — NÃO "
        "afirme que tem. Termine com UMA pergunta."
    )))
    response3 = await llm.ainvoke(lc_messages)
    result.final_text = _extract_text(response3.content) or result.final_text


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
    domain_tool_gate=None,
    stock_recall: StockRecall | None = None,
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
                await _execute_domain_tool(tc, tool_map, lc_messages, result, result.iters_used,
                                           gate=domain_tool_gate)

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

        # Força-busca de estoque (andaime weak-LLM em modo ERP): roda DEPOIS do
        # post_loop_hook (não conflita — o force-call do vendedor é do
        # pré-atendimento, onde stock_recall é None) e ANTES do empty-text
        # fallback. Não roda quando houve sinal de fluxo (handoff/escalate/end):
        # nesses casos a fala é da transição, não uma afirmação de disponibilidade.
        if stock_recall is not None and not broke_on_signal and not result.handoff_to:
            try:
                await _maybe_force_stock_search(
                    stock_recall, lc_messages, llm, llm_with_tools, tools, result,
                )
            except Exception as exc:  # noqa: BLE001
                # Fail-open: nunca derruba a entrega por causa do andaime.
                log.warning("runtime.stock_recall_errored", exc=str(exc))

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
