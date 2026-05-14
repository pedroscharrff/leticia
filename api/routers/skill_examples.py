"""
Per-tenant few-shot examples for skill training.

Tenants curate "ideal response" pairs per skill. The agent injects up to N
of these (ranked by similarity to the customer's current message) into the
skill's system prompt at run time, so the LLM has concrete templates to
follow without retraining.

Portal:
  GET    /portal/agent-examples?skill=vendedor   — list examples
  POST   /portal/agent-examples                   — create
  PATCH  /portal/agent-examples/{id}              — update
  DELETE /portal/agent-examples/{id}              — delete

Admin (read-only inspection of any tenant):
  GET    /admin/tenants/{id}/agent-examples
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_admin, require_tenant_user, TenantUserContext
from services.audit import log_event

log = structlog.get_logger()

portal_router = APIRouter(prefix="/portal/agent-examples", tags=["portal-examples"])
admin_router = APIRouter(prefix="/admin/tenants", tags=["admin-examples"])

TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]
AdminUser = Annotated[str, Depends(require_admin)]


# ── Schemas ──────────────────────────────────────────────────────────────────

class ExampleOut(BaseModel):
    id: str
    skill_name: str
    user_message: str
    ideal_response: str
    tags: list[str] = []
    notes: str | None = None
    enabled: bool = True
    weight: int = 1
    created_at: str
    updated_at: str


class ExampleCreate(BaseModel):
    skill_name: str = Field(min_length=1, max_length=50)
    user_message: str = Field(min_length=1)
    ideal_response: str = Field(min_length=1)
    tags: list[str] = []
    notes: str | None = None
    weight: int = Field(default=1, ge=0, le=10)


class ExampleUpdate(BaseModel):
    user_message: str | None = None
    ideal_response: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    enabled: bool | None = None
    weight: int | None = Field(default=None, ge=0, le=10)


def _row_to_out(row) -> ExampleOut:
    return ExampleOut(
        id=str(row["id"]),
        skill_name=row["skill_name"],
        user_message=row["user_message"],
        ideal_response=row["ideal_response"],
        tags=list(row["tags"] or []),
        notes=row["notes"],
        enabled=row["enabled"],
        weight=row["weight"],
        created_at=row["created_at"].isoformat(),
        updated_at=row["updated_at"].isoformat(),
    )


# ── Portal endpoints ─────────────────────────────────────────────────────────

@portal_router.get("", response_model=list[ExampleOut])
async def list_examples(
    user: TenantUser,
    skill: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
) -> list[ExampleOut]:
    filters = ["tenant_id = $1"]
    params: list = [user.tenant_id]
    if skill:
        filters.append(f"skill_name = ${len(params)+1}")
        params.append(skill)
    if enabled_only:
        filters.append("enabled = TRUE")
    where = " AND ".join(filters)

    async with get_db_conn() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM public.tenant_skill_examples WHERE {where} "
            f"ORDER BY skill_name, weight DESC, created_at DESC",
            *params,
        )
    return [_row_to_out(r) for r in rows]


@portal_router.post("", response_model=ExampleOut, status_code=status.HTTP_201_CREATED)
async def create_example(body: ExampleCreate, user: TenantUser) -> ExampleOut:
    user.assert_role("manager")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_skill_examples
                (tenant_id, skill_name, user_message, ideal_response, tags, notes, weight)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            user.tenant_id, body.skill_name, body.user_message,
            body.ideal_response, body.tags, body.notes, body.weight,
        )
    await log_event(
        action="skill_example.created", actor_id=user.email,
        tenant_id=user.tenant_id, target=body.skill_name,
    )
    return _row_to_out(row)


@portal_router.patch("/{example_id}", response_model=ExampleOut)
async def update_example(
    example_id: str, body: ExampleUpdate, user: TenantUser
) -> ExampleOut:
    user.assert_role("manager")
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

    set_clauses = ", ".join(f"{k} = ${i+3}" for i, k in enumerate(updates))
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.tenant_skill_examples
               SET {set_clauses}, updated_at = NOW()
             WHERE id = $1 AND tenant_id = $2
         RETURNING *
            """,
            example_id, user.tenant_id, *updates.values(),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Exemplo não encontrado")
    return _row_to_out(row)


@portal_router.delete("/{example_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_example(example_id: str, user: TenantUser) -> None:
    user.assert_role("manager")
    async with get_db_conn() as conn:
        result = await conn.execute(
            "DELETE FROM public.tenant_skill_examples WHERE id = $1 AND tenant_id = $2",
            example_id, user.tenant_id,
        )
    if result == "DELETE 0":
        raise HTTPException(status_code=404, detail="Exemplo não encontrado")


# ── Admin endpoints ──────────────────────────────────────────────────────────

@admin_router.get("/{tenant_id}/agent-examples", response_model=list[ExampleOut])
async def admin_list_examples(
    tenant_id: str,
    _admin: AdminUser,
    skill: str | None = Query(default=None),
) -> list[ExampleOut]:
    filters = ["tenant_id = $1"]
    params: list = [tenant_id]
    if skill:
        filters.append(f"skill_name = ${len(params)+1}")
        params.append(skill)
    where = " AND ".join(filters)

    async with get_db_conn() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM public.tenant_skill_examples WHERE {where} "
            f"ORDER BY skill_name, weight DESC, created_at DESC",
            *params,
        )
    return [_row_to_out(r) for r in rows]
