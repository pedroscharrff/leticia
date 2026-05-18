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

_FALLBACK_SKILL = "farmaceutico"

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
    available_skills = state.get("available_skills", [_FALLBACK_SKILL])
    current_message  = state.get("current_message", "")
    messages         = state.get("messages", [])

    if not available_skills:
        available_skills = [_FALLBACK_SKILL]

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
        skill      = parsed.get("skill", _FALLBACK_SKILL)
        confidence = float(parsed.get("confidence", 0.5))
        intent     = parsed.get("intent", current_message[:100])

        # Garante que a skill está disponível
        if skill not in available_skills:
            log.warning(
                "orchestrator.skill_unavailable",
                skill=skill,
                available=available_skills,
            )
            skill = _FALLBACK_SKILL

    except Exception as exc:
        log.error("orchestrator.failed", exc=str(exc))
        skill, confidence, intent = _FALLBACK_SKILL, 0.0, current_message[:100]

    log.info(
        "orchestrator.routed",
        skill=skill,
        confidence=round(confidence, 2),
        intent=intent[:60],
    )

    import time as _time
    trace = list(state.get("trace_steps", []))
    trace.append({
        "node": "orchestrator",
        "ts_ms": int(_time.time() * 1000),
        "data": {
            "skill": skill,
            "confidence": round(confidence, 2),
            "intent": intent[:120],
        },
    })

    return {
        **state,
        "selected_skill": skill,
        "confidence":     confidence,
        "intent":         intent,
        "trace_steps":    trace,
    }
