"""
Persona + per-tenant skill prompt customization.

Portal (tenant manager+):
  GET  /portal/persona                       — load own persona
  PUT  /portal/persona                       — update own persona
  GET  /portal/agent-prompts                 — list per-skill prompt overrides + catalog defaults
  PUT  /portal/agent-prompts/{skill}         — set/clear override or extras
  DELETE /portal/agent-prompts/{skill}       — drop override (revert to catalog)

Admin (super admin only):
  GET  /admin/tenants/{id}/persona
  PUT  /admin/tenants/{id}/persona
  GET  /admin/tenants/{id}/agent-prompts
  PUT  /admin/tenants/{id}/agent-prompts/{skill}
  DELETE /admin/tenants/{id}/agent-prompts/{skill}
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_admin, require_tenant_user, TenantUserContext
from services.audit import log_event
from services.persona import PERSONA_DEFAULTS

log = structlog.get_logger()


# ── Schemas ───────────────────────────────────────────────────────────────────

class PersonaOut(BaseModel):
    agent_name: str
    agent_gender: str
    pharmacy_name: str | None
    pharmacy_tagline: str | None
    tone: str
    formality: str
    emoji_usage: str
    response_length: str
    language: str
    persona_bio: str | None
    greeting_template: str | None
    signature: str | None
    custom_instructions: str | None
    forbidden_topics: str | None
    catchphrases: list[str]
    conversation_playbook: str | None
    business_hours: str | None
    location: str | None
    delivery_info: str | None
    payment_methods: str | None
    website: str | None
    instagram: str | None


class PersonaUpdate(BaseModel):
    agent_name: str | None = Field(default=None, max_length=60)
    agent_gender: str | None = None
    pharmacy_name: str | None = None
    pharmacy_tagline: str | None = None
    tone: str | None = None
    formality: str | None = None
    emoji_usage: str | None = None
    response_length: str | None = None
    language: str | None = None
    persona_bio: str | None = None
    greeting_template: str | None = None
    signature: str | None = None
    custom_instructions: str | None = None
    forbidden_topics: str | None = None
    catchphrases: list[str] | None = None
    conversation_playbook: str | None = None
    business_hours: str | None = None
    location: str | None = None
    delivery_info: str | None = None
    payment_methods: str | None = None
    website: str | None = None
    instagram: str | None = None


class SkillPromptOut(BaseModel):
    skill_name: str
    display_name: str
    catalog_default_prompt: str | None     # from skill_catalog.prompt_template
    code_default_prompt: str | None         # the in-code SYSTEM_PROMPT (fallback)
    system_prompt: str | None               # tenant override (None = use default)
    extra_instructions: str | None
    has_override: bool


class SkillPromptUpdate(BaseModel):
    system_prompt: str | None = None       # set None to clear override
    extra_instructions: str | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

ALLOWED = {
    "agent_gender": {"feminino", "masculino", "neutro"},
    "tone": {"formal", "amigavel", "informal", "profissional", "divertido"},
    "formality": {"tu", "voce", "senhor"},
    "emoji_usage": {"none", "light", "moderate", "heavy"},
    "response_length": {"short", "medium", "long"},
}


def _validate_persona(updates: dict) -> None:
    for k, allowed in ALLOWED.items():
        if k in updates and updates[k] is not None and updates[k] not in allowed:
            raise HTTPException(
                status_code=422,
                detail=f"{k} deve ser um de: {sorted(allowed)}",
            )


async def _get_persona_row(tenant_id: str) -> dict:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenant_persona WHERE tenant_id = $1",
            tenant_id,
        )
    if not row:
        # Lazy create with defaults so PUT-then-GET is consistent
        async with get_db_conn() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO public.tenant_persona (tenant_id) VALUES ($1)
                ON CONFLICT (tenant_id) DO NOTHING
                RETURNING *
                """,
                tenant_id,
            )
            if row is None:
                row = await conn.fetchrow(
                    "SELECT * FROM public.tenant_persona WHERE tenant_id = $1",
                    tenant_id,
                )
    return dict(row)


def _row_to_persona(row: dict) -> PersonaOut:
    out = {**PERSONA_DEFAULTS, **{k: v for k, v in row.items() if k in PERSONA_DEFAULTS and v is not None}}
    out["catchphrases"] = list(out.get("catchphrases") or [])
    return PersonaOut(**out)


async def _update_persona(tenant_id: str, body: PersonaUpdate, actor_email: str) -> PersonaOut:
    updates = body.model_dump(exclude_none=True)
    _validate_persona(updates)
    if not updates:
        row = await _get_persona_row(tenant_id)
        return _row_to_persona(row)

    cols = list(updates.keys())
    set_clauses = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    insert_cols = ", ".join(cols)
    insert_placeholders = ", ".join(f"${i + 2}" for i in range(len(cols)))

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"""
            INSERT INTO public.tenant_persona (tenant_id, {insert_cols})
            VALUES ($1, {insert_placeholders})
            ON CONFLICT (tenant_id) DO UPDATE
                SET {set_clauses}, updated_at = NOW()
            RETURNING *
            """,
            tenant_id, *updates.values(),
        )

    await log_event(
        action="persona.updated",
        actor_id=actor_email,
        tenant_id=tenant_id,
        target="persona",
        meta={"changed_fields": cols},
    )
    return _row_to_persona(dict(row))


async def _list_skill_prompts(tenant_id: str) -> list[SkillPromptOut]:
    """
    Returns one row per skill in the catalog, indicating whether the tenant
    has overridden it.
    """
    from agents.nodes.skills import SKILL_REGISTRY

    async with get_db_conn() as conn:
        catalog = await conn.fetch(
            "SELECT skill_name, display_name, prompt_template FROM public.skill_catalog ORDER BY category, skill_name"
        )
        overrides = await conn.fetch(
            "SELECT skill_name, system_prompt, extra_instructions FROM public.tenant_skill_prompts WHERE tenant_id = $1",
            tenant_id,
        )
    over_map = {r["skill_name"]: r for r in overrides}

    out: list[SkillPromptOut] = []
    for c in catalog:
        ov = over_map.get(c["skill_name"])
        node_cls = SKILL_REGISTRY.get(c["skill_name"])
        code_default = getattr(node_cls, "SYSTEM_PROMPT", None) if node_cls else None
        out.append(SkillPromptOut(
            skill_name=c["skill_name"],
            display_name=c["display_name"],
            catalog_default_prompt=c["prompt_template"],
            code_default_prompt=code_default,
            system_prompt=ov["system_prompt"] if ov else None,
            extra_instructions=ov["extra_instructions"] if ov else None,
            has_override=bool(ov and (ov["system_prompt"] or ov["extra_instructions"])),
        ))
    return out


async def _upsert_skill_prompt(
    tenant_id: str, skill_name: str, body: SkillPromptUpdate, actor_email: str,
) -> None:
    async with get_db_conn() as conn:
        catalog = await conn.fetchrow(
            "SELECT 1 FROM public.skill_catalog WHERE skill_name = $1 AND active = TRUE",
            skill_name,
        )
        if not catalog:
            raise HTTPException(status_code=404, detail="Skill não existe no catálogo")

        await conn.execute(
            """
            INSERT INTO public.tenant_skill_prompts
                (tenant_id, skill_name, system_prompt, extra_instructions, updated_by, updated_at)
            VALUES ($1, $2, $3, $4, $5, NOW())
            ON CONFLICT (tenant_id, skill_name) DO UPDATE
                SET system_prompt      = EXCLUDED.system_prompt,
                    extra_instructions = EXCLUDED.extra_instructions,
                    updated_by         = EXCLUDED.updated_by,
                    updated_at         = NOW()
            """,
            tenant_id, skill_name, body.system_prompt, body.extra_instructions, actor_email,
        )

    await log_event(
        action="skill_prompt.updated",
        actor_id=actor_email,
        tenant_id=tenant_id,
        target=skill_name,
        meta={"has_system_prompt": body.system_prompt is not None,
              "has_extras": body.extra_instructions is not None},
    )


async def _delete_skill_prompt(tenant_id: str, skill_name: str, actor_email: str) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            "DELETE FROM public.tenant_skill_prompts WHERE tenant_id = $1 AND skill_name = $2",
            tenant_id, skill_name,
        )
    await log_event(
        action="skill_prompt.cleared",
        actor_id=actor_email,
        tenant_id=tenant_id,
        target=skill_name,
        meta={},
    )


# ── Portal router ────────────────────────────────────────────────────────────

portal_router = APIRouter(prefix="/portal", tags=["portal-persona"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


@portal_router.get("/persona", response_model=PersonaOut)
async def portal_get_persona(user: TenantUser) -> PersonaOut:
    row = await _get_persona_row(user.tenant_id)
    return _row_to_persona(row)


@portal_router.put("/persona", response_model=PersonaOut)
async def portal_update_persona(body: PersonaUpdate, user: TenantUser) -> PersonaOut:
    user.assert_role("manager")
    return await _update_persona(user.tenant_id, body, user.email)


@portal_router.get("/agent-prompts", response_model=list[SkillPromptOut])
async def portal_list_prompts(user: TenantUser) -> list[SkillPromptOut]:
    return await _list_skill_prompts(user.tenant_id)


@portal_router.put("/agent-prompts/{skill_name}")
async def portal_set_prompt(
    skill_name: str, body: SkillPromptUpdate, user: TenantUser,
) -> dict:
    user.assert_role("manager")
    await _upsert_skill_prompt(user.tenant_id, skill_name, body, user.email)
    return {"ok": True}


@portal_router.delete("/agent-prompts/{skill_name}")
async def portal_clear_prompt(skill_name: str, user: TenantUser) -> Response:
    user.assert_role("manager")
    await _delete_skill_prompt(user.tenant_id, skill_name, user.email)
    return Response(status_code=204)


# ── Admin (super admin) router ───────────────────────────────────────────────

admin_router = APIRouter(prefix="/admin/tenants", tags=["admin-persona"])
AdminUser = Annotated[str, Depends(require_admin)]


@admin_router.get("/{tenant_id}/persona", response_model=PersonaOut)
async def admin_get_persona(tenant_id: str, _admin: AdminUser) -> PersonaOut:
    row = await _get_persona_row(tenant_id)
    return _row_to_persona(row)


@admin_router.put("/{tenant_id}/persona", response_model=PersonaOut)
async def admin_update_persona(
    tenant_id: str, body: PersonaUpdate, admin: AdminUser,
) -> PersonaOut:
    return await _update_persona(tenant_id, body, f"admin:{admin}")


@admin_router.get("/{tenant_id}/agent-prompts", response_model=list[SkillPromptOut])
async def admin_list_prompts(tenant_id: str, _admin: AdminUser) -> list[SkillPromptOut]:
    return await _list_skill_prompts(tenant_id)


@admin_router.put("/{tenant_id}/agent-prompts/{skill_name}")
async def admin_set_prompt(
    tenant_id: str, skill_name: str, body: SkillPromptUpdate, admin: AdminUser,
) -> dict:
    await _upsert_skill_prompt(tenant_id, skill_name, body, f"admin:{admin}")
    return {"ok": True}


@admin_router.delete("/{tenant_id}/agent-prompts/{skill_name}")
async def admin_clear_prompt(tenant_id: str, skill_name: str, admin: AdminUser) -> Response:
    await _delete_skill_prompt(tenant_id, skill_name, f"admin:{admin}")
    return Response(status_code=204)
