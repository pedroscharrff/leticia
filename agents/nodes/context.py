"""
Nodes: load_context  /  save_context

load_context — antes do orchestrator:
  • Carrega histórico da conversa do Redis
  • Carrega persona e prompts customizados do DB
  • Carrega perfil do cliente

save_context — ao final do grafo:
  • Persiste a nova mensagem no histórico (Redis)
  • Grava log de conversa no PostgreSQL
"""
from __future__ import annotations

import json
import time
import structlog

from agents.state import AgentState

log = structlog.get_logger()

_TTL = 1800  # segundos (30 min) — mesmo que SESSION_TTL_SECONDS


# ── load_context ──────────────────────────────────────────────────────────────

async def load_context(state: AgentState) -> AgentState:
    """Carrega histórico, persona e perfil do cliente."""
    session_id   = state["session_id"]
    tenant_id    = state["tenant_id"]
    schema_name  = state["schema_name"]
    max_messages = 10

    updates: dict = {}

    # ── Histórico do Redis ────────────────────────────────────────────────────
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        raw = await redis.get(f"hist:{session_id}")
        messages = json.loads(raw) if raw else []
        # Trunca para as últimas N mensagens para economizar tokens
        updates["messages"] = messages[-max_messages:]
    except Exception as exc:
        log.warning("context.redis.load_failed", session=session_id, exc=str(exc))
        updates["messages"] = []

    # ── Persona e prompts do PostgreSQL ───────────────────────────────────────
    try:
        from db.postgres import get_db_conn
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")

            # Persona
            persona_row = await conn.fetchrow(
                "SELECT name, tone, language, greeting, farewell, extra_instructions FROM persona LIMIT 1"
            )
            if persona_row:
                updates["persona"] = dict(persona_row)

            # Prompts customizados por skill
            prompt_rows = await conn.fetch(
                "SELECT skill_name, system_prompt FROM skill_examples WHERE system_prompt IS NOT NULL"
            )
            updates["skill_prompts"] = {r["skill_name"]: r["system_prompt"] for r in prompt_rows}

            # Perfil do cliente
            session_row = await conn.fetchrow(
                "SELECT customer_profile FROM sessions WHERE session_key = $1",
                session_id,
            )
            if session_row and session_row["customer_profile"]:
                updates["customer_profile"] = session_row["customer_profile"]

    except Exception as exc:
        log.warning("context.db.load_failed", tenant=tenant_id, exc=str(exc))

    return {**state, **updates}


# ── save_context ──────────────────────────────────────────────────────────────

async def save_context(state: AgentState) -> AgentState:
    """Persiste conversa no Redis e log no PostgreSQL."""
    session_id     = state["session_id"]
    schema_name    = state["schema_name"]
    current_msg    = state.get("current_message", "")
    final_response = state.get("final_response", "")
    skill_used     = state.get("selected_skill", "unknown")

    # Atualiza histórico
    messages = list(state.get("messages", []))
    messages.append({"role": "user",      "content": current_msg})
    messages.append({"role": "assistant", "content": final_response})

    # ── Redis ─────────────────────────────────────────────────────────────────
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        await redis.setex(f"hist:{session_id}", _TTL, json.dumps(messages))
    except Exception as exc:
        log.warning("context.redis.save_failed", session=session_id, exc=str(exc))

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    try:
        from db.postgres import get_db_conn
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")

            # Upsert de sessão
            await conn.execute(
                """
                INSERT INTO sessions (session_key, phone, turn_count, updated_at)
                VALUES ($1, $2, 1, NOW())
                ON CONFLICT (session_key) DO UPDATE
                SET turn_count = sessions.turn_count + 1,
                    updated_at = NOW()
                """,
                session_id,
                state.get("phone", ""),
            )

            # Log de conversa
            await conn.execute(
                """
                INSERT INTO conversation_logs
                    (session_key, role, content, skill_used, created_at)
                VALUES ($1, 'user',      $2, $4, NOW()),
                       ($1, 'assistant', $3, $4, NOW())
                """,
                session_id,
                current_msg,
                final_response,
                skill_used,
            )

    except Exception as exc:
        log.warning("context.db.save_failed", session=session_id, exc=str(exc))

    return {**state, "messages": messages}
