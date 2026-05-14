"""
Per-tenant metrics and skill configuration — JWT protected.
"""
import json
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from db.postgres import tenant_conn, get_db_conn
from models.skill import SkillConfig, SkillUpdate
from security import require_admin

router = APIRouter(prefix="/admin", tags=["dashboard"])
AdminUser = Annotated[str, Depends(require_admin)]


class UsageMetric(BaseModel):
    month: str
    conversations: int
    tokens_total: int
    cost_usd: float


class SystemOverview(BaseModel):
    total_tenants: int
    active_tenants: int
    total_conversations_this_month: int


# ── System overview ───────────────────────────────────────────────────────────

@router.get("/overview", response_model=SystemOverview)
async def system_overview(_admin: AdminUser) -> SystemOverview:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*)                          AS total_tenants,
                COUNT(*) FILTER (WHERE active)    AS active_tenants
            FROM public.tenants
            """
        )
    return SystemOverview(
        total_tenants=row["total_tenants"],
        active_tenants=row["active_tenants"],
        total_conversations_this_month=0,  # aggregated per tenant schema — simplified
    )


# ── Per-tenant usage ──────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/usage", response_model=list[UsageMetric])
async def get_usage(tenant_id: str, _admin: AdminUser) -> list[UsageMetric]:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE",
            tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    async with tenant_conn(row["schema_name"]) as conn:
        rows = await conn.fetch(
            "SELECT month::text, conversations, tokens_total, cost_usd "
            "FROM usage_metrics ORDER BY month DESC LIMIT 12"
        )
    return [UsageMetric(**dict(r)) for r in rows]


# ── Skills ────────────────────────────────────────────────────────────────────

@router.get("/tenants/{tenant_id}/skills", response_model=list[SkillConfig])
async def list_skills(tenant_id: str, _admin: AdminUser) -> list[SkillConfig]:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1", tenant_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    async with tenant_conn(row["schema_name"]) as conn:
        rows = await conn.fetch("SELECT * FROM skills_config ORDER BY skill_name")
    return [SkillConfig(**dict(r)) for r in rows]


@router.patch("/tenants/{tenant_id}/skills/{skill_name}", response_model=SkillConfig)
async def update_skill(
    tenant_id: str,
    skill_name: str,
    body: SkillUpdate,
    _admin: AdminUser,
) -> SkillConfig:
    async with get_db_conn() as conn:
        meta = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1", tenant_id
        )
    if not meta:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

    if "config_json" in updates:
        updates["config_json"] = json.dumps(updates["config_json"])

    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    values = list(updates.values())

    async with tenant_conn(meta["schema_name"]) as conn:
        row = await conn.fetchrow(
            f"UPDATE skills_config SET {set_clauses} WHERE skill_name = $1 RETURNING *",
            skill_name, *values,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Skill não encontrada")

    return SkillConfig(**dict(row))


# ── Seed skills for a tenant ──────────────────────────────────────────────────

PLAN_SKILLS: dict[str, list[tuple]] = {
    "basic":      [("farmaceutico",   "claude-sonnet-4-6",         "anthropic")],
    "pro":        [("farmaceutico",   "claude-sonnet-4-6",         "anthropic"),
                   ("principio_ativo","claude-sonnet-4-6",         "anthropic"),
                   ("genericos",      "gemini-2.0-flash",          "google"),
                   ("vendedor",       "claude-sonnet-4-6",         "anthropic")],
    "enterprise": [("farmaceutico",   "claude-sonnet-4-6",         "anthropic"),
                   ("principio_ativo","claude-sonnet-4-6",         "anthropic"),
                   ("genericos",      "gemini-2.0-flash",          "google"),
                   ("vendedor",       "claude-sonnet-4-6",         "anthropic"),
                   ("recuperador",    "claude-haiku-4-5-20251001", "anthropic")],
}


@router.post("/tenants/{tenant_id}/skills/seed", status_code=status.HTTP_200_OK)
async def seed_skills(tenant_id: str, _admin: AdminUser) -> dict:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name, plan FROM public.tenants WHERE id = $1", tenant_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    skills = PLAN_SKILLS.get(row["plan"], PLAN_SKILLS["basic"])
    async with tenant_conn(row["schema_name"]) as conn:
        for skill_name, llm_model, llm_provider in skills:
            await conn.execute(
                """
                INSERT INTO skills_config (skill_name, ativo, llm_model, llm_provider)
                VALUES ($1, TRUE, $2, $3)
                ON CONFLICT (skill_name) DO UPDATE
                SET ativo = TRUE, llm_model = EXCLUDED.llm_model,
                    llm_provider = EXCLUDED.llm_provider
                """,
                skill_name, llm_model, llm_provider,
            )

    return {"seeded": len(skills), "plan": row["plan"]}
