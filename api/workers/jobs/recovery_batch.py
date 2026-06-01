"""
Job: process_recovery_batch — processa um row de `public.recovery_batches`.

Disparado pelo endpoint `POST /portal/recovery/trigger` (não pelo beat). Para
cada session_key da lista:
  1. Checa se o batch foi cancelado (DB) → sai cedo.
  2. Aguarda 200ms entre disparos (rate-limit pra não martelar o gateway).
  3. Carrega cart + customer no schema do tenant.
  4. Envia via `send_proactive_message` (caminho canônico do broker).
  5. Marca `sent_recovery_at` / incrementa `recovery_attempts` no cart.
  6. Atualiza contadores do batch.

Tolerante a falha individual: erro num cart não derruba o batch inteiro.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

from db.postgres import get_db_conn, init_pool
from services.outbound import send_proactive_message
from services.persona import load_persona
from workers.jobs.abandoned_cart import _build_message

log = structlog.get_logger()

# Pausa entre envios (segundos). Mantém ~5 msg/s por tenant. Generoso o
# suficiente pra qualquer gateway respirar e pra cancel_requested propagar.
_SEND_INTERVAL_S = 0.2

# A cada N envios, recarrega o flag `cancel_requested` do batch. Evita ler o
# DB a cada iteração quando o batch é grande.
_CANCEL_CHECK_EVERY = 5


async def _load_batch(batch_id: str) -> dict | None:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, tenant_id, schema_name, actor_email, status, total,
                   sent, failed, skipped, session_keys, sent_session_keys,
                   cancel_requested
              FROM public.recovery_batches
             WHERE id = $1
            """,
            batch_id,
        )
    return dict(row) if row else None


async def _is_cancelled(batch_id: str) -> bool:
    async with get_db_conn() as conn:
        v = await conn.fetchval(
            "SELECT cancel_requested FROM public.recovery_batches WHERE id = $1",
            batch_id,
        )
    return bool(v)


async def _mark_started(batch_id: str) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.recovery_batches "
            "SET status='running', started_at=NOW() "
            "WHERE id=$1 AND status='queued'",
            batch_id,
        )


async def _record_send(batch_id: str, session_key: str, ok: bool) -> None:
    """Atualiza contadores + lista de sent_session_keys em UMA transação curta."""
    async with get_db_conn() as conn:
        if ok:
            await conn.execute(
                """
                UPDATE public.recovery_batches
                   SET sent = sent + 1,
                       sent_session_keys = sent_session_keys || to_jsonb($2::text)
                 WHERE id = $1
                """,
                batch_id, session_key,
            )
        else:
            await conn.execute(
                "UPDATE public.recovery_batches SET failed = failed + 1 WHERE id = $1",
                batch_id,
            )


async def _bump_skipped(batch_id: str) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.recovery_batches SET skipped = skipped + 1 WHERE id = $1",
            batch_id,
        )


async def _finish(batch_id: str, status: str, error: str | None = None) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            """
            UPDATE public.recovery_batches
               SET status = $2, finished_at = NOW(), error = $3
             WHERE id = $1
            """,
            batch_id, status, error,
        )


async def _load_cart_and_customer(
    schema: str, session_key: str
) -> tuple[list, float, str | None, str | None]:
    """Retorna (items, subtotal, phone, customer_name) — phone pode vir do
    session_key se o customer não estiver cadastrado."""
    async with get_db_conn() as conn:
        await conn.execute(f"SET search_path = {schema}, public")
        # JOIN cobre os 2 formatos de session_key (só dígitos ou "x:phone:y").
        row = await conn.fetchrow(
            """
            SELECT c.items, c.subtotal,
                   cu.phone AS customer_phone, cu.name AS customer_name
              FROM cart c
              LEFT JOIN LATERAL (
                   SELECT name, phone FROM customers
                    WHERE phone = c.session_key
                       OR phone = NULLIF(SPLIT_PART(c.session_key, ':', 2), '')
                    LIMIT 1
              ) cu ON TRUE
             WHERE c.session_key = $1
            """,
            session_key,
        )
    if not row:
        return [], 0.0, None, None

    items_raw = row["items"]
    if isinstance(items_raw, str):
        try: items = json.loads(items_raw)
        except json.JSONDecodeError: items = []
    else:
        items = list(items_raw or [])

    # Telefone vem do customer cadastrado, ou do próprio session_key.
    phone = row["customer_phone"]
    if not phone:
        sk = (session_key or "")
        if sk.isdigit():
            phone = sk
        elif ":" in sk:
            phone = next((p for p in sk.split(":")[1:] if p.isdigit()), None)

    return items, float(row["subtotal"] or 0), phone, row["customer_name"]


async def _mark_cart_sent(schema: str, session_key: str) -> None:
    async with get_db_conn() as conn:
        await conn.execute(f"SET search_path = {schema}, public")
        await conn.execute(
            """
            UPDATE cart
               SET sent_recovery_at  = NOW(),
                   recovery_attempts = COALESCE(recovery_attempts, 0) + 1
             WHERE session_key = $1
            """,
            session_key,
        )


async def _process(batch_id: str) -> dict:
    # Celery task roda via asyncio.run() → loop NOVO a cada task. O pool
    # asyncpg vive vinculado ao loop em que foi criado, então precisamos
    # garantir que está pronto neste loop antes do primeiro fetch. Sem isso
    # a primeira query falha com "DB pool not initialized". init_pool é
    # idempotente para o loop corrente. Ver [[pgbouncer-setup]].
    await init_pool()

    batch = await _load_batch(batch_id)
    if not batch:
        log.warning("recovery_batch.not_found", batch_id=batch_id)
        return {"error": "not_found"}

    if batch["status"] not in ("queued", "running"):
        log.info("recovery_batch.skip_terminal_status",
                 batch_id=batch_id, status=batch["status"])
        return {"status": batch["status"]}

    await _mark_started(batch_id)

    tenant_id   = str(batch["tenant_id"])
    schema      = batch["schema_name"]
    raw_keys    = batch["session_keys"]
    if isinstance(raw_keys, str):
        try: session_keys = json.loads(raw_keys)
        except json.JSONDecodeError: session_keys = []
    else:
        session_keys = list(raw_keys or [])

    persona: dict = {}
    try:
        persona = await load_persona(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("recovery_batch.persona_failed",
                    batch_id=batch_id, tenant=tenant_id, exc=str(exc))

    cancelled = False
    for idx, session_key in enumerate(session_keys):
        # Re-check cancel periodicamente
        if idx and idx % _CANCEL_CHECK_EVERY == 0:
            if await _is_cancelled(batch_id):
                cancelled = True
                break

        items, subtotal, phone, name = await _load_cart_and_customer(schema, session_key)
        if not items or not phone:
            await _bump_skipped(batch_id)
            continue

        body = _build_message(persona, items, name)
        ok = await send_proactive_message(
            tenant_id, phone, body,
            kind="cart_recovery_manual",
            extra={"session_key": session_key,
                   "subtotal": subtotal,
                   "batch_id": batch_id,
                   "triggered_by": batch.get("actor_email")},
        )
        await _record_send(batch_id, session_key, ok)
        if ok:
            try:
                await _mark_cart_sent(schema, session_key)
            except Exception as exc:  # noqa: BLE001
                log.warning("recovery_batch.mark_cart_failed",
                            batch_id=batch_id, session=session_key, exc=str(exc))

        await asyncio.sleep(_SEND_INTERVAL_S)

    final_status = "cancelled" if cancelled else "completed"
    await _finish(batch_id, final_status)
    log.info("recovery_batch.done", batch_id=batch_id,
             status=final_status, tenant=tenant_id)
    return {"status": final_status, "batch_id": batch_id}


def process_recovery_batch_sync(batch_id: str) -> dict:
    """Entrypoint sync para Celery."""
    try:
        return asyncio.run(_process(batch_id))
    except Exception as exc:  # noqa: BLE001
        log.error("recovery_batch.crashed", batch_id=batch_id, exc=str(exc))
        # Tentativa best-effort de marcar como failed — precisa de loop novo
        # E init_pool nesse loop também, senão o UPDATE perde silenciosamente.
        async def _mark_failed():
            await init_pool()
            await _finish(batch_id, "failed", error=str(exc)[:1000])
        try:
            asyncio.run(_mark_failed())
        except Exception:
            pass
        return {"error": str(exc), "batch_id": batch_id}
