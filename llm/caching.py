"""
Provider-aware prompt-caching helpers.

Why caching matters here:
    System prompts are LARGE and STABLE per (tenant, skill):
      persona block + skill prompt + tenant examples + extras
    They are re-sent on every turn. Caching them cuts cost and latency
    drastically — typical savings:
        Anthropic: 90% on cached input tokens, ~50% on first-token latency
        OpenAI:    50% on cached input tokens (automatic >=1024 tokens)
        Google:    requires Vertex Cached Content API (skipped for now)
        Ollama:    not applicable (local inference)

Anthropic requires explicit `cache_control` markers on message content blocks.
OpenAI is fully automatic — we just keep the system prompt stable and large
enough to qualify (>=1024 tokens, which all our skills already exceed).

Usage:
    from llm.caching import system_message
    msgs = [system_message(prompt, provider=self.PROVIDER), HumanMessage(...)]
"""
from __future__ import annotations

from langchain_core.messages import SystemMessage


def system_message(content: str, *, provider: str, volatile: str = "") -> SystemMessage:
    """
    Return a SystemMessage that opts into provider-native prompt caching
    when the provider supports it.

    `content`  = STABLE prefix (rules, persona, skill prompt, config blocks).
                 Identical turn-to-turn and across conversations of the same
                 tenant → this is what gets cached.
    `volatile` = per-turn state (cart, handoff continuation, customer status).
                 Placed AFTER the cache breakpoint so it never invalidates the
                 cached prefix. Optional.

    Why the split matters:
        Anthropic caches by PREFIX. If anything before the cache_control marker
        changes, the cache misses. Concatenating the cart into `content` would
        bust the cache on every add-to-cart. Keeping volatile state in a second
        block (without a marker) keeps the expensive prefix cached.

    Falls back to a plain string SystemMessage when caching does not apply
    (OpenAI auto-caches; Google/Ollama have no equivalent we wire up here).
    """
    if provider == "anthropic":
        # Anthropic content-block form with explicit cache_control marker.
        # `ephemeral` = ~5 minute TTL, the only public tier today.
        # The marker caches everything up to and including this block (tools +
        # this stable text). The volatile block after it is re-read each turn.
        blocks: list[dict] = [
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if volatile and volatile.strip():
            blocks.append({"type": "text", "text": volatile})
        return SystemMessage(content=blocks)

    # OpenAI's prompt cache is automatic for prefixes >= 1024 tokens.
    # Google Gemini / Ollama: no marker needed. Concatenate stable + volatile.
    full = content if not (volatile and volatile.strip()) else f"{content}\n\n{volatile}"
    return SystemMessage(content=full)
