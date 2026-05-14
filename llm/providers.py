"""
LLM provider factory.

Returns a LangChain chat model ready for async invocation.
All models are constructed with the same timeout so callers don't
need to worry about provider-specific configuration.

Supported providers:
  'anthropic' — Claude models via Anthropic API
  'google'    — Gemini models via Google Generative AI
  'openai'    — GPT models via OpenAI API
  'ollama'    — Any model running locally via Ollama (self-hosted)

Two usage modes:
  get_llm()            — uses platform API keys (credits mode), cached
  get_llm_for_tenant() — uses tenant's own API key (BYOK mode), not cached
"""
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.language_models import BaseChatModel

from config import settings


def _build_llm(
    provider: str,
    model: str,
    api_key: str | None,
    base_url: str | None,
) -> BaseChatModel:
    timeout = settings.llm_timeout_seconds
    # Low temperature: pharmacy customer service requires consistent,
    # deterministic answers. High temp = more "creative" hallucinations.
    temp = settings.llm_temperature

    if provider == "anthropic":
        return ChatAnthropic(
            model=model,
            api_key=api_key or settings.anthropic_api_key,
            timeout=timeout,
            temperature=temp,
            max_retries=0,  # retries handled by llm_retry()
        )

    if provider == "google":
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key or settings.google_api_key,
            request_options={"timeout": timeout},
            temperature=temp,
            max_retries=0,
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=model,
            api_key=api_key or settings.openai_api_key,
            timeout=timeout,
            temperature=temp,
            max_retries=0,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model,
            base_url=base_url or settings.ollama_base_url,
            timeout=timeout,
            temperature=temp,
        )

    raise ValueError(f"Unknown LLM provider: {provider!r}")


@lru_cache(maxsize=32)
def get_llm(provider: str, model: str) -> BaseChatModel:
    """
    Platform-key factory — cached per (provider, model).
    Used when tenant is in 'credits' mode.
    """
    return _build_llm(provider, model, api_key=None, base_url=None)


def get_llm_for_tenant(
    provider: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> BaseChatModel:
    """
    BYOK factory — uses the tenant's own API key.
    Not cached; each call creates a fresh client.
    """
    return _build_llm(provider, model, api_key=api_key, base_url=base_url)


# ── Canonical model identifiers used across the codebase ─────────────────────

HAIKU = ("anthropic", "claude-haiku-4-5-20251001")
SONNET = ("anthropic", "claude-sonnet-4-6")
GEMINI_FLASH = ("google", "gemini-2.0-flash")
GPT4O_MINI = ("openai", "gpt-4o-mini")
GPT4O = ("openai", "gpt-4o")
OLLAMA_LLAMA = ("ollama", "llama3.2")
