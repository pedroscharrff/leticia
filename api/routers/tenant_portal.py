"""
Rotas do portal da farmácia — protegidas por JWT com role=tenant.
Cada farmácia só acessa seus próprios dados.
"""
import json
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from datetime import datetime

from db.postgres import get_db_conn, tenant_conn
from models.skill import SkillConfig, SkillUpdate
from models.tenant import TenantResponse
from security import require_tenant_user, TenantUserContext

router = APIRouter(prefix="/portal", tags=["portal"])

TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


class UsageMetric(BaseModel):
    month: str
    conversations: int
    tokens_total: int
    cost_usd: float


class ConversationLog(BaseModel):
    id: str
    session_key: str
    role: str
    content: str
    skill_used: str | None
    llm_model: str | None
    tokens_in: int | None
    tokens_out: int | None
    latency_ms: int | None
    created_at: datetime


class PortalMeResponse(BaseModel):
    tenant_id: str
    tenant_name: str
    plan: str
    api_key: str
    schema_name: str
    active: bool
    callback_url: str
    user_email: str


# ── Informações do próprio tenant ─────────────────────────────────────────────

@router.get("/me", response_model=PortalMeResponse)
async def get_me(user: TenantUser) -> PortalMeResponse:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenants WHERE id = $1 AND active = TRUE",
            user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")
    return PortalMeResponse(
        tenant_id=str(row["id"]),
        tenant_name=row["name"],
        plan=row["plan"],
        api_key=row["api_key"],
        schema_name=row["schema_name"],
        active=row["active"],
        callback_url=row["callback_url"],
        user_email=user.email,
    )


# ── Métricas de uso ───────────────────────────────────────────────────────────

@router.get("/usage", response_model=list[UsageMetric])
async def get_usage(user: TenantUser) -> list[UsageMetric]:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE",
            user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")

    async with tenant_conn(row["schema_name"]) as conn:
        rows = await conn.fetch(
            "SELECT month::text, conversations, tokens_total, cost_usd "
            "FROM usage_metrics ORDER BY month DESC LIMIT 12"
        )
    return [UsageMetric(**dict(r)) for r in rows]


# ── Skills ────────────────────────────────────────────────────────────────────

@router.get("/skills", response_model=list[SkillConfig])
async def list_skills(user: TenantUser) -> list[SkillConfig]:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE",
            user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")

    async with tenant_conn(row["schema_name"]) as conn:
        rows = await conn.fetch("SELECT * FROM skills_config ORDER BY skill_name")
    return [SkillConfig(**dict(r)) for r in rows]


@router.patch("/skills/{skill_name}", response_model=SkillConfig)
async def update_skill(
    skill_name: str,
    body: SkillUpdate,
    user: TenantUser,
) -> SkillConfig:
    async with get_db_conn() as conn:
        meta = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE",
            user.tenant_id,
        )
    if not meta:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")

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


# ── Logs de conversa ─────────────────────────────────────────────────────────

@router.get("/logs", response_model=list[ConversationLog])
async def get_logs(
    user: TenantUser,
    limit: int = 50,
    offset: int = 0,
) -> list[ConversationLog]:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE",
            user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")

    async with tenant_conn(row["schema_name"]) as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, session_key, role, content, skill_used,
                   llm_model, tokens_in, tokens_out, latency_ms, created_at
            FROM conversation_logs
            ORDER BY created_at DESC
            LIMIT $1 OFFSET $2
            """,
            limit, offset,
        )
    return [ConversationLog(**dict(r)) for r in rows]
