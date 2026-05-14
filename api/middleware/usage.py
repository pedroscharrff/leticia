"""
Usage enforcement middleware.
Intercepts /webhook/* requests and:
  1. Checks monthly message limit for the tenant
  2. Increments the Redis counter
  3. Returns 402 if limit exceeded or subscription suspended
"""
from __future__ import annotations

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from services.billing import check_usage_allowed, increment_usage

log = structlog.get_logger()


class UsageEnforcementMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        if not request.url.path.startswith("/webhook/"):
            return await call_next(request)

        # Extract tenant_id from path: /webhook/{channel}/{tenant_id}/...
        parts = request.url.path.split("/")
        tenant_id = parts[3] if len(parts) > 3 else None

        if tenant_id:
            allowed, reason = await check_usage_allowed(tenant_id)
            if not allowed:
                log.warning("usage.blocked", tenant=tenant_id, reason=reason)
                return JSONResponse(
                    status_code=402,
                    content={"detail": reason, "upgrade_url": "/portal/billing"},
                )
            await increment_usage(tenant_id, "msgs")

        return await call_next(request)
