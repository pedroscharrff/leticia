"""
FastAPI application entry point.

Security hardening:
  - CORS restricted to configured origins
  - Per-IP rate limiting via slowapi
  - All admin routes protected with JWT Bearer
  - Webhook routes protected with per-tenant API key
"""
import asyncio
import sys
from pathlib import Path

# Add project root to sys.path so sibling packages (agents/, llm/) are importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# asyncpg requires the SelectorEventLoop on Windows (ProactorEventLoop is incompatible)
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import structlog
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from config import settings
from db.postgres import init_pool, close_pool
from db.redis_client import init_redis, close_redis
from db.migrate import auto_migrate
from routers import webhook, tenants, dashboard, auth, tenant_auth, tenant_portal
from routers.skills import admin_router as skills_admin_router, portal_router as skills_portal_router
from routers.channels import router as channels_router
from routers.inventory import router as inventory_router
from routers.customers import router as customers_router
from routers.billing import router as billing_router
from routers.onboarding import router as onboarding_router
from routers.simulate import admin_router as simulate_admin_router, portal_router as traces_portal_router
from routers.llm_config import portal_router as llm_config_portal_router, admin_router as llm_config_admin_router
from routers.persona import portal_router as persona_portal_router, admin_router as persona_admin_router
from routers.skill_examples import portal_router as examples_portal_router, admin_router as examples_admin_router
from routers.sales_config import portal_router as sales_config_portal_router, admin_router as sales_config_admin_router
from routers.orders import router as orders_router
from routers.order_status_messages import router as order_status_messages_router
from middleware.usage import UsageEnforcementMiddleware

# ── Structured logging ────────────────────────────────────────────────────────

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        getattr(__import__("logging"), settings.log_level)
    ),
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ],
)

log = structlog.get_logger()

# ── Rate limiter ──────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address, default_limits=["200/minute"])


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        await init_pool()
    except Exception as exc:
        log.warning("db.pool.unavailable", error=str(exc))
    try:
        await init_redis()
    except Exception as exc:
        log.warning("redis.unavailable", error=str(exc))
    try:
        await auto_migrate()
    except Exception as exc:
        log.warning("migrations.failed", error=str(exc))
    log.info("app.started", env=settings.environment)
    yield
    await close_pool()
    await close_redis()
    log.info("app.stopped")


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="SaaS Farmácia — Atendimento Inteligente",
    version="1.0.0",
    docs_url="/docs" if settings.environment != "production" else None,
    redoc_url="/redoc" if settings.environment != "production" else None,
    lifespan=lifespan,
)

# Rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# Usage enforcement (must come before CORS so it runs on webhook routes)
app.add_middleware(UsageEnforcementMiddleware)

# CORS — only allow configured origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Api-Key"],
)

# Prometheus metrics
Instrumentator().instrument(app).expose(app, include_in_schema=False)

# Routers
app.include_router(auth.router)
app.include_router(tenant_auth.router)
app.include_router(tenant_portal.router)
app.include_router(webhook.router)
app.include_router(tenants.router)
app.include_router(dashboard.router)
# New SaaS routers
app.include_router(skills_admin_router)
app.include_router(skills_portal_router)
app.include_router(channels_router)
app.include_router(inventory_router)
app.include_router(customers_router)
app.include_router(billing_router)
app.include_router(onboarding_router)
app.include_router(simulate_admin_router)
app.include_router(traces_portal_router)
app.include_router(llm_config_portal_router)
app.include_router(llm_config_admin_router)
app.include_router(persona_portal_router)
app.include_router(persona_admin_router)
app.include_router(examples_portal_router)
app.include_router(examples_admin_router)
app.include_router(sales_config_portal_router)
app.include_router(sales_config_admin_router)
app.include_router(orders_router)
app.include_router(order_status_messages_router)


@app.get("/health", tags=["infra"])
async def health() -> dict:
    return {"status": "ok"}


# ── Global error handlers ─────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error("unhandled_exception", path=request.url.path, exc=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Erro interno do servidor"},
    )
