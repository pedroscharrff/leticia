"""
Node: orchestrator

Classifica a intenção do cliente e decide qual skill usar.
Usa o modelo leve (Haiku/Flash) para baixa latência.
"""
from __future__ import annotations

import json
import re
import structlog

from agents.state import AgentState

log = structlog.get_logger()

_SYSTEM = """\
Você é o orquestrador de um sistema MULTI-AGENTE de farmácia. Sua função é classificar
a intenção do cliente e direcioná-lo para o PRIMEIRO agente certo. Os agentes podem
passar a bola entre si depois, então NÃO se preocupe em escolher "o agente final" —
escolha o agente que deve LIDERAR a resposta.

Skills disponíveis:
{skills_list}

REGRAS CRÍTICAS — leia com atenção:

1. **SINTOMAS / dor / mal-estar** ("dor de cabeça", "estou com febre", "tô gripado",
   "preciso de algo pra X") → SEMPRE farmaceutico
   (o farmacêutico avalia e depois passa para o vendedor verificar estoque)

2. **PRODUTO ESPECÍFICO já nomeado** ("vocês têm Dipirona?", "quanto custa Tylenol?",
   "tem Amoxicilina em estoque?") → vendedor
   (o cliente já sabe o que quer; vendedor consulta estoque/preço)

3. **PRINCÍPIO ATIVO** ("qual o princípio ativo do Tylenol?", "Aspirina contém AAS?")
   → principio_ativo

4. **GENÉRICOS / SUBSTITUIÇÕES** ("tem genérico de X?", "qual o mais barato similar?")
   → genericos

5. **DÚVIDAS FARMACÊUTICAS** (posologia, interações, efeitos colaterais, bulas)
   → farmaceutico

6. **SAUDAÇÕES / PRIMEIRO CONTATO** ("oi", "bom dia", "tudo bem?") → saudacao

7. **RECUPERAÇÃO** (cliente que sumiu, voltou depois) → recuperador

8. **OFF-TOPIC / EMERGÊNCIA / CONTEÚDO IMPRÓPRIO** → guardrails

Exemplos:
• "Oi" / "Olá" / "Bom dia" (sem histórico de conversa) → saudacao
• "Tudo bem?" / "Boa tarde, tá aí?" → saudacao
• "Estou com dor de cabeça" → farmaceutico (sintoma → farmacêutico recomenda)
• "Você tem Dipirona?" → vendedor (produto nomeado → vendedor checa estoque)
• "Pra dor de cabeça, vocês têm Dipirona ou Paracetamol?" → farmaceutico
  (combina sintoma + nomes; farmacêutico assume liderança)
• "Quanto custa o Paracetamol 750mg?" → vendedor

Responda APENAS com JSON válido, sem explicações:
{{"skill": "<skill_name>", "confidence": <0.0-1.0>, "intent": "<resumo da intenção em português>"}}

Se nenhuma skill disponível atender, use "farmaceutico" como fallback.\
"""

_HARD_FALLBACK_SKILL = "farmaceutico"


def _resolve_fallback_skill(
    available: list[str],
    skill_history: list[str] | None = None,
) -> str:
    """
    Escolhe o skill de fallback respeitando o que o tenant tem ATIVO.

    Prioridade:
      1. Último skill usado nesta conversa (continuidade) — evita o caso
         clássico: cliente confirma pedido com "pode finalizar", o LLM do
         orchestrator dá timeout, fallback hardcoded vai pra farmaceutico,
         farmaceutico não tem tool de pedido, LLM alucina sucesso.
      2. Hard fallback `farmaceutico` se ele estiver disponível.
      3. Primeiro skill disponível (caso tenant só tenha vendedor, p.ex.).

    Importante: se o tenant tem só um agente (ex.: só `vendedor` no plano
    básico, ou só `saudacao`), o fallback PRECISA ser esse agente — caso
    contrário o roteamento manda para um node que não existe no grafo.
    """
    if not available:
        return _HARD_FALLBACK_SKILL
    # 1) Continuidade da conversa: último skill ainda disponível
    if skill_history:
        for prev in reversed(skill_history):
            if prev in available:
                return prev
    # 2) Hard fallback padrão
    if _HARD_FALLBACK_SKILL in available:
        return _HARD_FALLBACK_SKILL
    # 3) Primeiro disponível
    return available[0]

# Saudações puras — se a mensagem for SÓ um cumprimento e não houver histórico,
# pulamos o LLM e mandamos direto para `saudacao` (quando disponível).
_GREETING_RE = re.compile(
    r"^\s*(oi+|ol[aá]+|e[ai]+|hey+|hi+|hello+|bom\s*dia|boa\s*tarde|boa\s*noite|"
    r"tudo\s*bem|tudo\s*bom|td\s*bem|td\s*bom|opa+|salve+|menina|moça|moco|"
    r"alo+|al[oô]+)"
    r"[\s!?.,😊🙂👋]*$",
    re.IGNORECASE,
)


def _is_pure_greeting(text: str) -> bool:
    if not text:
        return False
    # Limita a mensagens curtas: ninguém escreve "oi tudo bem queria saber X" e
    # quer ir pra saudacao.
    if len(text.strip()) > 30:
        return False
    return bool(_GREETING_RE.match(text.strip()))


def _build_skills_list(available: list[str]) -> str:
    descriptions = {
        "saudacao":        "recepção, saudações iniciais, primeiro contato, mensagens ambíguas",
        "farmaceutico":    "dúvidas farmacêuticas, bulas, posologia, interações, sintomas",
        "principio_ativo": "identificar princípio ativo de medicamentos",
        "genericos":       "buscar alternativas genéricas / similares",
        "vendedor":        "compras, preços, consulta de estoque, carrinho, pedidos",
        "recuperador":     "reengajamento de clientes inativos",
        "guardrails":      "off-topic, emergências médicas, conteúdo impróprio",
    }
    lines = []
    for skill in available:
        desc = descriptions.get(skill, skill)
        lines.append(f"- {skill}: {desc}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict:
    """Tenta extrair JSON do texto mesmo com texto extra ao redor."""
    # Tenta direto
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Tenta extrair bloco JSON com regex
    match = re.search(r'\{[^{}]+\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return {}


async def orchestrator(state: AgentState, llm_factory) -> AgentState:
    """
    Classifica a intenção e seleciona a skill.
    llm_factory é injetado pelo graph_builder via functools.partial.
    """
    available_skills = state.get("available_skills") or []
    current_message  = state.get("current_message", "")
    messages         = state.get("messages", [])

    if not available_skills:
        available_skills = [_HARD_FALLBACK_SKILL]

    fallback_skill = _resolve_fallback_skill(
        available_skills,
        skill_history=state.get("skill_history") or [],
    )

    # Fast-path: tenant rodando com UM ÚNICO agente (atendimento simples).
    # Pular o LLM de classificação economiza ~300-800ms e o custo do call;
    # e — crucialmente — evita o orquestrador escolher um skill que NÃO
    # existe no grafo (ex.: ele decide "farmaceutico" mas o tenant só tem
    # "vendedor" ativo). Guardrails segue sendo aplicado pelos próprios
    # skills via [[HANDOFF:guardrails]] quando necessário.
    non_safety = [s for s in available_skills if s != "guardrails"]
    if len(non_safety) == 1:
        only_skill = non_safety[0]
        log.info("orchestrator.single_skill_fast_path", skill=only_skill)
        import time as _time
        trace = list(state.get("trace_steps", []))
        trace.append({
            "node": "orchestrator",
            "ts_ms": int(_time.time() * 1000),
            "data": {
                "skill": only_skill,
                "confidence": 1.0,
                "intent": "tenant com agente único",
                "fast_path": "single_skill",
            },
        })
        return {
            **state,
            "selected_skill": only_skill,
            "confidence":     1.0,
            "intent":         current_message[:120],
            "trace_steps":    trace,
        }

    # Fast-path: saudação pura sem histórico → vai direto pra saudacao
    has_history = bool(messages)
    if (
        "saudacao" in available_skills
        and not has_history
        and _is_pure_greeting(current_message)
    ):
        log.info("orchestrator.fast_path_greeting", message=current_message[:40])
        import time as _time
        trace = list(state.get("trace_steps", []))
        trace.append({
            "node": "orchestrator",
            "ts_ms": int(_time.time() * 1000),
            "data": {
                "skill": "saudacao",
                "confidence": 1.0,
                "intent": "saudação inicial",
                "fast_path": True,
            },
        })
        return {
            **state,
            "selected_skill": "saudacao",
            "confidence":     1.0,
            "intent":         "saudação inicial",
            "trace_steps":    trace,
        }

    # Monta contexto resumido (últimas 4 trocas)
    history_text = ""
    for msg in messages[-8:]:
        role = "Cliente" if msg["role"] == "user" else "Assistente"
        history_text += f"{role}: {msg['content']}\n"

    system_prompt = _SYSTEM.format(skills_list=_build_skills_list(available_skills))

    user_content = ""
    if history_text:
        user_content = f"Histórico recente:\n{history_text}\n"
    user_content += f"Nova mensagem do cliente: {current_message}"

    try:
        llm = llm_factory("orchestrator")
        from langchain_core.messages import SystemMessage, HumanMessage
        response = await llm.ainvoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_content),
        ])

        # Garante string (response.content pode ser lista de blocos)
        content = response.content
        if not isinstance(content, str):
            content = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b)
                for b in (content or [])
            )
        parsed = _extract_json(content)
        skill      = parsed.get("skill", fallback_skill)
        confidence = float(parsed.get("confidence", 0.5))
        intent     = parsed.get("intent", current_message[:100])

        # Guardrails é sempre permitido (safety net), mesmo se não estiver
        # explicitamente em available_skills do tenant.
        if skill not in available_skills and skill != "guardrails":
            log.warning(
                "orchestrator.skill_unavailable",
                skill=skill,
                available=available_skills,
                fallback=fallback_skill,
            )
            skill = fallback_skill

    except Exception as exc:
        # Captura o erro real pro trace step (linha ~280). Sem isso o turno
        # fica indistinguível de routing legítimo e ninguém descobre por que
        # o orchestrator caiu em fallback.
        import traceback as _tb
        _node_error = {
            "type":  type(exc).__name__,
            "msg":   str(exc),
            "stack": _tb.format_exc()[-1500:],
        }
        log.error("orchestrator.failed",
                  exc=str(exc), error_type=type(exc).__name__)
        skill, confidence, intent = fallback_skill, 0.0, current_message[:100]

    log.info(
        "orchestrator.routed",
        skill=skill,
        confidence=round(confidence, 2),
        intent=intent[:60],
    )

    import time as _time
    trace = list(state.get("trace_steps", []))
    _trace_data: dict = {
        "skill": skill,
        "confidence": round(confidence, 2),
        "intent": intent[:120],
    }
    # Sinaliza explicitamente que esse routing veio do fallback de exceção,
    # não de uma classificação real do LLM.
    _node_error_val = locals().get("_node_error")
    if _node_error_val:
        _trace_data["error"]    = _node_error_val
        _trace_data["fallback"] = "exception"
    trace.append({
        "node": "orchestrator",
        "ts_ms": int(_time.time() * 1000),
        "data": _trace_data,
    })

    return {
        **state,
        "selected_skill": skill,
        "confidence":     confidence,
        "intent":         intent,
        "trace_steps":    trace,
    }
