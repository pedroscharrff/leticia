"""
Helpers for tenant persona + per-skill prompt overrides.

Used by:
  - Celery worker (to feed TenantConfig before building the LangGraph)
  - Admin/portal routers (read/write CRUD)
"""
from __future__ import annotations

from typing import Any

from db.postgres import get_db_conn


PERSONA_DEFAULTS: dict[str, Any] = {
    "agent_name": "Atendente",
    "agent_gender": "feminino",
    "pharmacy_name": None,
    "pharmacy_tagline": None,
    "tone": "amigavel",
    "formality": "voce",
    "emoji_usage": "light",
    "response_length": "medium",
    "language": "pt-BR",
    "persona_bio": None,
    "greeting_template": None,
    "signature": None,
    "custom_instructions": None,
    "forbidden_topics": None,
    "catchphrases": [],
    "business_hours": None,
    "location": None,
    "delivery_info": None,
    "payment_methods": None,
    "website": None,
    "instagram": None,
}


async def load_persona(tenant_id: str) -> dict:
    """Returns full persona row (with defaults filled in) for a tenant."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenant_persona WHERE tenant_id = $1",
            tenant_id,
        )
    if not row:
        return dict(PERSONA_DEFAULTS)
    out = dict(PERSONA_DEFAULTS)
    for k in PERSONA_DEFAULTS:
        if k in row.keys() and row[k] is not None:
            out[k] = row[k]
    return out


async def load_skill_prompts(tenant_id: str) -> dict[str, dict]:
    """
    Returns {skill_name: {"system_prompt": str|None, "extra_instructions": str|None}}.
    """
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT skill_name, system_prompt, extra_instructions
            FROM public.tenant_skill_prompts
            WHERE tenant_id = $1
            """,
            tenant_id,
        )
    return {
        r["skill_name"]: {
            "system_prompt": r["system_prompt"],
            "extra_instructions": r["extra_instructions"],
        }
        for r in rows
    }


# ── Prompt builder ────────────────────────────────────────────────────────────

_TONE_HINT = {
    "formal":        "Tom formal e respeitoso. Sem gírias.",
    "amigavel":      "Tom amigável, próximo, acolhedor.",
    "informal":      "Tom descontraído, como uma conversa entre amigos.",
    "profissional":  "Tom profissional e objetivo.",
    "divertido":     "Tom leve e bem-humorado, sem perder a credibilidade.",
}
_FORMALITY_HINT = {
    "tu":     "Trate o cliente por 'tu'.",
    "voce":   "Trate o cliente por 'você'.",
    "senhor": "Trate o cliente por 'senhor(a)'.",
}
_EMOJI_HINT = {
    "none":     "Não use emojis.",
    "light":    "Use no máximo 1 emoji por mensagem, quando fizer sentido.",
    "moderate": "Use até 2 emojis por mensagem para reforçar o tom.",
    "heavy":    "Use emojis livremente para tornar a conversa mais leve.",
}
_LENGTH_HINT = {
    "short":  "Respostas curtas, no máximo 2 frases.",
    "medium": "Respostas em até 3 parágrafos curtos.",
    "long":   "Respostas detalhadas quando o assunto exigir.",
}


_PERSONA_MEANINGFUL_KEYS = (
    "agent_name", "pharmacy_name", "persona_bio", "tone", "formality",
    "emoji_usage", "response_length", "business_hours", "location",
    "delivery_info", "payment_methods", "catchphrases", "greeting_template",
    "custom_instructions", "forbidden_topics", "signature",
)


def build_persona_block(persona: dict) -> str:
    """Build the persona section that's prepended to every skill's system prompt."""
    if not persona or not any(persona.get(k) for k in _PERSONA_MEANINGFUL_KEYS):
        return ""

    parts: list[str] = ["## Persona do atendente"]

    name = persona.get("agent_name") or "Atendente"
    pharmacy = persona.get("pharmacy_name")
    bio = persona.get("persona_bio")

    intro = f"Você é {name}"
    if pharmacy:
        intro += f", atendente da {pharmacy}"
    intro += "."
    parts.append(intro)
    if bio:
        parts.append(bio.strip())

    style: list[str] = []
    if persona.get("tone") in _TONE_HINT:
        style.append(_TONE_HINT[persona["tone"]])
    if persona.get("formality") in _FORMALITY_HINT:
        style.append(_FORMALITY_HINT[persona["formality"]])
    if persona.get("emoji_usage") in _EMOJI_HINT:
        style.append(_EMOJI_HINT[persona["emoji_usage"]])
    if persona.get("response_length") in _LENGTH_HINT:
        style.append(_LENGTH_HINT[persona["response_length"]])
    if style:
        parts.append("**Estilo:** " + " ".join(style))

    biz: list[str] = []
    if persona.get("business_hours"):
        biz.append(f"Horário: {persona['business_hours']}")
    if persona.get("location"):
        biz.append(f"Endereço: {persona['location']}")
    if persona.get("delivery_info"):
        biz.append(f"Entregas: {persona['delivery_info']}")
    if persona.get("payment_methods"):
        biz.append(f"Pagamentos: {persona['payment_methods']}")
    if persona.get("website"):
        biz.append(f"Site: {persona['website']}")
    if persona.get("instagram"):
        biz.append(f"Instagram: {persona['instagram']}")
    if biz:
        parts.append("**Contexto da farmácia:**\n" + "\n".join(f"- {b}" for b in biz))

    catch = persona.get("catchphrases") or []
    if catch:
        parts.append("**Bordões da marca (use com moderação):** " + "; ".join(catch))

    if persona.get("greeting_template"):
        parts.append(f"**Saudação preferida:** {persona['greeting_template']}")

    if persona.get("custom_instructions"):
        parts.append("**Instruções extras:**\n" + persona["custom_instructions"].strip())

    if persona.get("forbidden_topics"):
        parts.append("**Tópicos proibidos:**\n" + persona["forbidden_topics"].strip())

    if persona.get("signature"):
        parts.append(f"**Assinatura (opcional, no fim de respostas longas):** {persona['signature']}")

    parts.append(
        "Mantenha SEMPRE essa identidade. Nunca diga que é uma IA, "
        "robô ou assistente automatizado, a menos que o cliente pergunte diretamente."
    )

    return "\n\n".join(parts)
