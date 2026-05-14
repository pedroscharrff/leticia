"""
Tenant secret management — app-layer encryption using Fernet.

All third-party credentials (WhatsApp tokens, SQL DSNs, LLM keys) are
stored as Fernet-encrypted blobs so the database alone cannot leak them.
"""
from __future__ import annotations

import structlog
from cryptography.fernet import Fernet, InvalidToken

from config import settings
from db.postgres import get_db_conn

log = structlog.get_logger()

_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        key = settings.encryption_key
        if not key:
            raise RuntimeError("ENCRYPTION_KEY not configured")
        _fernet = Fernet(key.encode())
    return _fernet


def encrypt(value: str) -> bytes:
    return _get_fernet().encrypt(value.encode())


def decrypt(blob: bytes) -> str:
    try:
        return _get_fernet().decrypt(blob).decode()
    except InvalidToken as exc:
        raise ValueError("Failed to decrypt secret — wrong key or tampered data") from exc


async def set_secret(tenant_id: str, key: str, value: str) -> None:
    enc = encrypt(value)
    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.tenant_secrets (tenant_id, key, value_enc)
            VALUES ($1, $2, $3)
            ON CONFLICT (tenant_id, key) DO UPDATE
                SET value_enc = EXCLUDED.value_enc, updated_at = NOW()
            """,
            tenant_id, key, enc,
        )
    log.info("secret.set", tenant=tenant_id, key=key)


async def get_secret(tenant_id: str, key: str) -> str | None:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT value_enc FROM public.tenant_secrets WHERE tenant_id = $1 AND key = $2",
            tenant_id, key,
        )
    if not row:
        return None
    return decrypt(bytes(row["value_enc"]))


async def delete_secret(tenant_id: str, key: str) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            "DELETE FROM public.tenant_secrets WHERE tenant_id = $1 AND key = $2",
            tenant_id, key,
        )


async def list_secret_keys(tenant_id: str) -> list[str]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT key FROM public.tenant_secrets WHERE tenant_id = $1 ORDER BY key",
            tenant_id,
        )
    return [r["key"] for r in rows]
