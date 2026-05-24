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
    # Beat schedule — jobs proativos (capability-gated por dentro)
    beat_schedule={
        "recover_abandoned_carts": {
            "task":     "jobs.recover_abandoned_carts",
            "schedule": 60 * 60,           # 1 hora
        },
        "nudge_continuous_refill": {
            # 24h: roda 1x por dia. O job interno respeita time_of_day da
            # config (futuro) — por ora, executa sempre quando o beat acorda.
            "task":     "jobs.nudge_continuous_refill",
            "schedule": 60 * 60 * 24,
        },
    },
)


# ── Beat tasks (capability-gated dentro dos jobs) ───────────────────────────

@celery_app.task(name="jobs.recover_abandoned_carts", bind=True, max_retries=0)
def recover_abandoned_carts_task(self) -> dict:
    """Task agendada — chama o job sync que lê a flag por tenant."""
    from workers.jobs.abandoned_cart import recover_abandoned_carts_sync
    try:
        return recover_abandoned_carts_sync()
    except Exception as exc:  # noqa: BLE001
        log.warning("celery.recover_failed", exc=str(exc))
        return {"error": str(exc)}


@celery_app.task(name="jobs.nudge_continuous_refill", bind=True, max_retries=0)
def nudge_continuous_refill_task(self) -> dict:
    from workers.jobs.refill_nudge import nudge_continuous_refill_sync
    try:
        return nudge_continuous_refill_sync()
    except Exception as exc:  # noqa: BLE001
        log.warning("celery.refill_failed", exc=str(exc))
        return {"error": str(exc)}

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
    media: dict | None = None,
) -> None:
    asyncio.run(
        _run_graph(
            tenant_id=tenant_id,
            schema_name=schema_name,
            callback_url=callback_url,
            phone=phone,
            session_id=session_id,
            current_message=current_message,
            media=media,
        )
    )


async def _run_graph(
    tenant_id: str,
    schema_name: str,
    callback_url: str,
    phone: str,
    session_id: str,
    current_message: str,
    media: dict | None = None,
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

    if media:
        initial_state.update({
            "media_type": media.get("media_type"),
            "media_mime": media.get("media_mime"),
            "media_url":  media.get("media_url"),
            "media_id":   media.get("media_id"),
            "media_b64":  media.get("media_b64"),
        })

    config = {"configurable": {"thread_id": session_id}}

    t0 = time.monotonic()
    skill_used = "unknown"
    final_state: dict | None = None
    trace_error: str | None = None

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
        trace_error = str(exc)
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
    finally:
        from services.agent_traces import persist_trace
        await persist_trace(
            schema_name=schema_name,
            session_key=session_id,
            phone=phone,
            message_in=current_message,
            final_state=final_state,
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=trace_error,
        )


# ── Broker bundled task (debounce — agrupa mensagens picadas) ───────────────

@celery_app.task(name="process_bundled_message", bind=True, max_retries=0)
def process_bundled_message(
    self,
    tenant_id: str,
    integration_id: str,
    bundle_key: str,
    scheduled_for_ts: float,
) -> None:
    """
    Debounce processor.

    Cada mensagem que chega agenda esta task com countdown=window. Quando
    rodamos:
      - lemos `last_seen` do Redis. Se for > nosso `scheduled_for_ts`,
        significa que chegou mensagem nova depois de nós — então DESISTIMOS
        (uma task mais recente vai processar o bundle completo).
      - caso contrário, pegamos todas as mensagens do buffer, concatenamos
        com quebra de linha, e disparamos o fluxo do agente com o texto
        combinado.
    """
    asyncio.run(_run_bundle(
        tenant_id=tenant_id,
        integration_id=integration_id,
        bundle_key=bundle_key,
        scheduled_for_ts=scheduled_for_ts,
    ))


async def _run_bundle(
    tenant_id: str,
    integration_id: str,
    bundle_key: str,
    scheduled_for_ts: float,
) -> None:
    from db.postgres import init_pool
    from db.redis_client import get_redis, init_redis
    import json as _json

    await init_pool()
    await init_redis()
    redis = get_redis()

    last_seen_raw = await redis.get(f"{bundle_key}:last_seen")
    try:
        last_seen = float(last_seen_raw) if last_seen_raw else 0.0
    except (ValueError, TypeError):
        last_seen = 0.0

    # Outra mensagem chegou depois desta task ser agendada → desiste.
    # A task agendada por aquela mensagem mais recente vai processar tudo.
    if last_seen > scheduled_for_ts:
        log.info("bundle.skipped_newer_arrived",
                 bundle_key=bundle_key,
                 scheduled=scheduled_for_ts, last_seen=last_seen)
        return

    # Pega tudo do buffer e limpa
    items_raw = await redis.lrange(bundle_key, 0, -1)
    await redis.delete(bundle_key, f"{bundle_key}:last_seen")

    if not items_raw:
        return

    items = [_json.loads(i) for i in items_raw]
    combined_message = "\n".join(it["msg"].strip()
                                 for it in items if (it.get("msg") or "").strip())

    # Usa o canonical_input da última mensagem como base e sobrescreve message
    base_input = items[-1].get("input") or {}
    last_event_id = items[-1].get("event_id") or ""

    # Se o último item carrega mídia (áudio/imagem), preservamos a mídia
    # mesmo sem texto — o ingest_media node vai transcrever/descrever.
    has_media = bool(base_input.get("media_type"))
    if not combined_message and not has_media:
        # Nada útil para processar
        return

    canonical_input = {**base_input, "message": combined_message}

    log.info("bundle.processing",
             bundle_key=bundle_key,
             count=len(items),
             combined_len=len(combined_message))

    await _run_broker_flow(
        tenant_id=tenant_id,
        integration_id=integration_id,
        raw_event_id=last_event_id,
        canonical_input=canonical_input,
    )


# ── Broker task (universal webhook flow) ─────────────────────────────────────

@celery_app.task(name="process_broker_message", bind=True, max_retries=0)
def process_broker_message(
    self,
    tenant_id: str,
    integration_id: str,
    raw_event_id: str,
    canonical_input: dict,
) -> None:
    """
    Runs the agent for a webhook event ingested via /hooks/*.

    After the agent finishes:
      - Applies the integration's reply_body_template against
        {input, reply, phone, message, name, session_id, event_id}
      - If reply_mode == 'forward', POSTs the body to reply_url.
      - Updates the broker_raw_events row with the final canonical payload.
    """
    asyncio.run(_run_broker_flow(
        tenant_id=tenant_id,
        integration_id=integration_id,
        raw_event_id=raw_event_id,
        canonical_input=canonical_input,
    ))


async def _run_broker_flow(
    tenant_id: str,
    integration_id: str,
    raw_event_id: str,
    canonical_input: dict,
) -> None:
    from db.postgres import get_db_conn, init_pool
    from db.redis_client import get_redis, init_redis
    from agents.graph_builder import build_graph_for_tenant, TenantConfig
    from services.llm_config import load_tenant_llm_config
    from services import broker as broker_svc

    await init_pool()
    await init_redis()
    redis = get_redis()

    async with get_db_conn() as conn:
        tenant = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1", tenant_id,
        )
        integration = await conn.fetchrow(
            "SELECT * FROM public.tenant_integrations WHERE id = $1", integration_id,
        )

    if not tenant or not integration:
        log.error("broker.flow.missing_records", tenant=tenant_id, integration=integration_id)
        return

    schema_name = tenant["schema_name"]
    phone = canonical_input.get("phone") or ""
    # Sanitize phone: keep only digits (Z-API/WhatsApp formats add ":21@s.whatsapp.net")
    phone_clean = "".join(c for c in phone if c.isdigit())[:20] or "unknown"
    message = canonical_input.get("message") or ""
    session_id = canonical_input.get("session_id") or phone_clean

    # Safety net: se canonical_input não trouxer mídia, tenta auto-detectar
    # no payload bruto do evento (cobre o caso de canonical antigo ou
    # bundling com versão anterior do código).
    if not canonical_input.get("media_type") and raw_event_id:
        try:
            from services.media_detect import detect_media
            async with get_db_conn() as conn:
                raw_row = await conn.fetchrow(
                    "SELECT payload FROM public.broker_raw_events WHERE id = $1",
                    raw_event_id,
                )
            if raw_row and raw_row["payload"]:
                detected = detect_media(raw_row["payload"])
                if detected:
                    canonical_input.update(detected)
                    log.info("broker.flow.media_recovered_from_raw",
                             media_type=detected["media_type"],
                             event_id=raw_event_id)
        except Exception as exc:
            log.warning("broker.flow.media_recovery_failed", exc=str(exc))

    # Load active skills + LLM config
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
        callback_url="",   # not used; we control the reply ourselves
        skills_active=active_skills,
        **llm_cfg,
    )

    graph = build_graph_for_tenant(tenant_cfg, redis)

    initial_state = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "phone": phone_clean,
        "schema_name": schema_name,
        "current_message": message,
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
        "callback_url": "",
        "trace_steps": [],
        "persona": {},
        "skill_prompts": {},
    }

    # Pass-through de mídia mapeada pelo broker (Z-API/WA Cloud → canonical)
    if canonical_input.get("media_type"):
        initial_state.update({
            "media_type": canonical_input.get("media_type"),
            "media_mime": canonical_input.get("media_mime"),
            "media_url":  canonical_input.get("media_url"),
            "media_id":   canonical_input.get("media_id"),
            "media_b64":  canonical_input.get("media_b64"),
        })

    config = {"configurable": {"thread_id": session_id}}
    t0 = time.monotonic()
    skill_used = "broker"
    reply_text = ""
    error: str | None = None
    final_state: dict | None = None

    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        skill_used = final_state.get("selected_skill", "unknown")
        reply_text = final_state.get("final_response", "")
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="ok").inc()
        LATENCY.labels(tenant_id=tenant_id, skill=skill_used).observe(time.monotonic() - t0)
    except Exception as exc:
        error = str(exc)
        reply_text = "Ocorreu um erro no atendimento. Por favor, tente novamente."
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="error").inc()
        log.error("broker.flow.agent_failed", tenant=tenant_id, exc=error)

    # ── Handoff p/ atendente humano (balcão) ─────────────────────────────────
    # Roda DEPOIS do agente. Decide se transfere com base em:
    #   - escalate=True sinalizado pelo agente (guardrails, analyst, etc.)
    #   - palavra-chave configurada batendo na mensagem do cliente
    # Se transferir, sobrescreve reply_text pela mensagem de transferência
    # e dispara o POST para a API externa (ClickMassa / TalkFarma / ...).
    handoff_cfg = integration.get("handoff_config") or {}
    agent_escalate = bool(final_state.get("escalate")) if final_state else False
    handoff_result: dict | None = None
    try:
        from services.handoff import should_handoff, transfer_to_human
        do_handoff, reason = should_handoff(
            handoff_cfg,
            agent_escalate=agent_escalate,
            user_message=message,
        )
        if do_handoff:
            log.info("broker.flow.handoff_triggered",
                     tenant=tenant_id, reason=reason, phone_prefix=phone_clean[:4])
            handoff_result = await transfer_to_human(
                handoff_cfg, phone=phone_clean,
                # Se o agente já gerou uma resposta de despedida, prefere ela;
                # senão usa a transfer_message configurada.
                custom_message=reply_text if reply_text and agent_escalate else None,
            )
            # Mostra ao cliente a mensagem de transferência (em vez da resposta
            # padrão do agente) só quando o agente NÃO foi quem pediu — quando
            # o agente já respondeu algo sensato (ex: emergência), mantemos.
            if not agent_escalate:
                reply_text = (handoff_cfg.get("transfer_message")
                              or "Estou te transferindo para um atendente agora. Um momento, por favor.")
            skill_used = "handoff"
    except Exception as exc:
        log.error("broker.flow.handoff_dispatch_failed", error=str(exc))
        handoff_result = {"ok": False, "error": f"Erro no dispatcher de handoff: {exc}",
                          "status_code": None, "response": None}

    from services.agent_traces import persist_trace
    await persist_trace(
        schema_name=schema_name,
        session_key=session_id,
        phone=phone_clean,
        message_in=message,
        final_state=final_state,
        latency_ms=int((time.monotonic() - t0) * 1000),
        error=error,
    )

    # Build reply body from template
    reply_context = {
        "input": canonical_input,
        "reply": reply_text,
        "phone": phone,
        "message": message,
        "name": canonical_input.get("name"),
        "session_id": session_id,
        "event_id": raw_event_id,
    }
    template = integration["reply_body_template"] or {}
    reply_body = (
        broker_svc.apply_mapping(template, reply_context)
        if template else {"reply": reply_text}
    )

    # Forward to external URL if configured — captura status + body
    forward_status: int | None = None
    forward_response: dict | None = None
    forward_error: str | None = None

    if integration["reply_mode"] == "forward" and integration["reply_url"]:
        method = (integration.get("reply_method") or "POST").upper()
        headers = {str(k): str(v) for k, v in
                   (integration.get("reply_headers") or {}).items() if k and v}
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.request(
                    method, integration["reply_url"],
                    json=reply_body, headers=headers,
                )
            forward_status = resp.status_code
            try:
                forward_response = resp.json()
            except Exception:
                forward_response = {"_text": resp.text[:2000]}

            if 200 <= resp.status_code < 300:
                log.info("broker.flow.forwarded",
                         tenant=tenant_id, url=integration["reply_url"],
                         status=resp.status_code)
            else:
                forward_error = f"Gateway externo retornou {resp.status_code}"
                log.warning("broker.flow.forward_bad_status",
                            tenant=tenant_id, url=integration["reply_url"],
                            status=resp.status_code,
                            response_preview=str(forward_response)[:300])
        except Exception as exc:
            forward_error = f"Falha ao conectar no destino: {exc}"
            log.warning("broker.flow.forward_failed",
                        tenant=tenant_id, url=integration["reply_url"], exc=str(exc))

    # Persist final state (com info do forward, se houve)
    canonical_combined = {**reply_context, "_reply_body": reply_body, "_error": error}
    if handoff_result is not None:
        canonical_combined["_handoff"] = handoff_result
    final_status = (
        "failed" if (error or forward_error) else "processed"
    )
    final_error = error or forward_error

    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.broker_raw_events "
            "SET status=$2, canonical_event='agent.message', canonical_payload=$3, "
            "    attempts=attempts+1, processed_at=NOW(), error=$4, "
            "    forward_url=$5, forward_status_code=$6, forward_response=$7 "
            "WHERE id=$1",
            raw_event_id,
            final_status,
            canonical_combined,
            final_error,
            integration["reply_url"] if integration["reply_mode"] == "forward" else None,
            forward_status,
            forward_response,
        )
