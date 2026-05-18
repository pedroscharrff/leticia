"""
Persistence helper for `<schema>.agent_traces`.

Fire-and-forget: never raises so the agent flow is not disrupted if the trace
table is missing or the write fails. Called after every graph.ainvoke() in
production (broker + legacy whatsapp webhook) so the portal /traces page can
show real conversations.
"""
from __future__ import annotations

import json
from typing import Any

import structlog

from db.postgres import get_db_conn

log = structlog.get_logger()


async def persist_trace(
    *,
    schema_name: str,
    session_key: str,
    phone: str | None,
    message_in: str,
    final_state: dict[str, Any] | None,
    latency_ms: int,
    error: str | None = None,
) -> None:
    steps = []
    final_response = ""
    skill_used = "unknown"
    intent = ""
    confidence: float | None = None

    if final_state:
        steps = final_state.get("trace_steps") or []
        final_response = final_state.get("final_response") or ""
        skill_used = final_state.get("selected_skill") or "unknown"
        intent = (final_state.get("intent") or "")[:200]
        raw_conf = final_state.get("confidence")
        if raw_conf is not None:
            try:
                confidence = round(float(raw_conf), 3)
            except (TypeError, ValueError):
                confidence = None

    try:
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")
            await conn.execute(
                """
                INSERT INTO agent_traces
                  (session_key, phone, message_in, steps, final_response,
                   skill_used, intent, confidence, latency_ms, error)
                VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8, $9, $10)
                """,
                session_key,
                phone,
                message_in,
                json.dumps(steps, default=str),
                final_response,
                skill_used,
                intent,
                confidence,
                latency_ms,
                error,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "agent_traces.write_failed",
            schema=schema_name,
            session=session_key,
            error=str(exc),
        )
