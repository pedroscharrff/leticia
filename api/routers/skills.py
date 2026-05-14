"""
Admin skill catalog management + Portal skill activation per tenant.
"""
from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from db.postgres import get_db_conn, tenant_conn
from security import require_admin, require_tenant_user, TenantUserContext
from services.audit import log_event

log = structlog.get_logger()

# ── Schemas ───────────────────────────────────────────────────────────────────

class SkillCatalogOut(BaseModel):
    skill_name: str
    display_name: str
    description: str | None
    category: str | None
    plan_min: str
    channel_compat: list[str]
    default_llm: str | None
    default_provider: str | None
    active: bool


class SkillCatalogCreate(BaseModel):
    skill_name: str
    display_name: str
    description: str | None = None
    category: str = "general"
    plan_min: str = "basic"
    channel_compat: list[str] = ["whatsapp_cloud", "whatsapp_zapi"]
    default_llm: str | None = None
    default_provider: str | None = None
    prompt_template: str | None = None
    tools_json: list[dict] = []


class SkillCatalogUpdate(BaseModel):
    display_name: str | None = None
    description: str | None = None
    category: str | None = None
    plan_min: str | None = None
    channel_compat: list[str] | None = None
    default_llm: str | None = None
    default_provider: str | None = None
    prompt_template: str | None = None
    tools_json: list[dict] | None = None
    active: bool | None = None


class TenantSkillConfig(BaseModel):
    skill_name: str
    display_name: str
    description: str | None
    category: str | None
    plan_min: str
    ativo: bool
    llm_model: str | None
    llm_provider: str | None
    prompt_version: str
    config_json: dict


class TenantSkillUpdate(BaseModel):
    ativo: bool | None = None
    llm_model: str | None = None
    llm_provider: str | None = None
    prompt_version: str | None = None
    config_json: dict | None = None


# ── Admin router ──────────────────────────────────────────────────────────────

admin_router = APIRouter(prefix="/admin/skills", tags=["admin-skills"])
AdminUser = Annotated[str, Depends(require_admin)]


@admin_router.get("", response_model=list[SkillCatalogOut])
async def list_skill_catalog(_admin: AdminUser) -> list[SkillCatalogOut]:
    async with get_db_conn() as conn:
        rows = await conn.fetch("SELECT * FROM public.skill_catalog ORDER BY category, skill_name")
    return [SkillCatalogOut(**dict(r)) for r in rows]


@admin_router.post("", response_model=SkillCatalogOut, status_code=status.HTTP_201_CREATED)
async def create_skill(body: SkillCatalogCreate, _admin: AdminUser) -> SkillCatalogOut:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.skill_catalog
                (skill_name, display_name, description, category, plan_min,
                 channel_compat, default_llm, default_provider, prompt_template, tools_json)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            RETURNING *
            """,
            body.skill_name, body.display_name, body.description, body.category,
            body.plan_min, body.channel_compat, body.default_llm, body.default_provider,
            body.prompt_template, json.dumps(body.tools_json),
        )
    log.info("skill_catalog.created", skill=body.skill_name)
    return SkillCatalogOut(**dict(row))


@admin_router.patch("/{skill_name}", response_model=SkillCatalogOut)
async def update_skill_catalog(
    skill_name: str,
    body: SkillCatalogUpdate,
    _admin: AdminUser,
) -> SkillCatalogOut:
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

    if "tools_json" in updates:
        updates["tools_json"] = json.dumps(updates["tools_json"])
    if "channel_compat" in updates:
        updates["channel_compat"] = updates["channel_compat"]

    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.skill_catalog SET {set_clauses} WHERE skill_name = $1 RETURNING *",
            skill_name, *updates.values(),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Skill não encontrada no catálogo")
    return SkillCatalogOut(**dict(row))


@admin_router.delete("/{skill_name}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_skill_catalog(skill_name: str, _admin: AdminUser) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.skill_catalog SET active = FALSE WHERE skill_name = $1", skill_name
        )


# ── Portal router (per-tenant skill management) ───────────────────────────────

portal_router = APIRouter(prefix="/portal/skills", tags=["portal-skills"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


async def _get_schema(tenant_id: str) -> str:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT t.schema_name, p.features, p.limits, t.plan
            FROM public.tenants t
            JOIN public.plans p ON p.plan_name = t.plan
            WHERE t.id = $1 AND t.active = TRUE
            """,
            tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")
    return row["schema_name"], row["features"], row["plan"]


@portal_router.get("", response_model=list[TenantSkillConfig])
async def list_tenant_skills(user: TenantUser) -> list[TenantSkillConfig]:
    schema, features, plan = await _get_schema(user.tenant_id)
    skills_max: int = features.get("skills_max", 1) if isinstance(features, dict) else 1

    async with get_db_conn() as conn:
        catalog_rows = await conn.fetch(
            "SELECT * FROM public.skill_catalog WHERE active = TRUE ORDER BY category, skill_name"
        )

    async with tenant_conn(schema) as conn:
        config_rows = await conn.fetch("SELECT * FROM skills_config")

    config_map = {r["skill_name"]: dict(r) for r in config_rows}

    result: list[TenantSkillConfig] = []
    for cr in catalog_rows:
        cfg = config_map.get(cr["skill_name"], {})
        result.append(TenantSkillConfig(
            skill_name=cr["skill_name"],
            display_name=cr["display_name"],
            description=cr["description"],
            category=cr["category"],
            plan_min=cr["plan_min"],
            ativo=cfg.get("ativo", False),
            llm_model=cfg.get("llm_model") or cr["default_llm"],
            llm_provider=cfg.get("llm_provider") or cr["default_provider"],
            prompt_version=cfg.get("prompt_version", "v1"),
            config_json=cfg.get("config_json") or {},
        ))
    return result


@portal_router.patch("/{skill_name}", response_model=TenantSkillConfig)
async def update_tenant_skill(
    skill_name: str,
    body: TenantSkillUpdate,
    user: TenantUser,
) -> TenantSkillConfig:
    user.assert_role("manager")
    schema, features, plan = await _get_schema(user.tenant_id)

    async with get_db_conn() as conn:
        catalog = await conn.fetchrow(
            "SELECT * FROM public.skill_catalog WHERE skill_name = $1 AND active = TRUE",
            skill_name,
        )
    if not catalog:
        raise HTTPException(status_code=404, detail="Skill não encontrada no catálogo")

    # Plan gate: check if skill is available for tenant's plan
    plan_order = ["basic", "pro", "enterprise"]
    if plan_order.index(catalog["plan_min"]) > plan_order.index(plan):
        raise HTTPException(
            status_code=402,
            detail=f"Skill '{skill_name}' requer plano '{catalog['plan_min']}' ou superior",
        )

    # Enforce max active skills limit
    if body.ativo:
        skills_max = features.get("skills_max", 1) if isinstance(features, dict) else 1
        async with tenant_conn(schema) as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM skills_config WHERE ativo = TRUE")
        if skills_max and count >= skills_max:
            raise HTTPException(
                status_code=402,
                detail=f"Limite de {skills_max} skill(s) ativas atingido para seu plano",
            )

    updates = body.model_dump(exclude_none=True)
    if "config_json" in updates:
        updates["config_json"] = json.dumps(updates["config_json"])

    async with tenant_conn(schema) as conn:
        # Upsert: ensure row exists in skills_config
        await conn.execute(
            """
            INSERT INTO skills_config (skill_name) VALUES ($1)
            ON CONFLICT (skill_name) DO NOTHING
            """,
            skill_name,
        )
        if updates:
            set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
            row = await conn.fetchrow(
                f"UPDATE skills_config SET {set_clauses} WHERE skill_name = $1 RETURNING *",
                skill_name, *updates.values(),
            )
        else:
            row = await conn.fetchrow("SELECT * FROM skills_config WHERE skill_name = $1", skill_name)

    await log_event(
        action="skill.updated",
        actor_id=user.email,
        tenant_id=user.tenant_id,
        target=skill_name,
        meta={"changes": list(body.model_dump(exclude_none=True).keys())},
    )

    return TenantSkillConfig(
        skill_name=row["skill_name"],
        display_name=catalog["display_name"],
        description=catalog["description"],
        category=catalog["category"],
        plan_min=catalog["plan_min"],
        ativo=row["ativo"],
        llm_model=row["llm_model"],
        llm_provider=row["llm_provider"],
        prompt_version=row["prompt_version"],
        config_json=row["config_json"] or {},
    )
