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

import asyncio
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


# ── Resiliência da PRIMEIRA chamada do loop ──────────────────────────────────
# SPEC 08 §retry / invariante #2: a instância `ChatModel` é cacheada (lru_cache em
# `get_llm`) e fica IDLE entre turnos; o pool httpx interno envelhece e a PRIMEIRA
# chamada após o idle estoura `APIConnectionError`. A nota antiga da SPEC dizia que
# o tool-loop não precisava de retry ("cada iter já é nova chamada") — errado para a
# 1ª iteração, que não tem iteração anterior pra ter reaberto a conexão. Sintoma real
# (jun/2026): turno do vendedor caía no fallback "tive uma dificuldade técnica" e o
# cliente via o bot pedir pra repetir uma msg que ele acabara de mandar.
#
# Só reentramos em erros TRANSIENTES de conexão/timeout/5xx — erros de lógica
# (bad request, schema de tool inválido) sobem na hora, sem mascarar. Detecção por
# NOME da exceção pra não acoplar a openai/anthropic. NÃO é gated por tier: idle
# aging atinge qualquer provider (Anthropic/GPT/DeepSeek/Gemini).
_TRANSIENT_EXC_NAMES = {
    "APIConnectionError",
    "APITimeoutError",
    "InternalServerError",
    "APIError",
}


async def _ainvoke_resilient(invoke_fn: Callable[[], Awaitable]):
    """Invoca um LLM reentrando só em erros transientes de conexão (até 3x)."""
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            return await invoke_fn()
        except Exception as exc:  # noqa: BLE001
            if type(exc).__name__ not in _TRANSIENT_EXC_NAMES:
                raise
            last_exc = exc
            log.warning("runtime.llm_transient_retry",
                        attempt=attempt + 1, exc=str(exc))
            if attempt < 2:
                await asyncio.sleep(min(2 * (2 ** attempt), 10))
    assert last_exc is not None
    raise last_exc


# ── Resultado ─────────────────────────────────────────────────────────────────

@dataclass
class RuntimeResult:
    """Saída do tool-loop. O skill monta o AgentState a partir disto."""
    final_text: str = ""
    tool_calls_trace: list[dict] = field(default_factory=list)
    iters_used: int = 0
    last_tool_result: str = ""
    # Resultados COMPLETOS das tools de domínio do turno — list[{"name","full"}].
    # Em memória, NÃO vai pro trace (o `result_preview` do trace é truncado em
    # 300 chars e o formato do buscar_produto põe o cabeçalho+INSTRUÇÃO INTERNA
    # (~296 chars) ANTES das linhas de produto, então o preview corta justamente
    # antes dos `R$ X.XX`). Os guards de preço/grounding precisam do texto inteiro.
    domain_tool_results: list[dict] = field(default_factory=list)
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
    """Config do andaime de força-busca (force-recall) para LLM fraca quando
    EXISTE catálogo (`sales.stock_check` ON — Sheets OU ERP). Cf. SPEC 10
    §força-busca de estoque.

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


@dataclass
class ClaimGrounding:
    """Config do andaime de grounding de FATO FARMACOLÓGICO para LLM fraca.
    Cf. SPEC 10 §Grounding de fato farmacológico.

    Problema que resolve: a LLM fraca VOLUNTARIA, de memória, um genérico /
    princípio ativo / composição que NÃO veio de nenhuma tool deste turno
    (ex.: "o genérico do Benegripe é Dipirona + Clorfeniramina + Cafeína", sem
    ter chamado tool). Nem o `availability_guard` (cruza NOME DE PRODUTO) nem o
    force-recall (afirmação de disponibilidade / preço-fantasma) pegam — a fala
    não afirma estoque nem cita preço, afirma COMPOSIÇÃO.

    `source_tools`: tools que ANCORAM um fato farmacológico (a base de referência
        ou o catálogo). Se uma delas estiver bindada, o andaime FORÇA a consulta
        do termo e regenera; senão, cai numa fala segura (sem despejar o fato).
    """
    source_tools: tuple[str, ...] = ("consultar_medicamento_referencia", "buscar_produto")


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
        # Guarda o resultado COMPLETO (não-truncado) p/ os guards de preço/grounding.
        result.domain_tool_results.append({"name": tc["name"], "full": str(out)})
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
    skill só fornece quando weak + há catálogo / `sales.stock_check` ON). Cf. SPEC 10.

    Defense-in-depth: após a busca forçada, `_search_results_this_turn` fica
    populado, então o `safety_guard` downstream ainda cobre o caso de o modelo
    insistir em afirmar um produto que voltou sem match.
    """
    from services.availability_guard import affirms_or_offers_availability
    from services.price_guard import extract_prices

    txt = result.final_text or ""
    if not txt.strip():
        return

    called = {tc.get("name") for tc in result.tool_calls_trace}
    if called & set(stock_recall.suppress_tools):
        return  # item já validado (carrinho/pedido) → afirmação legítima

    searched = stock_recall.search_tool in called

    # ── Sinal B: PREÇO-FANTASMA ──────────────────────────────────────────────
    # Preço citado na resposta que NÃO veio de nenhuma busca deste turno. É o
    # caso que escapava: a LLM fraca buscava o produto A (ou nada) e ofertava o
    # produto B — real no mundo, fora do catálogo — com preço da própria memória
    # (ex.: Gemini ofertando "Targifor C por R$ 45"). O `affirms_or_offers` não
    # pega (não precisa de afirmação explícita) e, se houve busca de A, o
    # stand-down "já buscou" deixava passar. Cruza contra os preços que as buscas
    # DESTE turno retornaram (mesmo regex do price_guard — fonte única). Sem busca
    # → qualquer preço citado é fantasma.
    # Falso-positivo só custa um re-prompt de busca (recuperável) — toleramos.
    #
    # ⚠️ Fonte = `domain_tool_results` (resultado COMPLETO), NÃO o `result_preview`
    # do trace: o preview é truncado em 300 chars e o buscar_produto põe o
    # cabeçalho+INSTRUÇÃO INTERNA (~296 chars) ANTES das linhas `• ... R$ X.XX`,
    # então o preview NUNCA contém preço → known_prices vinha sempre vazio →
    # TODO preço real citado virava "fantasma" (falso positivo). Isso forçava
    # re-busca em todo turno com preço; modelo weak que não re-busca (DeepSeek)
    # caía no fallback seguro e "esquecia" o produto. Corrigido 2026-06-26.
    mentioned_prices = extract_prices(txt)
    known_prices: set[float] = set()
    for tr in result.domain_tool_results:
        if tr.get("name") == stock_recall.search_tool:
            for p in extract_prices(str(tr.get("full", ""))):
                known_prices.add(p)
    phantom_price = any(
        not any(abs(v - k) <= 0.01 for k in known_prices) for v in mentioned_prices
    )

    affirms = affirms_or_offers_availability(txt)

    # Quando JÁ buscou neste turno: o guard determinístico cobre a afirmação;
    # só forçamos no caso NOVO de preço-fantasma (produto que a busca não trouxe).
    # Quando NÃO buscou: força se afirmou disponibilidade OU citou preço-fantasma.
    if searched:
        if not phantom_price:
            return  # buscou e nenhum preço fora do catálogo → guard cobre
    else:
        if not affirms and not phantom_price:
            return

    log.warning("runtime.stock_affirmation_without_search",
                search_tool=stock_recall.search_tool,
                searched=searched, phantom_price=phantom_price,
                mentioned_prices=mentioned_prices,
                final_preview=txt[:200])

    lc_messages.append(HumanMessage(content=(
        "[INSTRUÇÃO INTERNA DO SISTEMA — não é o cliente falando]\n"
        "⚠️ Você ofereceu um produto e/ou um PREÇO que não veio do catálogo "
        f"(via `{stock_recall.search_tool}`) neste turno. Você NÃO pode afirmar "
        "disponibilidade nem citar preço sem consultar o catálogo — seu "
        "conhecimento próprio sobre marcas/preços NÃO conta, só o catálogo.\n\n"
        f"AGORA: chame `{stock_recall.search_tool}` para CADA produto que você "
        "mencionou. Use o nome base (sem dosagem). NÃO escreva texto antes — só "
        "a(s) tool call(s)."
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


def _build_turn_evidence(lc_messages: list) -> str:
    """Concatena a evidência ANCORADA do turno: texto das tool results
    (`ToolMessage`) + falas do cliente (`HumanMessage`). É contra isso que o
    grounding cruza os termos farmacológicos da resposta — conteúdo COMPLETO,
    não o `result_preview` truncado em 300 do trace.

    Pula HumanMessages internas (instruções `[INSTRUÇÃO INTERNA…]` re-injetadas
    pelos próprios andaimes) para não "ancorar" um termo só porque o sistema o
    citou numa correção anterior.
    """
    parts: list[str] = []
    for m in lc_messages:
        if isinstance(m, ToolMessage):
            parts.append(str(m.content or ""))
        elif isinstance(m, HumanMessage):
            txt = str(m.content or "")
            if "[INSTRUÇÃO INTERNA" in txt or "[INSTRUCAO INTERNA" in txt:
                continue
            parts.append(txt)
    return "\n".join(parts)


async def _maybe_reground_claims(
    claim_grounding: ClaimGrounding,
    lc_messages: list,
    llm,
    llm_with_tools,
    tools: list,
    result: RuntimeResult,
) -> None:
    """Andaime weak-LLM: se a resposta afirma um FATO FARMACOLÓGICO (genérico /
    princípio ativo / composição) cujo termo NÃO veio de nenhuma tool/fala do
    cliente neste turno, reancora. Roda só quando `claim_grounding` é fornecido
    (skill só fornece quando weak). Cf. SPEC 10 §Grounding de fato farmacológico.

    Remediação:
      • Tem tool de fonte bindada (`source_tools`) → força a consulta do termo e
        regenera "use APENAS o resultado" (mesmo mecanismo do force-recall).
      • Sem tool de fonte (ex.: vendedor) → substitui por fala segura, sem
        despejar o fato inventado.
    """
    from services.grounding_guard import (
        detect_ungrounded_claims, build_grounding_correction,
    )
    from services.referencia_repo import load_reference_lexicon

    txt = result.final_text or ""
    if not txt.strip():
        return

    lexicon = await load_reference_lexicon()
    if not lexicon:
        return  # sem base curada → nada a cruzar (fail-open)

    evidence = _build_turn_evidence(lc_messages)
    issues = detect_ungrounded_claims(txt, evidence, lexicon)
    if not issues:
        return

    terms = [i["term"] for i in issues]
    log.warning("runtime.ungrounded_pharma_claim",
                terms=terms, final_preview=txt[:200])

    available = {t.name for t in tools}
    source_tool = next((s for s in claim_grounding.source_tools if s in available), None)

    # ── Sem tool de fonte (vendedor): fala segura, não despeja o fato ─────────
    if source_tool is None:
        result.final_text = build_grounding_correction(issues)
        log.info("runtime.claim_grounding_safe_reply", terms=terms)
        return

    # ── Tem tool de fonte: força a consulta do(s) termo(s) e regenera ─────────
    termos_str = ", ".join(terms)
    lc_messages.append(HumanMessage(content=(
        "[INSTRUÇÃO INTERNA DO SISTEMA — não é o cliente falando]\n"
        "⚠️ Você afirmou um fato sobre medicamento (genérico / princípio ativo / "
        f"composição) que NÃO veio de nenhuma consulta neste turno: {termos_str}. "
        "Seu conhecimento próprio sobre composição/genérico NÃO conta.\n\n"
        f"AGORA: chame `{source_tool}` para confirmar antes de afirmar. NÃO escreva "
        "texto antes — só a tool call."
    )))
    response2 = await llm_with_tools.ainvoke(lc_messages)

    consulted = any(
        tc.get("name") == source_tool for tc in (response2.tool_calls or [])
    )
    if not consulted:
        # Modelo não consultou nem forçado → não deixamos o fato vazar.
        log.error("runtime.claim_grounding_failed",
                  tool_calls=[tc.get("name") for tc in (response2.tool_calls or [])])
        result.final_text = build_grounding_correction(issues)
        return

    lc_messages.append(response2)
    tool_map = {t.name: t for t in tools}
    for tc in response2.tool_calls:
        rec: dict = {"iter": "claim_grounding", "name": tc.get("name"), "args": tc.get("args")}
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
            lc_messages.append(ToolMessage(content="erro ao consultar", tool_call_id=tc["id"]))
            log.warning("runtime.claim_grounding_tool_failed", exc=str(exc))
        result.tool_calls_trace.append(rec)

    lc_messages.append(HumanMessage(content=(
        f"Agora responda ao cliente em 1-3 frases usando APENAS o que `{source_tool}` "
        "retornou. Se a consulta não confirmou o que você ia dizer, NÃO afirme — diga "
        "que vai confirmar com o atendente. Termine com UMA pergunta."
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
    claim_grounding: ClaimGrounding | None = None,
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

    `stock_recall` / `claim_grounding`: andaimes weak-LLM pós-loop (force-busca de
    estoque e grounding de fato farmacológico). Só fornecidos pelo skill quando o
    modelo é fraco (`needs_tool_scaffolding`). None = caminho forte byte-idêntico.
    """
    result = RuntimeResult()
    try:
        llm_with_tools = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        broke_on_signal = False
        for i in range(max_iters):
            result.iters_used = i + 1
            # 1ª iteração reusa cliente possivelmente idle → APIConnectionError.
            # `_ainvoke_resilient` reabre a conexão em erro transiente (ver nota no
            # topo do módulo). Iterações seguintes já têm conexão quente, mas o
            # wrapper é barato e cobre flakiness transiente mid-loop também.
            response = await _ainvoke_resilient(lambda: llm_with_tools.ainvoke(lc_messages))

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

        # Grounding de fato farmacológico (andaime weak-LLM): roda DEPOIS do
        # force-recall (o force-recall pode ter repopulado a evidência via busca)
        # e ANTES do empty-text fallback. Não roda quando houve sinal de fluxo
        # (handoff/escalate/end): a fala é da transição, não uma afirmação.
        if claim_grounding is not None and not broke_on_signal and not result.handoff_to:
            try:
                await _maybe_reground_claims(
                    claim_grounding, lc_messages, llm, llm_with_tools, tools, result,
                )
            except Exception as exc:  # noqa: BLE001
                # Fail-open: nunca derruba a entrega por causa do andaime.
                log.warning("runtime.claim_grounding_errored", exc=str(exc))

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
