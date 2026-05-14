"""
Per-tenant few-shot examples for skills.

Tenants curate "look, when X comes in, the ideal response is Y" pairs.
The graph injects up to N relevant examples into the skill's system prompt
at run time, so the LLM has concrete templates to follow.

Selection strategy:
    1. Fuzzy-rank examples by trigram similarity to the current message.
    2. If similarity tie, prefer higher `weight`.
    3. Cap at MAX_EXAMPLES (avoid blowing the prompt budget).
"""
from __future__ import annotations

from db.postgres import get_db_conn

MAX_EXAMPLES = 5


async def load_skill_examples(
    tenant_id: str,
    skill_name: str,
    current_message: str | None = None,
    limit: int = MAX_EXAMPLES,
) -> list[dict]:
    """
    Returns ranked examples for a (tenant, skill).

    If `current_message` is provided, ranks by trigram similarity to it,
    so the most relevant examples bubble up. Otherwise returns highest
    weight first.
    """
    limit = max(1, min(limit, MAX_EXAMPLES))

    if current_message:
        sql = """
            SELECT user_message, ideal_response, tags, weight,
                   similarity(user_message, $2) AS sim
              FROM public.tenant_skill_examples
             WHERE tenant_id = $1
               AND skill_name = $3
               AND enabled = TRUE
          ORDER BY sim DESC, weight DESC, created_at DESC
             LIMIT $4
        """
        params = (tenant_id, current_message, skill_name, limit)
    else:
        sql = """
            SELECT user_message, ideal_response, tags, weight, 0.0 AS sim
              FROM public.tenant_skill_examples
             WHERE tenant_id = $1
               AND skill_name = $2
               AND enabled = TRUE
          ORDER BY weight DESC, created_at DESC
             LIMIT $3
        """
        params = (tenant_id, skill_name, limit)

    async with get_db_conn() as conn:
        rows = await conn.fetch(sql, *params)

    return [dict(r) for r in rows]


def format_examples_block(examples: list[dict]) -> str:
    """
    Render examples as a markdown block to be appended to the system prompt.

    Returns empty string when there are no examples so callers can skip
    the section entirely.
    """
    if not examples:
        return ""

    lines = [
        "## Exemplos curados por esta farmácia",
        "Estes são exemplos de respostas que esta farmácia considera IDEAIS.",
        "Use o estilo, o tom e a estrutura como referência. Não copie",
        "literalmente — adapte ao contexto da conversa atual.",
        "",
    ]
    for i, ex in enumerate(examples, 1):
        tags = ex.get("tags") or []
        tag_str = f"  *(tags: {', '.join(tags)})*" if tags else ""
        lines.append(f"### Exemplo {i}{tag_str}")
        lines.append(f"**Cliente:** {ex['user_message'].strip()}")
        lines.append(f"**Resposta ideal:** {ex['ideal_response'].strip()}")
        lines.append("")

    return "\n".join(lines).rstrip()
