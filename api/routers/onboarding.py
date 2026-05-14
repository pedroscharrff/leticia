"""
Self-service signup — creates tenant + owner user + schema in one atomic step.
Only enabled when settings.allow_signup = True.
"""
from __future__ import annotations

import re
import secrets

import structlog
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from config import settings
from db.postgres import get_db_conn
from security import hash_password, create_access_token
from services.audit import log_event
from services.email import send_welcome

log = structlog.get_logger()
router = APIRouter(tags=["onboarding"])


class SignupRequest(BaseModel):
    pharmacy_name: str = Field(..., min_length=2, max_length=200)
    owner_name: str = Field(..., min_length=2, max_length=200)
    owner_email: str = Field(..., pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    owner_password: str = Field(..., min_length=8)
    callback_url: str = Field(..., description="WhatsApp webhook callback URL")
    plan: str = Field("basic", pattern="^(basic|pro|enterprise)$")


class SignupResponse(BaseModel):
    tenant_id: str
    schema_name: str
    api_key: str
    access_token: str
    message: str


def _make_schema_name(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", name.lower())[:40]
    return f"tenant_{slug}"


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest) -> SignupResponse:
    if not settings.allow_signup:
        raise HTTPException(status_code=403, detail="Cadastro auto-atendimento está desabilitado")

    api_key = secrets.token_urlsafe(32)
    schema_name = _make_schema_name(body.pharmacy_name)
    pw_hash = hash_password(body.owner_password)

    async with get_db_conn() as conn:
        # Check duplicates
        if await conn.fetchval("SELECT 1 FROM public.tenants WHERE schema_name = $1", schema_name):
            raise HTTPException(status_code=409, detail="Já existe uma farmácia com esse nome")
        if await conn.fetchval("SELECT 1 FROM public.tenant_users WHERE email = $1", body.owner_email):
            raise HTTPException(status_code=409, detail="E-mail já cadastrado")

        async with conn.transaction():
            # Create tenant
            tenant_row = await conn.fetchrow(
                """
                INSERT INTO public.tenants (name, api_key, callback_url, plan, schema_name)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, schema_name
                """,
                body.pharmacy_name, api_key, body.callback_url, body.plan, schema_name,
            )
            tenant_id = str(tenant_row["id"])

            # Create owner user
            await conn.execute(
                """
                INSERT INTO public.tenant_users (tenant_id, email, password_hash, name, role)
                VALUES ($1, $2, $3, $4, 'owner')
                """,
                tenant_id, body.owner_email, pw_hash, body.owner_name,
            )

            # Create schema + tables
            await conn.execute("SELECT create_tenant_schema($1)", schema_name)

            # Seed skills_config from catalog (only plan-eligible skills, inactive by default)
            await conn.execute(
                f"""
                INSERT INTO {schema_name}.skills_config (skill_name)
                SELECT skill_name FROM public.skill_catalog
                WHERE active = TRUE
                  AND plan_min IN (
                      SELECT plan_name FROM public.plans
                      WHERE plan_name = $1
                         OR (ARRAY_POSITION(ARRAY['basic','pro','enterprise'], plan_name)
                             <= ARRAY_POSITION(ARRAY['basic','pro','enterprise'], $1))
                  )
                ON CONFLICT DO NOTHING
                """,
                body.plan,
            )

            # Create trial subscription
            await conn.execute(
                """
                INSERT INTO public.subscriptions (tenant_id, plan_name, provider, status)
                VALUES ($1, $2, 'manual', 'trialing')
                ON CONFLICT DO NOTHING
                """,
                tenant_id, body.plan,
            )

    token = create_access_token(
        sub=body.owner_email,
        role="tenant",
        tenant_id=tenant_id,
        tenant_role="owner",
        name=body.owner_name,
    )

    await log_event(
        action="tenant.signup",
        actor_id=body.owner_email,
        tenant_id=tenant_id,
        meta={"plan": body.plan, "pharmacy": body.pharmacy_name},
    )

    # Fire-and-forget welcome email (don't fail signup if email fails)
    try:
        await send_welcome(body.owner_email, body.owner_name, body.pharmacy_name, body.owner_password)
    except Exception as exc:
        log.warning("signup.email_failed", error=str(exc))

    log.info("tenant.signup_complete", tenant=tenant_id, schema=schema_name, plan=body.plan)

    return SignupResponse(
        tenant_id=tenant_id,
        schema_name=schema_name,
        api_key=api_key,
        access_token=token,
        message=f"Farmácia '{body.pharmacy_name}' criada com sucesso! Plano {body.plan} com 7 dias de trial.",
    )
