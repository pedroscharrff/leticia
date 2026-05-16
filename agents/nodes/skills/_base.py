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

# Skills permitidos como destino de handoff
_VALID_HANDOFF_TARGETS = {
    "farmaceutico", "principio_ativo", "genericos",
    "vendedor", "recuperador", "saudacao",
}


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


def _persona_prefix(persona: dict) -> str:
    """Monta instrução de persona a partir do dicionário carregado do DB."""
    if not persona:
        persona = {}
    # Aceita tanto o esquema novo (tenant_persona) quanto chaves antigas
    name     = persona.get("agent_name") or persona.get("name") or "Assistente"
    tone     = persona.get("tone") or "amigável"
    lang     = persona.get("language") or "português brasileiro"
    pharmacy = persona.get("pharmacy_name") or ""
    bio      = persona.get("persona_bio") or ""
    response_length = persona.get("response_length") or "short"
    playbook = persona.get("conversation_playbook") or ""
    extra    = persona.get("custom_instructions") or persona.get("extra_instructions") or ""

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
    if bio:
        parts.append(bio)
    parts.append(f"Tom: {tone}. Idioma: {lang}.")
    if response_length == "short":
        parts.append("Tamanho preferido: respostas curtas (1-3 frases).")
    elif response_length == "medium":
        parts.append("Tamanho preferido: respostas em até 2 parágrafos curtos.")

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


def _build_messages(state: AgentState, system_prompt: str) -> list:
    """Constrói lista de mensagens LangChain com histórico."""
    lc_messages = [SystemMessage(content=system_prompt)]

    for msg in state.get("messages", []):
        if msg["role"] == "user":
            lc_messages.append(HumanMessage(content=msg["content"]))
        elif msg["role"] == "assistant":
            lc_messages.append(AIMessage(content=msg["content"]))

    lc_messages.append(HumanMessage(content=state.get("current_message", "")))
    return lc_messages


async def run_skill(
    state: AgentState,
    llm_factory,
    skill_name: str,
    base_system: str,
) -> AgentState:
    """
    Executa um skill genérico.

    Args:
        state:        Estado atual do grafo.
        llm_factory:  Callable(skill_name) → LLM — injetado pelo graph_builder.
        skill_name:   Nome do skill para lookup de prompt customizado.
        base_system:  System prompt padrão do skill.
    """
    persona            = state.get("persona", {})
    skill_prompts      = state.get("skill_prompts", {})
    skill_instructions = state.get("skill_instructions", {})
    trace              = list(state.get("trace_steps", []))
    handoff_context    = state.get("handoff_context", "")
    prev_skill         = (state.get("skill_history") or [None])[-1] if state.get("skill_history") else None
    prev_response      = state.get("final_response", "") if prev_skill and prev_skill != skill_name else ""

    # Monta system prompt: persona + prompt customizado (se houver) + base + extra instructions
    parts = []
    persona_txt = _persona_prefix(persona)
    if persona_txt:
        parts.append(persona_txt)

    # Prompt do skill — custom (tenant) substitui o base; senão usa base
    custom_prompt = skill_prompts.get(skill_name, "")
    parts.append(custom_prompt or base_system)

    # extra_instructions específicas deste skill (camada do dono da farmácia)
    skill_extra = skill_instructions.get(skill_name, "")
    if skill_extra:
        parts.append(
            f"[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA — sobreponha qualquer "
            f"comportamento padrão]\n{skill_extra}"
        )

    # Se este skill recebeu um handoff, injeta o contexto e a resposta anterior
    if prev_response and prev_skill and prev_skill != skill_name:
        parts.append(
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

    system_prompt = "\n\n".join(parts)
    messages = _build_messages(state, system_prompt)

    try:
        # Passa o nome do skill para permitir overrides por skill (SkillOverride)
        llm = llm_factory(skill_name)
        from llm.retry import llm_retry
        async for attempt in llm_retry():
            with attempt:
                response = await llm.ainvoke(messages)
        final_response = _extract_text(response.content)

    except Exception as exc:
        log.error("skill.failed", skill=skill_name, exc=str(exc))
        final_response = (
            "Desculpe, tive uma dificuldade técnica agora. "
            "Pode repetir sua pergunta? Estou aqui para ajudar."
        )

    # Parseia marcador de handoff (se houver) — apenas se este skill ainda não recebeu handoff
    handoff_target: str | None = None
    handoff_ctx_new = ""
    is_receiving_handoff = bool(prev_response)
    if not is_receiving_handoff:
        final_response, handoff_target, handoff_ctx_new = _parse_handoff(final_response)

    # Se está recebendo handoff, concatena: resposta anterior + nova resposta
    if is_receiving_handoff and final_response and final_response.strip():
        final_response = f"{prev_response.strip()}\n\n{final_response.strip()}"
    elif is_receiving_handoff:
        final_response = prev_response  # fallback se LLM não respondeu nada

    skill_history = list(state.get("skill_history", []))
    skill_history.append(skill_name)
    handoff_count = state.get("handoff_count", 0)

    import time as _time
    trace.append({
        "node": f"skill:{skill_name}",
        "ts_ms": int(_time.time() * 1000),
        "data": {
            "chars": len(final_response or ""),
            "handoff_to": handoff_target,
        },
    })

    return {
        **state,
        "final_response":  final_response,
        "trace_steps":     trace,
        "handoff_to":      handoff_target,
        "handoff_context": handoff_ctx_new,
        "handoff_count":   handoff_count + (1 if handoff_target else 0),
        "skill_history":   skill_history,
        # Atualiza selected_skill para refletir o skill que efetivamente respondeu
        "selected_skill":  handoff_target or state.get("selected_skill", skill_name),
    }
