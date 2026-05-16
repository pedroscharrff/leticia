"""
Helper to load a tenant's LLM configuration and resolve credentials.

Returns a dict ready to be unpacked into TenantConfig fields.
"""
from __future__ import annotations

import structlog

from db.postgres import get_db_conn
from services.secrets import decrypt

log = structlog.get_logger()

# Models that belong to OpenAI and cannot be used with other providers
_OPENAI_MODEL_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "chatgpt-")
# Models that belong to Anthropic
_ANTHROPIC_MODEL_PREFIXES = ("claude-",)

# Cheap/fast default per provider, used when the configured model is
# incompatible with the resolved provider (e.g. BYOK switches provider
# but the model column still has a model from the old provider).
_PROVIDER_DEFAULT_FAST = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai":    "gpt-4o-mini",
    "google":    "gemini-2.0-flash",
}


def _detect_provider_from_key(api_key: str | None) -> str | None:
    """Infer provider from API key prefix when the DB column is empty."""
    if not api_key:
        return None
    if api_key.startswith("sk-ant-"):
        return "anthropic"
    if api_key.startswith(("sk-proj-", "sk-")):
        return "openai"
    if api_key.startswith("AIza"):
        return "google"
    return None


def _ensure_model_compatible(provider: str, model: str) -> str:
    """Return model if it is compatible with provider, else a safe default."""
    if provider == "anthropic" and model.startswith(_OPENAI_MODEL_PREFIXES):
        return _PROVIDER_DEFAULT_FAST["anthropic"]
    if provider == "openai" and model.startswith(_ANTHROPIC_MODEL_PREFIXES):
        return _PROVIDER_DEFAULT_FAST["openai"]
    if provider == "google" and (
        model.startswith(_OPENAI_MODEL_PREFIXES) or model.startswith(_ANTHROPIC_MODEL_PREFIXES)
    ):
        return _PROVIDER_DEFAULT_FAST["google"]
    return model


async def load_tenant_llm_config(tenant_id: str) -> dict:
    """
    Returns dict ready to be unpacked into TenantConfig.

    Two layers of customization, both available regardless of mode:
      - mode='byok': tenant brings their own API key (decrypted here)
      - model selection (orchestrator/analyst/skill): tenant picks the
        model tier on every mode, falling back to platform defaults

    Returned keys:
        llm_mode, llm_api_key, llm_base_url,
        orchestrator_provider, orchestrator_model,
        analyst_provider,      analyst_model,
        default_skill_provider, default_skill_model,
    """
    from config import settings

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenant_llm_config WHERE tenant_id = $1",
            tenant_id,
        )

    # No row at all → pure platform defaults
    if not row:
        return {"llm_mode": "credits", "llm_api_key": None, "llm_base_url": None}

    is_byok = row["mode"] == "byok"

    api_key: str | None = None
    if is_byok and row["api_key_enc"]:
        try:
            api_key = decrypt(bytes(row["api_key_enc"]))
        except ValueError:
            # Chave de criptografia trocada ou dado corrompido → fallback para credits
            log.warning("llm_config.decrypt_failed", tenant_id=tenant_id)
            is_byok = False

    # Provider override only applies in BYOK; in credits mode each node uses
    # the platform's curated provider so we don't try to call OpenAI with
    # an Anthropic platform key.
    #
    # If the tenant set mode=byok but left provider NULL, auto-detect from
    # the key prefix (sk-ant-* → anthropic, sk-* → openai, AIza* → google).
    # This prevents cross-provider 401 errors when the key is injected into
    # a node configured for a different provider.
    if is_byok:
        provider_override = row["provider"] or _detect_provider_from_key(api_key)
    else:
        provider_override = None

    def _resolve(provider: str, model_col: str | None, default_model: str) -> tuple[str, str]:
        model = model_col or default_model
        return provider, _ensure_model_compatible(provider, model)

    orch_p, orch_m = _resolve(
        provider_override or settings.default_orchestrator_provider,
        row["orchestrator_model"],
        settings.default_orchestrator_model,
    )
    analyst_p, analyst_m = _resolve(
        provider_override or settings.default_analyst_provider,
        row["analyst_model"],
        settings.default_analyst_model,
    )
    skill_p, skill_m = _resolve(
        provider_override or settings.default_skill_provider,
        row["skill_model"],
        settings.default_skill_model,
    )

    return {
        "llm_mode": "byok" if is_byok else "credits",
        "llm_api_key": api_key,
        "llm_base_url": row["ollama_base_url"] if is_byok else None,
        "orchestrator_provider": orch_p,
        "orchestrator_model":    orch_m,
        "analyst_provider":      analyst_p,
        "analyst_model":         analyst_m,
        "default_skill_provider": skill_p,
        "default_skill_model":    skill_m,
    }
