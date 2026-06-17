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
from langchain_google_genai import (
    ChatGoogleGenerativeAI,
    HarmBlockThreshold,
    HarmCategory,
)
from langchain_core.language_models import BaseChatModel

from config import settings
from llm.usage_tracking import TokenUsageCallback


# Callback singleton — stateless, lê ContextVars do turno. Anexado a TODO model
# construído por essa factory pra captura uniforme de tokens.
_USAGE_CB = TokenUsageCallback()


# ── Gemini safety settings ───────────────────────────────────────────────────
# Os filtros de segurança DEFAULT do Gemini bloqueiam conteúdo sobre
# medicamentos/dosagens (cai em HARM_CATEGORY_DANGEROUS_CONTENT) → o modelo
# devolve resposta vazia / candidate bloqueado, que vira fallback técnico pro
# cliente. Num atendimento de FARMÁCIA isso derruba o núcleo do produto.
#
# Relaxamos as 4 categorias que o Gemini efetivamente aceita configurar (as
# legadas — MEDICAL, VIOLENCE, etc. — são rejeitadas pela API). A segurança real
# do domínio NÃO depende do filtro do provider: já temos persona.forbidden_topics,
# os safety_guards pós-LLM (SPEC 10) e a temperatura baixa. Cf. SPEC 08.
_GEMINI_SAFETY_SETTINGS = {
    HarmCategory.HARM_CATEGORY_HARASSMENT:        HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_HATE_SPEECH:       HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT: HarmBlockThreshold.BLOCK_NONE,
    HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT: HarmBlockThreshold.BLOCK_NONE,
}


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
            callbacks=[_USAGE_CB],
        )

    if provider == "google":
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key or settings.google_api_key,
            request_options={"timeout": timeout},
            temperature=temp,
            max_retries=0,
            safety_settings=_GEMINI_SAFETY_SETTINGS,
            callbacks=[_USAGE_CB],
        )

    if provider == "openai":
        from langchain_openai import ChatOpenAI
        # Reasoning models (o1/o3/o4 family) reject the temperature parameter.
        is_reasoning = model.startswith(("o1", "o3", "o4"))
        extra = {} if is_reasoning else {"temperature": temp}
        return ChatOpenAI(
            model=model,
            api_key=api_key or settings.openai_api_key,
            timeout=timeout,
            max_retries=0,
            callbacks=[_USAGE_CB],
            **extra,
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama
        return ChatOllama(
            model=model,
            base_url=base_url or settings.ollama_base_url,
            timeout=timeout,
            temperature=temp,
            callbacks=[_USAGE_CB],
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
GEMINI_FLASH = ("google", "gemini-2.5-flash")  # 2.0 foi descontinuado na API (404)
# GPT-4o family (128K ctx)
GPT4O_MINI = ("openai", "gpt-4o-mini")
GPT4O = ("openai", "gpt-4o")
# GPT-4.1 family (1M ctx)
GPT41_NANO = ("openai", "gpt-4.1-nano")
GPT41_MINI = ("openai", "gpt-4.1-mini")
GPT41 = ("openai", "gpt-4.1")
# GPT-5 family (400K ctx)
GPT5_NANO = ("openai", "gpt-5-nano")
GPT5_MINI = ("openai", "gpt-5-mini")
GPT5 = ("openai", "gpt-5")
# GPT-5.4 family (1M ctx, frontier)
GPT54_MINI = ("openai", "gpt-5.4-mini")
GPT54 = ("openai", "gpt-5.4")
# GPT-5.5 — latest flagship (1M ctx)
GPT55 = ("openai", "gpt-5.5")
# Reasoning models (o-series, 200K ctx) — sem temperature
O3_MINI = ("openai", "o3-mini")
O3 = ("openai", "o3")
O4_MINI = ("openai", "o4-mini")
OLLAMA_LLAMA = ("ollama", "llama3.2")
