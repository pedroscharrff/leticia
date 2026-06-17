"""
Base helper para todos os skill nodes.

Cada skill:
  1. Monta o system prompt (persona + prompt customizado da DB)
  2. Reconstrói as mensagens (histórico + mensagem atual)
  3. Invoca o LLM com retry
  4. Retorna AgentState com final_response atualizado
"""
from __future__ import annotations

import re
import structlog
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from agents.state import AgentState

log = structlog.get_logger()

# Regex para detectar handoff: [[HANDOFF:skill]] ou [[HANDOFF:skill:contexto]]
_HANDOFF_RE = re.compile(r"\[\[HANDOFF:([a-z_]+)(?::([^\]]+))?\]\]", re.IGNORECASE)

# Regex para detectar pedido de escalation humana ([[ESCALATE]]).
_ESCALATE_RE = re.compile(r"\[\[ESCALATE\]\]", re.IGNORECASE)

# Regex para detectar fim de atendimento sinalizado pelo agente ([[END]]).
_END_RE = re.compile(r"\[\[END\]\]", re.IGNORECASE)


def _parse_escalate(response: str) -> tuple[str, bool]:
    """Detecta marcador [[ESCALATE]] na resposta.

    Retorna (resposta_limpa, True) se o agente pediu escalation humana,
    senão (resposta_original, False).
    """
    if not response:
        return response, False
    if _ESCALATE_RE.search(response):
        cleaned = _ESCALATE_RE.sub("", response).strip()
        return cleaned, True
    return response, False


def _parse_end(response: str) -> tuple[str, bool]:
    """Detecta marcador [[END]] na resposta (fim de atendimento).

    Retorna (resposta_limpa, True) se o agente sinalizou que o cliente
    encerrou o atendimento (despedida / "era só isso" sem pedido pendente),
    senão (resposta_original, False). O marcador é SEMPRE removido do texto
    antes de enviar ao cliente.
    """
    if not response:
        return response, False
    if _END_RE.search(response):
        cleaned = _END_RE.sub("", response).strip()
        return cleaned, True
    return response, False


def _extract_text(content) -> str:
    """
    Extrai string limpa de `response.content`.

    LangChain pode retornar content como:
      • str — uso normal
      • list[dict] — Anthropic com tool calls, ex:
        [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]
      • list[str] — fallback
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                # Bloco de texto Anthropic
                if block.get("type") == "text" and block.get("text"):
                    parts.append(block["text"])
                # Pula tool_use, tool_result, etc.
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    return str(content)

# Skills permitidos como destino de handoff — DERIVADO do skills_registry
# (fonte única). Antes era um set hardcoded {farmaceutico, principio_ativo,
# genericos, vendedor, recuperador, saudacao}. Usamos PLAN_GATED_SKILLS (mesmo
# conjunto) para o PARSER de fallback ser PERMISSIVO como antes — limpa/aceita
# qualquer destino plan-gated que um prompt (inclusive custom de tenant) possa
# emitir em texto. O gating ESTRITO por-skill é feito pelo Literal do
# HandoffTool (caminho primário), via allowed_handoffs do registry.
from agents.skills_registry import PLAN_GATED_SKILLS as _PLAN_GATED_SKILLS
_VALID_HANDOFF_TARGETS = set(_PLAN_GATED_SKILLS)


def resolve_skill_tier(llm_factory, role: str) -> tuple[str, str, str]:
    """Resolve (provider, model, tier) do modelo que vai rodar este role.

    `tier` ∈ {"strong","weak"} (ver `llm.model_tier`) decide se aplicamos andaime
    (force-call determinístico, bloco de disciplina de tool) para modelos fracos
    — SEM tocar no caminho dos modelos fortes. Aqui (Fase B) só é gravado no
    trace pra observabilidade; o gating de comportamento vem na Fase C.

    Tolerante: se a factory não expõe `.resolve` (ex.: stubs de teste), assume
    "strong" — default seguro, nunca injeta andaime sob incerteza."""
    try:
        resolve = getattr(llm_factory, "resolve", None)
        if resolve is None:
            return ("", "", "strong")
        provider, model = resolve(role)
        from llm.model_tier import model_tier
        return (provider or "", model or "", model_tier(provider, model))
    except Exception as _exc:  # noqa: BLE001
        log.warning("skill.tier_resolve_failed", role=role, exc=str(_exc))
        return ("", "", "strong")


def _parse_handoff(text: str) -> tuple[str, str | None, str]:
    """
    Extrai marcador [[HANDOFF:skill:contexto]] do texto.

    Returns:
        (texto_sem_marcador, skill_destino_ou_None, contexto)
    """
    match = _HANDOFF_RE.search(text)
    if not match:
        return text, None, ""

    target  = match.group(1).strip().lower()
    context = (match.group(2) or "").strip()

    if target not in _VALID_HANDOFF_TARGETS:
        return text, None, ""

    cleaned = _HANDOFF_RE.sub("", text).strip()
    return cleaned, target, context


# Hints de formalidade/emoji/length — espelham services/persona.py mas vivem
# aqui porque _persona_prefix é o único consumidor real no caminho do agente.
# Se mudar um hint, mude nos dois lugares (e atualize spec 02 §Persona).
_FORMALITY_HINT = {
    "tu":     "Trate o cliente por 'tu'.",
    "voce":   "Trate o cliente por 'você'.",
    "senhor": "Trate o cliente por 'senhor(a)'.",
}
_EMOJI_HINT = {
    "none":     "Não use emojis.",
    "light":    "Use no máximo 1 emoji por mensagem, e só quando agregar.",
    "moderate": "Use até 2 emojis por mensagem para reforçar o tom.",
    "heavy":    "Use emojis livremente para tornar a conversa mais leve.",
}
# Nível de vocabulário (registro técnico↔leigo) e profundidade da explicação.
# Migration 064. _persona_prefix é a única porta de entrada destes campos.
_VOCABULARY_HINT = {
    "leigo":         "Use linguagem simples e cotidiana; evite jargão e traduza "
                     "termos técnicos quando precisar citá-los.",
    "intermediario": "Use linguagem clara; explique o termo técnico na primeira "
                     "vez que aparecer.",
    "tecnico":       "Pode usar terminologia técnica/farmacológica apropriada, "
                     "assumindo que o cliente entende o vocabulário da área.",
}
_DEPTH_HINT = {
    "minima":      "Profundidade: responda o essencial, sem desdobrar detalhes "
                   "não pedidos.",
    "equilibrada": "Profundidade: dê o necessário para a decisão, sem alongar.",
    "detalhada":   "Profundidade: pode detalhar mais quando o assunto exigir, "
                   "mantendo a brevidade por turno.",
}


def _persona_prefix(persona: dict) -> str:
    """Monta instrução de persona a partir do dicionário carregado do DB.

    IMPORTANTE: esta função é a ÚNICA porta de entrada da persona no prompt
    dos skills. Todo campo novo em `public.tenant_persona` que precise afetar
    o comportamento do agente deve ser renderizado aqui. Salvar no DB sem
    renderizar = config "fantasma" (operador edita e nada muda).
    Cf. docs/specs/02-skills.md §Persona — campos suportados.
    """
    if not persona:
        persona = {}
    # Aceita tanto o esquema novo (tenant_persona) quanto chaves antigas
    name      = persona.get("agent_name") or persona.get("name") or "Assistente"
    tone      = persona.get("tone") or "amigável"
    lang      = persona.get("language") or "português brasileiro"
    pharmacy  = persona.get("pharmacy_name") or ""
    tagline   = persona.get("pharmacy_tagline") or ""
    bio       = persona.get("persona_bio") or ""
    gender    = persona.get("agent_gender") or ""
    formality = persona.get("formality") or ""
    emoji     = persona.get("emoji_usage") or ""
    response_length = persona.get("response_length") or "short"
    vocabulary = persona.get("vocabulary_level") or ""
    depth      = persona.get("explanation_depth") or ""
    greeting  = persona.get("greeting_template") or ""
    signature = persona.get("signature") or ""
    playbook  = persona.get("conversation_playbook") or ""
    forbidden = persona.get("forbidden_topics") or ""
    catch     = persona.get("catchphrases") or []
    business_hours   = persona.get("business_hours") or ""
    location         = persona.get("location") or ""
    delivery_info    = persona.get("delivery_info") or ""
    payment_methods  = persona.get("payment_methods") or ""
    website          = persona.get("website") or ""
    instagram        = persona.get("instagram") or ""
    extra = persona.get("custom_instructions") or persona.get("extra_instructions") or ""

    parts = [
        f"Você é {name}" + (f", atendente da {pharmacy}." if pharmacy else "."),
        # ── PERSONA ÚNICA ─────────────────────────────────────────────────
        f"Você é UMA ÚNICA pessoa atendendo o cliente, com várias especialidades "
        f"internas (orientação farmacêutica, vendas, etc). Para o cliente você é "
        f"SEMPRE {name}, uma só pessoa.",
        "REGRAS DE IDENTIDADE:",
        "• NUNCA diga 'vou te passar para o farmacêutico/vendedor'.",
        "• NUNCA fale de si em 3ª pessoa.",
        "• Cliente NÃO sabe que existem agentes internos.",
        # ── CANAIS DE ENTRADA (texto, áudio, imagem) ──────────────────────
        "VOCÊ RECEBE TEXTO, ÁUDIO E IMAGEM:",
        "• O cliente pode mandar mensagem de TEXTO, de ÁUDIO ou IMAGEM/FOTO.",
        "• Áudios chegam já transcritos em texto e você CONSEGUE LER imagens "
        "(foto de receita, caixa/cartela de remédio, exame, etc.). Trate-os "
        "com naturalidade, como se tivesse ouvido o áudio ou visto a imagem — "
        "não diga que 'não consegue ver/ouvir'.",
        "• Se uma imagem vier ilegível/cortada ou um áudio vier vazio ou "
        "truncado, peça gentilmente para o cliente reenviar ou descrever em texto.",
        # ── LINGUAGEM AO FALAR DE FONTES (não citar bases internas) ────────
        "AO FALAR DE INFORMAÇÃO DE MEDICAMENTO:",
        "• NUNCA cite ao cliente as palavras 'bulário' nem 'ANVISA', nem o nome "
        "de bases/órgãos internos que você consultou. Refira-se à origem apenas "
        "como 'fontes oficiais', 'a bula do medicamento' ou 'informações do "
        "fabricante'. (A consulta interna continua igual; só não exponha o termo.)",
        # ── BREVIDADE & CONDUÇÃO ──────────────────────────────────────────
        "═══════════════════════════════════════════════════════════════",
        "REGRAS DE CONVERSAÇÃO (críticas):",
        "═══════════════════════════════════════════════════════════════",
        "• Você CONDUZ um atendimento — passo a passo, como pessoa real.",
        "• NÃO despeje todas as informações de uma vez. Vá descobrindo o que o cliente "
        "precisa, etapa por etapa.",
        "• Cada resposta sua deve ter NO MÁXIMO 3-4 frases curtas.",
        "• Faça SEMPRE apenas UMA pergunta por vez — espere o cliente responder.",
        "• Não liste 3 opções de remédio + dose + horários + alertas tudo junto. "
        "Recomende 1-2 opções com 1 linha cada, e pergunte qual prefere.",
        "• Não misture orientação clínica com info comercial (PIX, fidelidade, entrega) "
        "na mesma resposta. Use cada turno para UMA coisa.",
        "• Só comente disponibilidade/preço quando o cliente escolher o produto.",
    ]
    if tagline:
        parts.append(f"Slogan da farmácia: {tagline}")
    if bio:
        parts.append(bio)

    # ── Estilo (tom, gênero, formalidade, emoji, tamanho, idioma) ─────────
    style_bits: list[str] = [f"Tom: {tone}.", f"Idioma: {lang}."]
    if gender:
        style_bits.append(f"Use concordância de gênero {gender} ao se referir a si.")
    if formality in _FORMALITY_HINT:
        style_bits.append(_FORMALITY_HINT[formality])
    if emoji in _EMOJI_HINT:
        style_bits.append(_EMOJI_HINT[emoji])
    if response_length == "short":
        style_bits.append("Tamanho preferido: respostas curtas (1-3 frases).")
    elif response_length == "medium":
        style_bits.append("Tamanho preferido: respostas em até 2 parágrafos curtos.")
    elif response_length == "long":
        style_bits.append("Tamanho preferido: respostas detalhadas quando o assunto exigir.")
    if vocabulary in _VOCABULARY_HINT:
        style_bits.append(_VOCABULARY_HINT[vocabulary])
    if depth in _DEPTH_HINT:
        style_bits.append(_DEPTH_HINT[depth])
    parts.append(" ".join(style_bits))

    # ── Precedência de voz ────────────────────────────────────────────────────
    # A persona é a autoridade ÚNICA de voz (tom, registro, vocabulário, emojis,
    # tamanho). Os blocos seguintes (prompt do skill) tratam de CONTEÚDO e
    # CONDUTA (regras clínicas, uso de tools, handoff) — se houver conflito de
    # ESTILO entre eles e a persona, a persona vence. Sem esta linha, o estilo
    # hardcoded de cada skill (ex.: farmaceutico) competia e diluía os ajustes
    # do dono da farmácia. Cf. docs/specs/02-skills.md §Persona — precedência.
    parts.append(
        "PRECEDÊNCIA DE ESTILO: as instruções de tom, registro, vocabulário, "
        "uso de emojis e tamanho de resposta definidas ACIMA (persona) são "
        "PRIORITÁRIAS. As seções seguintes definem o que fazer (conteúdo, "
        "regras clínicas, ferramentas), não COMO soar."
    )

    # ── Bordões / saudação / assinatura ──────────────────────────────────
    if isinstance(catch, (list, tuple)) and catch:
        parts.append("Bordões da marca (use com moderação): " + "; ".join(str(c) for c in catch))
    if greeting:
        parts.append(f"Saudação preferida (use no PRIMEIRO contato): {greeting}")
    if signature:
        parts.append(f"Assinatura opcional (no fim de respostas longas): {signature}")

    # ── Contexto da farmácia (loja física + canais) ───────────────────────
    # Bloco estável — vai no prefixo cacheado. Mude no portal → próximo turno
    # invalida o cache, depois cacheia o novo prefixo.
    biz: list[str] = []
    if business_hours:
        biz.append(f"- Horário de atendimento: {business_hours}")
    if location:
        biz.append(f"- Endereço: {location}")
    if delivery_info:
        biz.append(f"- Entregas: {delivery_info}")
    if payment_methods:
        biz.append(f"- Pagamentos aceitos: {payment_methods}")
    if website:
        biz.append(f"- Site: {website}")
    if instagram:
        biz.append(f"- Instagram: {instagram}")
    if biz:
        parts.append("Contexto da farmácia (use quando o cliente perguntar):\n" + "\n".join(biz))

    # ── Tópicos proibidos ────────────────────────────────────────────────
    if forbidden:
        parts.append(
            "TÓPICOS PROIBIDOS — NÃO comente, NÃO opine, NÃO recomende:\n"
            f"{forbidden}\n"
            "Se o cliente puxar esses assuntos, redirecione gentilmente para o "
            "atendimento da farmácia."
        )

    # Playbook customizado pelo dono (fluxo de atendimento)
    if playbook:
        parts.append(
            "═══════════════════════════════════════════════════════════════\n"
            "PLAYBOOK DE ATENDIMENTO (definido pelo dono da farmácia):\n"
            "═══════════════════════════════════════════════════════════════\n"
            f"{playbook}\n"
            "Siga este fluxo. Identifique em qual etapa você está pelo histórico da "
            "conversa e execute APENAS a etapa atual nesta resposta."
        )
    if extra:
        parts.append(f"Instruções extras do dono da farmácia: {extra}")
    return "\n".join(parts)


def _build_messages(
    state: AgentState,
    system_prompt: str,
    provider: str | None = None,
    volatile_prompt: str = "",
) -> list:
    """Constrói lista de mensagens LangChain com histórico.

    `system_prompt`   = bloco ESTÁVEL (regras, persona, instruções). É o maior
                        bloco e re-enviado em TODA chamada (inclusive retries do
                        analyst, segundos depois). Vai no prefixo cacheado.
    `volatile_prompt` = estado por-turno (carrinho, handoff, status de campos).
                        Colocado APÓS o marcador de cache → nunca invalida o
                        prefixo. Skills sem estado volátil deixam vazio.

    provider: "anthropic" liga o cache_control explícito; outros providers
    degradam para SystemMessage simples (OpenAI cacheia automático >=1024 tk).
    Default = settings.default_skill_provider (anthropic no projeto).
    """
    if provider is None:
        try:
            from config import settings
            provider = getattr(settings, "default_skill_provider", "anthropic")
        except Exception:  # noqa: BLE001
            provider = "anthropic"

    try:
        from llm.caching import system_message
        lc_messages = [system_message(system_prompt, provider=provider, volatile=volatile_prompt)]
    except Exception:  # noqa: BLE001
        # Fallback ultra-seguro: nunca deixa o build de mensagens quebrar.
        full = system_prompt
        if volatile_prompt and volatile_prompt.strip():
            full = f"{system_prompt}\n\n{volatile_prompt}"
        lc_messages = [SystemMessage(content=full)]

    # Skip blank entries — Anthropic rejects any message with empty content.
    for msg in state.get("messages", []):
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=content))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=content))

    current = (state.get("current_message", "") or "").strip()
    if not current:
        # Fallback to last non-empty user msg from history, or a placeholder.
        current = next(
            ((m.get("content") or "").strip()
             for m in reversed(state.get("messages", []))
             if m.get("role") == "user" and (m.get("content") or "").strip()),
            "Oi",
        )
        log.warning("skill.current_message_empty.fallback", used=current[:50])
    lc_messages.append(HumanMessage(content=current))
    return lc_messages


async def run_skill(
    state: AgentState,
    llm_factory,
    skill_name: str,
    base_system: str,
    tools: list | None = None,
    *,
    enable_handoff: bool = False,
    enable_escalate: bool = False,
    enable_end: bool = False,
) -> AgentState:
    """
    Executa um skill genérico.

    Args:
        state:        Estado atual do grafo.
        llm_factory:  Callable(skill_name) → LLM — injetado pelo graph_builder.
        skill_name:   Nome do skill para lookup de prompt customizado.
        base_system:  System prompt padrão do skill.
        tools:        Tools de DOMÍNIO do skill (opcional).
        enable_handoff/escalate/end: liga as TOOLS de controle de fluxo + as
            instruções correspondentes no prompt (geradas em prompts/flow.py).
            Default False = comportamento histórico (skill não faz handoff/
            escalation/end via tool). O parser de marcadores continua como rede
            de segurança independentemente destas flags.

    Montagem do prompt via PromptBuilder: persona (porta única _persona_prefix) +
    base/override + flow + extra = ESTÁVEL (prefixo cacheado); memória do cliente,
    tempo, sentimento e continuação de handoff = VOLÁTIL (após o marker de cache).
    Cf. SPEC 08 + [[reference_prompt_caching_volatile_split]].
    """
    from agents.prompts import PromptBuilder
    from agents.skills_registry import allowed_handoffs_for

    persona            = state.get("persona", {})
    skill_prompts      = state.get("skill_prompts", {})
    skill_instructions = state.get("skill_instructions", {})
    trace              = list(state.get("trace_steps", []))
    handoff_context    = state.get("handoff_context", "")
    prev_skill         = (state.get("skill_history") or [None])[-1] if state.get("skill_history") else None
    prev_response      = state.get("final_response", "") if prev_skill and prev_skill != skill_name else ""

    allowed_targets = allowed_handoffs_for(skill_name)

    pb = PromptBuilder(
        persona, skill_name,
        override=skill_prompts.get(skill_name) or None,
        extra=skill_instructions.get(skill_name) or None,
    )
    pb.core(base_system)
    pb.flow(
        allowed_targets,
        handoff=enable_handoff, escalate=enable_escalate, end=enable_end,
    )
    pb.extra_instructions()

    # ── VOLÁTIL (após o marker de cache) ──────────────────────────────────
    # Bloco "o que sabemos sobre este cliente" — só injetado quando a
    # capability `attendance.customer_memory` está ativa para o tenant.
    # É volátil porque muda quando o cliente declara alergia/preferência/etc.
    # Tolerante a falhas: qualquer erro cai em bloco vazio.
    try:
        from services import capabilities as cap_svc
        from services.persona import build_customer_memory_block
        if await cap_svc.is_enabled(state.get("tenant_id"), "attendance.customer_memory"):
            mem_block = build_customer_memory_block(state.get("customer") or {})
            if mem_block:
                pb.volatile(mem_block)
    except Exception as _exc:  # noqa: BLE001
        log.warning("skill.customer_memory_block.failed", exc=str(_exc))

    # Contexto temporal (hora + período) — capability `attendance.time_aware_greeting`.
    # Sempre volátil: o conteúdo muda a cada turno. Quando OFF, bloco vazio.
    try:
        from services import capabilities as cap_svc
        from services.time_context import build_time_context_block
        if await cap_svc.is_enabled(state.get("tenant_id"), "attendance.time_aware_greeting"):
            time_block = build_time_context_block()
            if time_block:
                pb.volatile(time_block)
    except Exception as _exc:  # noqa: BLE001
        log.warning("skill.time_context_block.failed", exc=str(_exc))

    # Orientação de adaptação por sentimento — produzida pelo nó
    # sentiment_analyzer (capability intelligence.sentiment_analysis). É VOLÁTIL
    # (muda a cada turno conforme o humor do cliente) → entra após o marker de
    # cache, nunca invalida o prefixo. Vazia quando a capability está OFF.
    sent_directive = (state.get("sentiment_directive") or "").strip()
    if sent_directive:
        pb.volatile(sent_directive)

    # Se este skill recebeu um handoff, injeta o contexto e a resposta anterior.
    # ESTE BLOCO É VOLÁTIL — depende do skill anterior e do conteúdo da resposta
    # dele. Se for pro prefixo estável, invalida o cache em TODO handoff
    # (farmaceutico→vendedor é o caminho mais comum). Cf. spec 08 §regra de ouro.
    if prev_response and prev_skill and prev_skill != skill_name:
        pb.volatile(
            "[CONTINUAÇÃO INTERNA — não é visível ao cliente]\n"
            f"Você acabou de dizer (como parte da mesma conversa contínua):\n"
            f"\"\"\"\n{prev_response}\n\"\"\"\n"
            f"Agora você deve COMPLEMENTAR essa resposta com sua especialidade.\n"
            + (f"Contexto recebido: {handoff_context}\n" if handoff_context else "")
            + "REGRAS:\n"
            "• NÃO repita o que já foi dito acima — apenas COMPLEMENTE.\n"
            "• Sua resposta será CONCATENADA à anterior — escreva como continuação natural.\n"
            "• NÃO faça outro handoff. NÃO mencione 'sou o vendedor' ou similares.\n"
            "• Aja como a MESMA pessoa que escreveu o trecho acima."
        )

    system_prompt, volatile_prompt = pb.build()
    messages = _build_messages(state, system_prompt, volatile_prompt=volatile_prompt)

    # Monta as tools de fluxo conforme as flags (sinais detectados pelo runtime).
    flow_tools = []
    if enable_handoff and allowed_targets:
        from agents.tools.flow_control import make_handoff_tool
        ht = make_handoff_tool(allowed_targets)
        if ht is not None:
            flow_tools.append(ht)
    if enable_escalate:
        from agents.tools.flow_control import make_escalate_tool
        flow_tools.append(make_escalate_tool())
    if enable_end:
        from agents.tools.flow_control import make_end_tool
        flow_tools.append(make_end_tool())

    all_tools = list(tools or []) + flow_tools

    _node_error: dict | None = None
    tool_calls_trace: list[dict] = []
    iters_used = 0
    # Sinais de fluxo vindos das TOOLS (None/False quando não há tools de fluxo).
    sig_handoff_to: str | None = None
    sig_handoff_ctx = ""
    sig_escalate = False
    sig_end = False

    # llm_factory pode falhar (provider/config inválido) — degradar com
    # mensagem amigável em vez de quebrar o node (princípio 10.1). O loop com
    # tools tem seu próprio try interno (runtime); aqui cobrimos só a criação.
    try:
        llm = llm_factory(skill_name)
        llm_ok = True
    except Exception as exc:
        import traceback as _tb
        _node_error = {
            "type": type(exc).__name__, "msg": str(exc),
            "stack": _tb.format_exc()[-1500:],
        }
        log.error("skill.llm_factory_failed", skill=skill_name, exc=str(exc))
        final_response = (
            "Desculpe, tive uma dificuldade técnica agora. "
            "Pode repetir sua pergunta? Estou aqui para ajudar."
        )
        llm_ok = False

    if not llm_ok:
        pass  # final_response já definido; pula a invocação
    elif all_tools:
        # Tool-loop compartilhado (runtime). Import preguiçoso evita ciclo
        # _base ↔ runtime. O runtime captura tools de domínio E sinais de fluxo.
        from agents.runtime import run_tool_loop
        from config import settings
        result = await run_tool_loop(
            llm, list(messages), all_tools, settings.skill_max_tool_iterations,
        )
        final_response   = result.final_text
        tool_calls_trace = result.tool_calls_trace
        iters_used       = result.iters_used
        _node_error      = result.node_error
        sig_handoff_to   = result.handoff_to
        sig_handoff_ctx  = result.handoff_context
        sig_escalate     = result.escalate
        sig_end          = result.end_conversation
    else:
        # Skill puro (sem tools) — fluxo histórico com retry.
        try:
            from llm.retry import llm_retry
            async for attempt in llm_retry():
                with attempt:
                    response = await llm.ainvoke(messages)
            final_response = _extract_text(response.content)
        except Exception as exc:
            import traceback as _tb
            _node_error = {
                "type":  type(exc).__name__,
                "msg":   str(exc),
                "stack": _tb.format_exc()[-1500:],
            }
            log.error("skill.failed", skill=skill_name,
                      exc=str(exc), error_type=type(exc).__name__)
            final_response = (
                "Desculpe, tive uma dificuldade técnica agora. "
                "Pode repetir sua pergunta? Estou aqui para ajudar."
            )

    # ── Rede de segurança: parser de marcadores ──────────────────────────────
    # SEMPRE rodamos os parsers pra LIMPAR qualquer marcador residual do texto
    # antes de enviar ao cliente (LLM que ainda emite [[...]], ou prompt custom
    # de tenant). Os SINAIS das tools de fluxo têm PRIORIDADE; o marcador só
    # entra como fallback quando a tool não foi chamada.
    handoff_target: str | None = None
    handoff_ctx_new = ""
    is_receiving_handoff = bool(prev_response)
    final_response, parsed_target, parsed_ctx = _parse_handoff(final_response)
    if not is_receiving_handoff:
        handoff_target  = sig_handoff_to or parsed_target
        handoff_ctx_new = sig_handoff_ctx or parsed_ctx

    final_response, parsed_escalate = _parse_escalate(final_response)
    escalate = sig_escalate or parsed_escalate

    # Fim de atendimento ([[END]] OU tool). SEMPRE limpamos o marcador do texto;
    # o flag só é propagado quando NÃO estamos roteando handoff nem escalando
    # (essas têm prioridade — já finalizam).
    final_response, parsed_end = _parse_end(final_response)
    end_conversation = sig_end or parsed_end
    if handoff_target or escalate:
        end_conversation = False

    # Se está recebendo handoff, concatena: resposta anterior + nova resposta
    if is_receiving_handoff and final_response and final_response.strip():
        final_response = f"{prev_response.strip()}\n\n{final_response.strip()}"
    elif is_receiving_handoff:
        final_response = prev_response  # fallback se LLM não respondeu nada

    skill_history = list(state.get("skill_history", []))
    skill_history.append(skill_name)
    handoff_count = state.get("handoff_count", 0)

    # Tier do modelo que rodou este skill — observabilidade (Fase B). Permite
    # correlacionar "% turnos sem tool" com strong/weak na análise. O gating de
    # andaime (Fase C) lê o mesmo tier.
    _prov, _mdl, _tier = resolve_skill_tier(llm_factory, skill_name)

    import time as _time
    _trace_data: dict = {
        "chars": len(final_response or ""),
        "handoff_to": handoff_target,
        "escalate": escalate,
        "model": f"{_prov}:{_mdl}" if _mdl else None,
        "tier": _tier,
    }
    if all_tools:
        _trace_data["iters"] = iters_used
        _trace_data["tool_calls"] = tool_calls_trace
    if _node_error:
        _trace_data["error"] = _node_error
    trace.append({
        "node": f"skill:{skill_name}",
        "ts_ms": int(_time.time() * 1000),
        "data": _trace_data,
    })

    out: AgentState = {
        **state,
        "final_response":  final_response,
        "trace_steps":     trace,
        "handoff_to":      handoff_target,
        "handoff_context": handoff_ctx_new,
        "handoff_count":   handoff_count + (1 if handoff_target else 0),
        "skill_history":   skill_history,
        # Atualiza selected_skill para refletir o skill que efetivamente respondeu
        "selected_skill":  handoff_target or state.get("selected_skill", skill_name),
        "end_conversation": end_conversation,
        # Tier do modelo do skill — disponível p/ downstream (Fase C: gating de andaime).
        "model_tier": _tier,
    }
    # Só propaga escalate quando ligado para este skill — não sobrescreve o flag
    # de outros caminhos (guardrails/vendedor) com False.
    if enable_escalate:
        out["escalate"] = escalate
    return out
