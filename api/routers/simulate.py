"""
Simulation & traces endpoints.

POST /admin/test/simulate — runs the LangGraph directly (no Celery) and returns
                            the full execution trace for debugging.

GET  /portal/traces       — returns recent agent_traces records for the tenant.
GET  /portal/traces/{id}  — returns a single trace with full step details.
"""
import time
import structlog
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from db.postgres import get_db_conn, tenant_conn
from security import require_admin, require_tenant_user, TenantUserContext

log = structlog.get_logger()

admin_router = APIRouter(prefix="/admin/test", tags=["admin-simulate"])
portal_router = APIRouter(prefix="/portal/traces", tags=["portal-traces"])

AdminUser = Annotated[str, Depends(require_admin)]
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


# ── Pydantic models ───────────────────────────────────────────────────────────

class SimulateRequest(BaseModel):
    tenant_id: str
    phone: str = "5511999990000"
    message: str
    session_id: str | None = None


class TraceStep(BaseModel):
    node: str
    ts_ms: int
    data: dict = {}


class SimulateResponse(BaseModel):
    final_response: str
    selected_skill: str | None
    intent: str | None
    confidence: float | None
    customer_profile: str | None
    latency_ms: int
    trace_steps: list[dict]


class TraceListItem(BaseModel):
    id: str
    session_key: str
    phone: str | None
    message_in: str | None
    final_response: str | None
    skill_used: str | None
    intent: str | None
    confidence: float | None
    latency_ms: int | None
    error: str | None
    created_at: str


class TraceDetail(TraceListItem):
    steps: list[dict]


# ── Admin: simulate ───────────────────────────────────────────────────────────

@admin_router.post("/simulate", response_model=SimulateResponse)
async def simulate(body: SimulateRequest, _admin: AdminUser) -> SimulateResponse:
    """Run the agent graph synchronously (no Celery) and return execution trace."""
    import asyncio
    import redis.asyncio as aioredis
    from config import settings
    from agents.graph_builder import build_graph_for_tenant, TenantConfig, SkillOverride

    # Load tenant from DB
    async with get_db_conn() as conn:
        tenant_row = await conn.fetchrow(
            "SELECT * FROM public.tenants WHERE id = $1 AND active = TRUE",
            body.tenant_id,
        )
    if not tenant_row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    schema_name = tenant_row["schema_name"]

    # Load active skills + overrides
    async with tenant_conn(schema_name) as conn:
        skills_rows = await conn.fetch(
            """
            SELECT skill_name, ativo, llm_model, llm_provider,
                   prompt_version, config_json
            FROM skills_config
            WHERE ativo = TRUE
            """
        )

    active_skills = [r["skill_name"] for r in skills_rows]
    skill_overrides = {
        r["skill_name"]: SkillOverride(
            llm_model=r["llm_model"],
            llm_provider=r["llm_provider"],
            prompt_version=r["prompt_version"] or "v1",
            config_json=r["config_json"] or {},
        )
        for r in skills_rows
        if r["llm_model"] or r["llm_provider"]
    }

    from services.llm_config import load_tenant_llm_config
    llm_cfg = await load_tenant_llm_config(body.tenant_id)

    tenant_config = TenantConfig(
        tenant_id=body.tenant_id,
        schema_name=schema_name,
        callback_url=tenant_row.get("callback_url", ""),
        skills_active=active_skills or ["farmaceutico"],
        skill_overrides=skill_overrides,
        plan=tenant_row.get("plan", "basic"),
        **llm_cfg,
    )

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)

    try:
        graph = build_graph_for_tenant(tenant_config, redis_client)

        session_id = body.session_id or f"{body.tenant_id}:{body.phone}"
        # Only include fields that should change per turn. Stateful fields
        # (messages, cart, customer_profile, stock_mode) MUST NOT be set here
        # or they will overwrite the checkpointed values on every turn.
        initial_state = {
            "tenant_id": body.tenant_id,
            "session_id": session_id,
            "phone": body.phone,
            "schema_name": schema_name,
            "current_message": body.message,
            "available_skills": active_skills or ["farmaceutico"],
            "callback_url": tenant_row.get("callback_url", ""),
            # Per-turn resets (these SHOULD overwrite each turn)
            "intent": "",
            "selected_skill": "",
            "confidence": 0.0,
            "retry_count": 0,
            "analyst_approved": False,
            "escalate": False,
            "final_response": "",
            "trace_steps": [],
        }

        t0 = int(time.time() * 1000)
        # thread_id MUST be stable per session for the checkpointer to restore
        # prior turns (messages, cart, profile). DO NOT include t0 here.
        config = {"configurable": {"thread_id": f"sim:{session_id}"}}
        final_state = await graph.ainvoke(initial_state, config=config)
        latency_ms = int(time.time() * 1000) - t0

        return SimulateResponse(
            final_response=final_state.get("final_response", ""),
            selected_skill=final_state.get("selected_skill"),
            intent=final_state.get("intent"),
            confidence=final_state.get("confidence"),
            customer_profile=final_state.get("customer_profile"),
            latency_ms=latency_ms,
            trace_steps=final_state.get("trace_steps") or [],
        )
    finally:
        await redis_client.aclose()


# ── Portal: traces ────────────────────────────────────────────────────────────

@portal_router.get("", response_model=list[TraceListItem])
async def list_traces(
    user: TenantUser,
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    skill: str | None = Query(default=None),
    phone: str | None = Query(default=None),
) -> list[TraceListItem]:
    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1", user.tenant_id
        )
    if not schema_row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    schema = schema_row["schema_name"]

    filters = ["1=1"]
    params: list = []
    idx = 1

    if skill:
        filters.append(f"skill_used = ${idx}")
        params.append(skill)
        idx += 1
    if phone:
        filters.append(f"phone = ${idx}")
        params.append(phone)
        idx += 1

    where = " AND ".join(filters)
    params += [limit, offset]

    try:
        async with tenant_conn(schema) as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, session_key, phone, message_in, final_response,
                       skill_used, intent, confidence, latency_ms, error, created_at
                FROM agent_traces
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ${idx} OFFSET ${idx+1}
                """,
                *params,
            )
    except Exception:
        return []  # agent_traces table not yet created (migration pending)

    return [
        TraceListItem(
            id=str(r["id"]),
            session_key=r["session_key"],
            phone=r["phone"],
            message_in=r["message_in"],
            final_response=r["final_response"],
            skill_used=r["skill_used"],
            intent=r["intent"],
            confidence=float(r["confidence"]) if r["confidence"] is not None else None,
            latency_ms=r["latency_ms"],
            error=r["error"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]


@portal_router.get("/{trace_id}", response_model=TraceDetail)
async def get_trace(trace_id: str, user: TenantUser) -> TraceDetail:
    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1", user.tenant_id
        )
    if not schema_row:
        raise HTTPException(status_code=404, detail="Tenant não encontrado")

    schema = schema_row["schema_name"]

    async with tenant_conn(schema) as conn:
        row = await conn.fetchrow(
            "SELECT * FROM agent_traces WHERE id = $1", trace_id
        )

    if not row:
        raise HTTPException(status_code=404, detail="Trace não encontrado")

    steps = row["steps"]
    if isinstance(steps, str):
        import json
        steps = json.loads(steps)

    return TraceDetail(
        id=str(row["id"]),
        session_key=row["session_key"],
        phone=row["phone"],
        message_in=row["message_in"],
        final_response=row["final_response"],
        skill_used=row["skill_used"],
        intent=row["intent"],
        confidence=float(row["confidence"]) if row["confidence"] is not None else None,
        latency_ms=row["latency_ms"],
        error=row["error"],
        created_at=row["created_at"].isoformat(),
        steps=steps or [],
    )
