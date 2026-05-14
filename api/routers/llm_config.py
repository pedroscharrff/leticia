"""
LLM configuration endpoints — per-tenant BYOK vs platform credits.

Portal (tenant):
  GET  /portal/llm-config          — get current config
  PUT  /portal/llm-config          — update mode / provider / api key
  DELETE /portal/llm-config/key    — remove BYOK key (revert to credits)

Admin:
  GET  /admin/tenants/{id}/llm-config   — inspect any tenant's config
  POST /admin/tenants/{id}/llm-config/reset — force back to credits
"""
import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from typing import Annotated

from db.postgres import get_db_conn
from security import require_admin, require_tenant_user, TenantUserContext
from services.secrets import encrypt, decrypt

log = structlog.get_logger()

portal_router = APIRouter(prefix="/portal/llm-config", tags=["portal-llm-config"])
admin_router = APIRouter(prefix="/admin/tenants", tags=["admin-llm-config"])

TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]
AdminUser = Annotated[str, Depends(require_admin)]

SUPPORTED_PROVIDERS = {"anthropic", "openai", "google", "ollama"}

# Curated catalog of models known to work, grouped by provider.
# Tenants can still type a custom model id — this is just suggestions
# for the portal UI to render dropdowns.
MODEL_CATALOG: dict[str, list[dict]] = {
    "anthropic": [
        {"id": "claude-haiku-4-5-20251001", "label": "Claude Haiku 4.5",  "tier": "fast",     "good_for": ["orchestrator", "analyst"]},
        {"id": "claude-sonnet-4-6",         "label": "Claude Sonnet 4.6", "tier": "balanced", "good_for": ["skill"]},
        {"id": "claude-opus-4-7",           "label": "Claude Opus 4.7",   "tier": "smart",    "good_for": ["skill"]},
    ],
    "openai": [
        {"id": "gpt-4o-mini",   "label": "GPT-4o mini", "tier": "fast",     "good_for": ["orchestrator", "analyst"]},
        {"id": "gpt-4o",        "label": "GPT-4o",      "tier": "balanced", "good_for": ["skill"]},
        {"id": "gpt-5-mini",    "label": "GPT-5 mini",  "tier": "fast",     "good_for": ["orchestrator", "analyst"]},
        {"id": "gpt-5",         "label": "GPT-5",       "tier": "smart",    "good_for": ["skill"]},
    ],
    "google": [
        {"id": "gemini-2.0-flash", "label": "Gemini 2.0 Flash", "tier": "fast",     "good_for": ["orchestrator", "analyst", "skill"]},
        {"id": "gemini-2.5-pro",   "label": "Gemini 2.5 Pro",   "tier": "smart",    "good_for": ["skill"]},
    ],
    "ollama": [
        {"id": "llama3.2",  "label": "Llama 3.2 (local)",  "tier": "fast",     "good_for": ["orchestrator", "analyst"]},
        {"id": "qwen2.5",   "label": "Qwen 2.5 (local)",   "tier": "balanced", "good_for": ["skill"]},
    ],
}


# ── Pydantic models ───────────────────────────────────────────────────────────

class LLMConfigResponse(BaseModel):
    mode: str
    provider: str | None
    has_api_key: bool
    orchestrator_model: str | None
    analyst_model: str | None
    skill_model: str | None
    ollama_base_url: str | None


class LLMConfigUpdate(BaseModel):
    mode: str                        # 'byok' | 'credits'
    provider: str | None = None      # required when mode='byok'
    api_key: str | None = None       # required when mode='byok' (not 'ollama')
    orchestrator_model: str | None = None
    analyst_model: str | None = None
    skill_model: str | None = None
    ollama_base_url: str | None = None

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        if v not in ("byok", "credits"):
            raise ValueError("mode must be 'byok' or 'credits'")
        return v

    @field_validator("provider")
    @classmethod
    def validate_provider(cls, v: str | None) -> str | None:
        if v is not None and v not in SUPPORTED_PROVIDERS:
            raise ValueError(f"provider must be one of {sorted(SUPPORTED_PROVIDERS)}")
        return v


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_config(tenant_id: str) -> dict | None:
    async with get_db_conn() as conn:
        return await conn.fetchrow(
            "SELECT * FROM public.tenant_llm_config WHERE tenant_id = $1",
            tenant_id,
        )


async def _upsert_config(tenant_id: str, **fields) -> None:
    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(fields))
    values = list(fields.values())
    async with get_db_conn() as conn:
        await conn.execute(
            f"""
            INSERT INTO public.tenant_llm_config (tenant_id, {', '.join(fields)})
            VALUES ($1, {', '.join(f'${i+2}' for i in range(len(fields)))})
            ON CONFLICT (tenant_id) DO UPDATE
                SET {set_clauses}, updated_at = NOW()
            """,
            tenant_id, *values,
        )


def _row_to_response(row) -> LLMConfigResponse:
    return LLMConfigResponse(
        mode=row["mode"],
        provider=row["provider"],
        has_api_key=row["api_key_enc"] is not None,
        orchestrator_model=row["orchestrator_model"],
        analyst_model=row["analyst_model"],
        skill_model=row["skill_model"],
        ollama_base_url=row["ollama_base_url"],
    )


# ── Portal endpoints ──────────────────────────────────────────────────────────

@portal_router.get("/models", response_model=dict)
async def list_model_catalog(_: TenantUser) -> dict:
    """
    Returns the curated catalog of models per provider so the portal can
    render dropdowns. Tenants can still submit any custom model id.
    """
    return {"providers": sorted(SUPPORTED_PROVIDERS), "models": MODEL_CATALOG}


@portal_router.get("", response_model=LLMConfigResponse)
async def get_llm_config(user: TenantUser) -> LLMConfigResponse:
    row = await _get_config(user.tenant_id)
    if not row:
        return LLMConfigResponse(
            mode="credits", provider=None, has_api_key=False,
            orchestrator_model=None, analyst_model=None,
            skill_model=None, ollama_base_url=None,
        )
    return _row_to_response(row)


@portal_router.put("", response_model=LLMConfigResponse)
async def update_llm_config(body: LLMConfigUpdate, user: TenantUser) -> LLMConfigResponse:
    user.assert_role("manager")

    if body.mode == "byok":
        if not body.provider:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="provider é obrigatório no modo byok",
            )
        if body.provider != "ollama" and not body.api_key:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="api_key é obrigatória no modo byok (exceto ollama)",
            )
        if body.provider == "ollama" and not body.ollama_base_url:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="ollama_base_url é obrigatória ao usar provider ollama",
            )

    api_key_enc = None
    if body.mode == "byok" and body.api_key:
        api_key_enc = encrypt(body.api_key)

    await _upsert_config(
        user.tenant_id,
        mode=body.mode,
        provider=body.provider if body.mode == "byok" else None,
        api_key_enc=api_key_enc if body.mode == "byok" else None,
        orchestrator_model=body.orchestrator_model,
        analyst_model=body.analyst_model,
        skill_model=body.skill_model,
        ollama_base_url=body.ollama_base_url if body.mode == "byok" else None,
    )

    log.info(
        "llm_config.updated",
        tenant=user.tenant_id,
        mode=body.mode,
        provider=body.provider,
    )

    row = await _get_config(user.tenant_id)
    return _row_to_response(row)


@portal_router.delete("/key", status_code=status.HTTP_204_NO_CONTENT)
async def remove_api_key(user: TenantUser) -> None:
    """Remove the BYOK key and revert to platform credits."""
    user.assert_role("manager")
    await _upsert_config(
        user.tenant_id,
        mode="credits",
        provider=None,
        api_key_enc=None,
        orchestrator_model=None,
        analyst_model=None,
        skill_model=None,
        ollama_base_url=None,
    )
    log.info("llm_config.key_removed", tenant=user.tenant_id)


# ── Admin endpoints ───────────────────────────────────────────────────────────

@admin_router.get("/{tenant_id}/llm-config", response_model=LLMConfigResponse)
async def admin_get_llm_config(tenant_id: str, _admin: AdminUser) -> LLMConfigResponse:
    row = await _get_config(tenant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Tenant sem configuração LLM")
    return _row_to_response(row)


@admin_router.post("/{tenant_id}/llm-config/reset", status_code=status.HTTP_204_NO_CONTENT)
async def admin_reset_llm_config(tenant_id: str, _admin: AdminUser) -> None:
    """Force tenant back to platform credits and wipe their API key."""
    await _upsert_config(
        tenant_id,
        mode="credits",
        provider=None,
        api_key_enc=None,
        orchestrator_model=None,
        analyst_model=None,
        skill_model=None,
        ollama_base_url=None,
    )
    log.info("llm_config.admin_reset", tenant=tenant_id)
