"""
Celery application and the main process_message task.

Each task:
1. Fetches the tenant's active skills from PostgreSQL
2. Builds the LangGraph for that tenant
3. Invokes the graph with the incoming message
4. POSTs the final response to the tenant's callback_url (with retry)
"""
import asyncio
import json
import time

import httpx
import structlog
from celery import Celery
from prometheus_client import Counter, Histogram
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings

log = structlog.get_logger()

# ── Celery app ────────────────────────────────────────────────────────────────

celery_app = Celery(
    "saas_farmacia",
    broker=settings.rabbitmq_url,
    backend=f"redis://{settings.redis_url.split('redis://')[-1]}",
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.celery_workers_concurrency,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

# ── Prometheus metrics ────────────────────────────────────────────────────────

CONV_TOTAL = Counter(
    "conversations_total",
    "Total conversations processed",
    ["tenant_id", "skill", "status"],
)
LATENCY = Histogram(
    "conversation_latency_seconds",
    "End-to-end conversation latency",
    ["tenant_id", "skill"],
)
LLM_ERRORS = Counter(
    "llm_errors_total",
    "LLM call failures",
    ["tenant_id", "skill", "llm_model"],
)


# ── Callback delivery ─────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _deliver_response(callback_url: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(callback_url, json=payload)
        resp.raise_for_status()


# ── Main task ─────────────────────────────────────────────────────────────────

@celery_app.task(name="process_message", bind=True, max_retries=0)
def process_message(
    self,
    tenant_id: str,
    schema_name: str,
    callback_url: str,
    phone: str,
    session_id: str,
    current_message: str,
) -> None:
    asyncio.run(
        _run_graph(
            tenant_id=tenant_id,
            schema_name=schema_name,
            callback_url=callback_url,
            phone=phone,
            session_id=session_id,
            current_message=current_message,
        )
    )


async def _run_graph(
    tenant_id: str,
    schema_name: str,
    callback_url: str,
    phone: str,
    session_id: str,
    current_message: str,
) -> None:
    from db.postgres import get_db_conn, init_pool
    from db.redis_client import get_redis, init_redis
    from agents.graph_builder import build_graph_for_tenant, TenantConfig
    from services.llm_config import load_tenant_llm_config

    # Lazy-init connections inside the async loop (worker process)
    await init_pool()
    await init_redis()

    redis = get_redis()

    # Fetch active skills and LLM config for this tenant
    async with get_db_conn() as conn:
        await conn.execute(f"SET search_path = {schema_name}, public")
        rows = await conn.fetch(
            "SELECT skill_name FROM skills_config WHERE ativo = TRUE"
        )
        active_skills = [r["skill_name"] for r in rows]

    llm_cfg = await load_tenant_llm_config(tenant_id)

    tenant_cfg = TenantConfig(
        tenant_id=tenant_id,
        schema_name=schema_name,
        callback_url=callback_url,
        skills_active=active_skills,
        **llm_cfg,
    )

    graph = build_graph_for_tenant(tenant_cfg, redis)

    initial_state = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "phone": phone,
        "schema_name": schema_name,
        "current_message": current_message,
        "messages": [],
        "intent": "",
        "selected_skill": "",
        "confidence": 0.0,
        "retry_count": 0,
        "customer_profile": "indefinido",
        "cart": {"items": [], "subtotal": 0.0},
        "stock_mode": "catalogo",
        "available_skills": active_skills,
        "analyst_approved": False,
        "final_response": "",
        "escalate": False,
        "callback_url": callback_url,
        "trace_steps": [],
        "persona": {},
        "skill_prompts": {},
    }

    config = {"configurable": {"thread_id": session_id}}

    t0 = time.monotonic()
    skill_used = "unknown"

    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        skill_used = final_state.get("selected_skill", "unknown")
        response_text = final_state.get("final_response", "")

        elapsed = time.monotonic() - t0
        LATENCY.labels(tenant_id=tenant_id, skill=skill_used).observe(elapsed)
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="ok").inc()

        await _deliver_response(
            callback_url,
            {
                "phone": phone,
                "session_id": session_id,
                "message": response_text,
                "tenant_id": tenant_id,
            },
        )

        log.info(
            "task.done",
            tenant=tenant_id,
            session=session_id,
            skill=skill_used,
            elapsed_s=round(elapsed, 2),
        )

    except Exception as exc:  # noqa: BLE001
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="error").inc()
        log.error("task.failed", tenant=tenant_id, session=session_id, exc=str(exc))
        # Best-effort: notify the callback with an error payload
        try:
            await _deliver_response(
                callback_url,
                {
                    "phone": phone,
                    "session_id": session_id,
                    "message": "Ocorreu um erro no atendimento. Por favor, tente novamente.",
                    "tenant_id": tenant_id,
                    "error": True,
                },
            )
        except Exception:
            pass
        raise
