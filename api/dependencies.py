from fastapi import Header, HTTPException, status
from sqlalchemy import text

from db.postgres import get_db_conn
from models.tenant import TenantRow


async def resolve_tenant(
    tenant_id: str,
    x_api_key: str = Header(..., alias="X-Api-Key"),
) -> TenantRow:
    """Validate API key and return the tenant row."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenants WHERE id = $1 AND api_key = $2 AND active = TRUE",
            tenant_id,
            x_api_key,
        )

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key or tenant not found",
        )

    return TenantRow(**dict(row))
