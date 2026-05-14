"""
Audit log helper — fire-and-forget async write to public.audit_events.
Never raises; logs errors internally so callers are never disrupted.
"""
from __future__ import annotations

import structlog
from fastapi import Request

from db.postgres import get_db_conn

log = structlog.get_logger()


async def log_event(
    action: str,
    actor_id: str,
    actor_type: str = "user",
    tenant_id: str | None = None,
    target: str | None = None,
    meta: dict | None = None,
    request: Request | None = None,
) -> None:
    ip = None
    if request:
        forwarded = request.headers.get("X-Forwarded-For")
        ip = forwarded.split(",")[0].strip() if forwarded else request.client.host if request.client else None

    try:
        async with get_db_conn() as conn:
            await conn.execute(
                """
                INSERT INTO public.audit_events
                    (tenant_id, actor_type, actor_id, action, target, meta, ip_addr)
                VALUES ($1, $2, $3, $4, $5, $6, $7::inet)
                """,
                tenant_id, actor_type, actor_id, action, target,
                meta or {}, ip,
            )
    except Exception as exc:
        log.warning("audit.write_failed", action=action, actor=actor_id, error=str(exc))
