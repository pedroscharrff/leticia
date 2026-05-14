"""
POST /auth/login  — exchange admin credentials for a JWT access token.

The admin password is stored as a bcrypt hash in the ADMIN_PASSWORD_HASH
environment variable.  On first run, generate the hash with:

    python -c "from security import hash_password; print(hash_password('your_password'))"
"""
import structlog
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from slowapi import Limiter
from slowapi.util import get_remote_address

from config import settings
from security import create_access_token, verify_password

log = structlog.get_logger()
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


@router.post("/login", response_model=TokenResponse)
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest) -> TokenResponse:
    """
    Rate-limited login endpoint.  Returns a JWT valid for
    jwt_access_token_expire_minutes minutes.
    """
    # Constant-time comparison to prevent user enumeration
    email_ok = body.email == settings.admin_email
    password_ok = verify_password(body.password, settings.admin_password_hash) if settings.admin_password_hash else False

    if not (email_ok and password_ok):
        log.warning("auth.login.failed", email=body.email, ip=request.client.host if request.client else "unknown")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciais inválidas",
        )

    token = create_access_token(sub=settings.admin_email)
    log.info("auth.login.success", email=body.email)

    return TokenResponse(
        access_token=token,
        expires_in=settings.jwt_access_token_expire_minutes * 60,
    )
