"""
Persistence helper for `<schema>.agent_traces`.

Fire-and-forget: never raises so the agent flow is not disrupted if the trace
table is missing or the write fails. Called after every graph.ainvoke() in
production (broker + legacy whatsapp webhook) so the portal /traces page can
show real conversations.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog

from db.postgres import get_db_conn

log = structlog.get_logger()


def _json_safe(value: Any) -> Any:
    """Converte recursivamente tipos não-JSON-nativos (datetime, UUID, Decimal)
    em equivalentes serializáveis. Substitui o `default=str` que o codec
    asyncpg.jsonb não suporta.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


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

    # asyncpg já tem codec jsonb configurado em db/postgres.py:25 que faz
    # json.dumps() automaticamente. Fazer json.dumps() aqui causava double-
    # encoding (jsonb scalar string em vez de array), inutilizando queries
    # como jsonb_array_elements(steps). Passamos a lista crua e deixamos o
    # codec serializar. `default=str` foi substituído por uma normalização
    # explícita já que o codec usa json.dumps padrão (sem default).
    safe_steps = _json_safe(steps)
    try:
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")
            await conn.execute(
                """
                INSERT INTO agent_traces
                  (session_key, phone, message_in, steps, final_response,
                   skill_used, intent, confidence, latency_ms, error)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                session_key,
                phone,
                message_in,
                safe_steps,
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
