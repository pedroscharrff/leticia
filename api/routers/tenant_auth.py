"""
POST /portal/auth/login  — login do proprietário da farmácia.
POST /portal/auth/register — criado pelo admin ao cadastrar uma farmácia.
"""
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from db.postgres import get_db_conn
from security import create_access_token, verify_password, hash_password, require_admin
from typing import Annotated
from fastapi import Depends

log = structlog.get_logger()
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/portal/auth", tags=["portal-auth"])

AdminUser = Annotated[str, Depends(require_admin)]


class PortalLoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class CreateTenantUserRequest(BaseModel):
    email: str
    password: str
    name: str | None = None


class TenantUserResponse(BaseModel):
    id: str
    tenant_id: str
    email: str
    name: str | None
    active: bool


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def portal_login(request: Request, body: PortalLoginRequest) -> TokenResponse:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT tu.*, t.schema_name
            FROM public.tenant_users tu
            JOIN public.tenants t ON t.id = tu.tenant_id
            WHERE tu.email = $1 AND tu.active = TRUE AND t.active = TRUE
            """,
            body.email,
        )

    ok = row is not None and verify_password(body.password, row["password_hash"])
    if not ok:
        log.warning("portal.login.failed", email=body.email)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Credenciais inválidas")

    token = create_access_token(
        sub=row["email"],
        role="tenant",
        tenant_id=str(row["tenant_id"]),
        tenant_role=row.get("role", "owner"),
        name=row.get("name"),
    )
    log.info("portal.login.success", email=body.email, tenant_id=str(row["tenant_id"]))

    from config import settings
    return TokenResponse(access_token=token, expires_in=settings.jwt_access_token_expire_minutes * 60)


# ── Criado pelo admin ao cadastrar a farmácia ─────────────────────────────────

@router.post("/tenants/{tenant_id}/users", response_model=TenantUserResponse, status_code=201)
async def create_tenant_user(
    tenant_id: str,
    body: CreateTenantUserRequest,
    _admin: AdminUser,
) -> TenantUserResponse:
    async with get_db_conn() as conn:
        tenant = await conn.fetchrow(
            "SELECT id FROM public.tenants WHERE id = $1", tenant_id
        )
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant não encontrado")

        existing = await conn.fetchrow(
            "SELECT id FROM public.tenant_users WHERE email = $1", body.email
        )
        if existing:
            raise HTTPException(status_code=409, detail="E-mail já cadastrado")

        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_users (tenant_id, email, password_hash, name)
            VALUES ($1, $2, $3, $4)
            RETURNING *
            """,
            tenant_id,
            body.email,
            hash_password(body.password),
            body.name,
        )

    log.info("portal.user.created", email=body.email, tenant_id=tenant_id)
    return TenantUserResponse(
        id=str(row["id"]),
        tenant_id=str(row["tenant_id"]),
        email=row["email"],
        name=row["name"],
        active=row["active"],
    )


@router.get("/tenants/{tenant_id}/users", response_model=list[TenantUserResponse])
async def list_tenant_users(tenant_id: str, _admin: AdminUser) -> list[TenantUserResponse]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.tenant_users WHERE tenant_id = $1 ORDER BY created_at",
            tenant_id,
        )
    return [
        TenantUserResponse(
            id=str(r["id"]),
            tenant_id=str(r["tenant_id"]),
            email=r["email"],
            name=r["name"],
            active=r["active"],
        )
        for r in rows
    ]
