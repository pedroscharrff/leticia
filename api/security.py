"""
Centralised security utilities:
  - Password hashing / verification (bcrypt)
  - JWT creation and verification
  - FastAPI dependencies: require_admin, require_tenant_user, require_role
"""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Callable

import bcrypt
import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config import settings

log = structlog.get_logger()

_bearer = HTTPBearer(auto_error=True)

# Role hierarchy: higher index = more permissions
ROLE_HIERARCHY = ["viewer", "operator", "manager", "owner"]


# ── Password ──────────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ── JWT ───────────────────────────────────────────────────────────────────────

def create_access_token(
    sub: str,
    role: str = "admin",
    tenant_id: str | None = None,
    tenant_role: str = "owner",
    name: str | None = None,
) -> str:
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    payload = {
        "sub": sub,
        "role": role,
        "tenant_id": tenant_id,
        "tenant_role": tenant_role,
        "name": name,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")


# ── FastAPI dependencies ──────────────────────────────────────────────────────

def require_admin(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> str:
    payload = _decode_token(credentials.credentials)
    sub: str | None = payload.get("sub")
    if not sub or sub != settings.admin_email:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso negado")
    return sub


class TenantUserContext:
    """Contexto do usuário autenticado da farmácia (com RBAC)."""
    def __init__(
        self,
        email: str,
        tenant_id: str,
        name: str | None = None,
        tenant_role: str = "owner",
    ):
        self.email = email
        self.tenant_id = tenant_id
        self.name = name
        self.tenant_role = tenant_role

    def has_role(self, minimum_role: str) -> bool:
        """Check if user has at least the specified role level."""
        user_level = ROLE_HIERARCHY.index(self.tenant_role) if self.tenant_role in ROLE_HIERARCHY else -1
        min_level = ROLE_HIERARCHY.index(minimum_role) if minimum_role in ROLE_HIERARCHY else 99
        return user_level >= min_level

    def assert_role(self, minimum_role: str) -> None:
        if not self.has_role(minimum_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permissão insuficiente — requer papel '{minimum_role}' ou superior",
            )


def require_tenant_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(_bearer)],
) -> TenantUserContext:
    payload = _decode_token(credentials.credentials)
    if payload.get("role") != "tenant":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso negado")
    tenant_id: str | None = payload.get("tenant_id")
    if not tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso negado")
    return TenantUserContext(
        email=payload["sub"],
        tenant_id=tenant_id,
        name=payload.get("name"),
        tenant_role=payload.get("tenant_role", "owner"),
    )


def require_role(minimum_role: str) -> Callable:
    """Factory that returns a dependency enforcing a minimum RBAC role."""
    def _dep(user: Annotated[TenantUserContext, Depends(require_tenant_user)]) -> TenantUserContext:
        user.assert_role(minimum_role)
        return user
    return _dep
