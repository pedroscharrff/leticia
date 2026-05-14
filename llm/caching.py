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


def system_message(content: str, *, provider: str) -> SystemMessage:
    """
    Return a SystemMessage that opts into provider-native prompt caching
    when the provider supports it.

    Falls back to a plain string SystemMessage when caching does not apply
    (OpenAI auto-caches; Google/Ollama have no equivalent we wire up here).
    """
    if provider == "anthropic":
        # Anthropic content-block form with explicit cache_control marker.
        # `ephemeral` = ~5 minute TTL, the only public tier today.
        return SystemMessage(content=[
            {
                "type": "text",
                "text": content,
                "cache_control": {"type": "ephemeral"},
            }
        ])

    # OpenAI's prompt cache is automatic for prefixes >= 1024 tokens.
    # Google Gemini / Ollama: no marker needed.
    return SystemMessage(content=content)
