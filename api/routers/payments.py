"""
Endpoints do portal para a capability `payments.pix_asaas`:

  GET    /portal/payments/status     — Asaas conectado? últimas cobranças?
  PUT    /portal/payments/asaas-key  — grava/atualiza o secret ASAAS_API_KEY
  DELETE /portal/payments/asaas-key  — remove

E para `sales.abandoned_cart` + `sales.continuous_refill_nudge`:
  GET    /portal/recovery/stats      — contadores das últimas execuções
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from db.postgres import get_db_conn
from security import require_tenant_user, TenantUserContext
from services import secrets as sec_svc
from services.audit import log_event

log = structlog.get_logger()

payments_router = APIRouter(prefix="/portal/payments", tags=["portal:payments"])
recovery_router = APIRouter(prefix="/portal/recovery", tags=["portal:recovery"])

TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


class AsaasKeyIn(BaseModel):
    api_key: str


class PaymentRow(BaseModel):
    id:          str
    order_id:    str | None
    phone:       str | None
    amount:      float
    status:      str
    created_at:  datetime
    paid_at:     datetime | None
    expires_at:  datetime | None


class PaymentsStatusOut(BaseModel):
    asaas_connected: bool
    pending_count:   int
    paid_last_30d:   int
    revenue_last_30d: float
    recent_charges:  list[PaymentRow]


@payments_router.get("/status", response_model=PaymentsStatusOut)
async def payments_status(user: TenantUser) -> PaymentsStatusOut:
    # Conexão: existe um secret ASAAS_API_KEY? Não decifra — apenas verifica.
    keys = await sec_svc.list_secret_keys(user.tenant_id)
    connected = "ASAAS_API_KEY" in keys

    since = datetime.now(timezone.utc) - timedelta(days=30)
    async with get_db_conn() as conn:
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM public.payments_log "
            "WHERE tenant_id = $1 AND status = 'pending'",
            user.tenant_id,
        )
        paid = await conn.fetchval(
            "SELECT COUNT(*) FROM public.payments_log "
            "WHERE tenant_id = $1 AND status = 'paid' AND paid_at >= $2",
            user.tenant_id, since,
        )
        revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM public.payments_log "
            "WHERE tenant_id = $1 AND status = 'paid' AND paid_at >= $2",
            user.tenant_id, since,
        )
        recent = await conn.fetch(
            "SELECT id, order_id, phone, amount, status, "
            "       created_at, paid_at, expires_at "
            "  FROM public.payments_log "
            " WHERE tenant_id = $1 "
            " ORDER BY created_at DESC LIMIT 20",
            user.tenant_id,
        )

    return PaymentsStatusOut(
        asaas_connected=connected,
        pending_count=int(pending or 0),
        paid_last_30d=int(paid or 0),
        revenue_last_30d=float(revenue or 0),
        recent_charges=[
            PaymentRow(
                id=str(r["id"]),
                order_id=str(r["order_id"]) if r["order_id"] else None,
                phone=r["phone"],
                amount=float(r["amount"] or 0),
                status=r["status"],
                created_at=r["created_at"],
                paid_at=r["paid_at"],
                expires_at=r["expires_at"],
            ) for r in recent
        ],
    )


@payments_router.put("/asaas-key")
async def set_asaas_key(payload: AsaasKeyIn, user: TenantUser) -> Response:
    user.assert_role("manager")
    key = (payload.api_key or "").strip()
    if not key or len(key) < 20:
        raise HTTPException(status_code=422,
                            detail="API key parece inválida.")
    await sec_svc.set_secret(user.tenant_id, "ASAAS_API_KEY", key)
    await log_event(
        action="payments.asaas_key_set", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="ASAAS_API_KEY", meta={},
    )
    return Response(status_code=204)


@payments_router.delete("/asaas-key")
async def delete_asaas_key(user: TenantUser) -> Response:
    user.assert_role("manager")
    await sec_svc.delete_secret(user.tenant_id, "ASAAS_API_KEY")
    await log_event(
        action="payments.asaas_key_removed", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="ASAAS_API_KEY", meta={},
    )
    return Response(status_code=204)


# ── Recovery (carrinho abandonado + recompra contínuo) ──────────────────────

class RecoveryStatsOut(BaseModel):
    carts_pending_recovery:   int     # carrinhos com itens > delay sem nudge
    carts_recovered_last_7d:  int     # carrinhos com sent_recovery_at recente
    refill_clients_total:     int     # clientes com continuous_meds não-vazio
    refills_nudged_last_30d:  int     # nudges enviados nos últimos 30 dias


@recovery_router.get("/stats", response_model=RecoveryStatsOut)
async def recovery_stats(user: TenantUser) -> RecoveryStatsOut:
    import asyncpg

    async def _safe_count(conn, sql: str, label: str) -> int:
        # Dois modos de falha:
        #   1) Schema drift (migrations 023/025 mudas): UndefinedColumn/Table.
        #   2) Dados sujos: ex. `cart.items` com valor escalar em vez de array
        #      dispara InvalidParameterValueError ("cannot get array length of
        #      a scalar"). O planner pode reordenar AND e avaliar
        #      jsonb_array_length antes do jsonb_typeof guard.
        # Em qualquer caso, melhor contar 0 e logar do que devolver 500.
        try:
            v = await conn.fetchval(sql)
            return int(v or 0)
        except asyncpg.PostgresError as e:
            log.warning("recovery.stats.query_failed",
                        tenant_id=str(user.tenant_id),
                        query=label,
                        error_type=type(e).__name__,
                        error=str(e))
            return 0

    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        schema = schema_row["schema_name"]

        await conn.execute(f"SET search_path = {schema}, public")

        # Self-heal: garante as colunas que as migrations 023/025 deveriam ter
        # adicionado. Idempotente; cobre tenants onde a migration foi
        # silenciosamente engolida pelo EXCEPTION WHEN OTHERS.
        try:
            await conn.execute(f"""
                ALTER TABLE {schema}.cart
                    ADD COLUMN IF NOT EXISTS sent_recovery_at  TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS recovery_attempts INTEGER NOT NULL DEFAULT 0
            """)
        except asyncpg.UndefinedTableError:
            log.warning("recovery.stats.no_cart_table", schema=schema)
        try:
            await conn.execute(f"""
                ALTER TABLE {schema}.customers
                    ADD COLUMN IF NOT EXISTS continuous_meds JSONB DEFAULT '[]'
            """)
        except asyncpg.UndefinedTableError:
            log.warning("recovery.stats.no_customers_table", schema=schema)

        # Carrinhos abandonados (itens > 0 + última atualização > 4h e sem nudge ainda)
        # CASE WHEN é o guard correto: jsonb_typeof num AND não impede o
        # planner de avaliar jsonb_array_length primeiro e estourar
        # InvalidParameterValueError quando `items` é um escalar/objeto.
        carts_pending = await _safe_count(conn, """
            SELECT COUNT(*) FROM cart
             WHERE (CASE WHEN jsonb_typeof(COALESCE(items, '[]'::jsonb)) = 'array'
                         THEN jsonb_array_length(COALESCE(items, '[]'::jsonb))
                         ELSE 0
                    END) > 0
               AND updated_at < NOW() - INTERVAL '4 hours'
               AND (sent_recovery_at IS NULL
                    OR sent_recovery_at < NOW() - INTERVAL '24 hours')
        """, "carts_pending")

        carts_recovered = await _safe_count(conn, """
            SELECT COUNT(*) FROM cart
             WHERE sent_recovery_at IS NOT NULL
               AND sent_recovery_at >= NOW() - INTERVAL '7 days'
        """, "carts_recovered")

        refill_clients = await _safe_count(conn, """
            SELECT COUNT(*) FROM customers
             WHERE (CASE WHEN jsonb_typeof(COALESCE(continuous_meds, '[]'::jsonb)) = 'array'
                         THEN jsonb_array_length(COALESCE(continuous_meds, '[]'::jsonb))
                         ELSE 0
                    END) > 0
        """, "refill_clients")

        # Nudges enviados nos últimos 30 dias: contagem de last_nudge_at >= 30d
        refills_nudged = await _safe_count(conn, """
            SELECT COUNT(*) FROM customers c,
                 LATERAL jsonb_array_elements(
                     CASE WHEN jsonb_typeof(COALESCE(c.continuous_meds, '[]'::jsonb)) = 'array'
                          THEN COALESCE(c.continuous_meds, '[]'::jsonb)
                          ELSE '[]'::jsonb
                     END
                 ) m
             WHERE (m->>'last_nudge_at') IS NOT NULL
               AND (m->>'last_nudge_at')::timestamptz >= NOW() - INTERVAL '30 days'
        """, "refills_nudged")

    return RecoveryStatsOut(
        carts_pending_recovery=carts_pending,
        carts_recovered_last_7d=carts_recovered,
        refill_clients_total=refill_clients,
        refills_nudged_last_30d=refills_nudged,
    )


# ── Listagem de carrinhos (em andamento + já notificados) ───────────────────

class CartRowOut(BaseModel):
    session_key:        str
    phone:              str | None
    customer_name:      str | None
    items_count:        int
    subtotal:           float
    updated_at:         datetime
    sent_recovery_at:   datetime | None
    recovery_attempts:  int
    # Heurística simples de "status" para o portal:
    #   'recovered'  → sent_recovery_at preenchido nos últimos 7d
    #   'pending'    → tem itens, sem nudge OU nudge antigo
    #   'in_progress'→ atualizado nas últimas 4h (cliente ainda ativo)
    status:             str


@recovery_router.get("/carts", response_model=list[CartRowOut])
async def list_carts(user: TenantUser) -> list[CartRowOut]:
    """Lista até 100 carrinhos com pelo menos 1 item, ordenado por atividade.

    Inclui carrinhos em andamento (cliente ativo nas últimas horas) e os já
    notificados. O frontend usa o campo `status` para diferenciar.
    """
    import asyncpg

    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        schema = schema_row["schema_name"]

        await conn.execute(f"SET search_path = {schema}, public")

        try:
            rows = await conn.fetch(
                """
                SELECT c.session_key,
                       SPLIT_PART(c.session_key, ':', 2) AS phone_guess,
                       cu.name AS customer_name,
                       cu.phone AS customer_phone,
                       (CASE WHEN jsonb_typeof(COALESCE(c.items, '[]'::jsonb)) = 'array'
                             THEN jsonb_array_length(COALESCE(c.items, '[]'::jsonb))
                             ELSE 0
                        END) AS items_count,
                       COALESCE(c.subtotal, 0) AS subtotal,
                       c.updated_at,
                       c.sent_recovery_at,
                       COALESCE(c.recovery_attempts, 0) AS recovery_attempts,
                       CASE
                         WHEN c.sent_recovery_at IS NOT NULL
                              AND c.sent_recovery_at >= NOW() - INTERVAL '7 days'
                           THEN 'recovered'
                         WHEN c.updated_at >= NOW() - INTERVAL '4 hours'
                           THEN 'in_progress'
                         ELSE 'pending'
                       END AS status
                  FROM cart c
                  LEFT JOIN customers cu
                    ON cu.phone = SPLIT_PART(c.session_key, ':', 2)
                 WHERE (CASE WHEN jsonb_typeof(COALESCE(c.items, '[]'::jsonb)) = 'array'
                             THEN jsonb_array_length(COALESCE(c.items, '[]'::jsonb))
                             ELSE 0
                        END) > 0
                 ORDER BY c.updated_at DESC
                 LIMIT 100
                """
            )
        except asyncpg.PostgresError as e:
            log.warning("recovery.list.query_failed",
                        tenant_id=str(user.tenant_id), error=str(e))
            return []

    return [
        CartRowOut(
            session_key=r["session_key"],
            phone=r["customer_phone"] or r["phone_guess"] or None,
            customer_name=r["customer_name"],
            items_count=int(r["items_count"] or 0),
            subtotal=float(r["subtotal"] or 0),
            updated_at=r["updated_at"],
            sent_recovery_at=r["sent_recovery_at"],
            recovery_attempts=int(r["recovery_attempts"] or 0),
            status=r["status"],
        )
        for r in rows
    ]


# ── Disparo manual em lote ──────────────────────────────────────────────────

class TriggerAllOut(BaseModel):
    checked:        int   # carrinhos elegíveis encontrados
    sent:           int   # disparos OK
    skipped_no_phone: int # session_key sem telefone parseável
    errors:         int   # falhas de envio


# Disparo manual é assíncrono: o endpoint enfileira um Celery task e
# devolve 202 com o batch_id. O frontend polla /batches/{id} pra mostrar
# progresso. Permite cancelar e desfazer (reverter marcador de envio).

class TriggerIn(BaseModel):
    # Lista opcional de session_keys. Vazio/None → todos os carrinhos com itens.
    session_keys: list[str] | None = None


class TriggerOut(BaseModel):
    batch_id: str
    total:    int


class BatchOut(BaseModel):
    id:              str
    status:          str
    total:           int
    sent:            int
    failed:          int
    skipped:         int
    actor_email:     str | None
    created_at:      datetime
    started_at:      datetime | None
    finished_at:     datetime | None
    cancel_requested: bool
    error:           str | None


def _row_to_batch(r) -> BatchOut:
    return BatchOut(
        id=str(r["id"]), status=r["status"],
        total=int(r["total"]), sent=int(r["sent"]),
        failed=int(r["failed"]), skipped=int(r["skipped"]),
        actor_email=r["actor_email"],
        created_at=r["created_at"], started_at=r["started_at"],
        finished_at=r["finished_at"],
        cancel_requested=bool(r["cancel_requested"]),
        error=r["error"],
    )


@recovery_router.post("/trigger", response_model=TriggerOut, status_code=202)
async def trigger_recovery(payload: TriggerIn, user: TenantUser) -> TriggerOut:
    """Enfileira um batch de envio. Não bloqueia: o Celery worker processa
    em background com rate-limit, e o frontend mostra progresso.

    Se `session_keys` vier vazio, seleciona TODOS os carrinhos do tenant que
    têm pelo menos 1 item (independente de status).
    """
    user.assert_role("manager")

    import asyncpg
    import json as _json

    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        schema = schema_row["schema_name"]

        # Recusa se já houver batch em andamento — evita duplicar envio.
        active = await conn.fetchrow(
            """
            SELECT id FROM public.recovery_batches
             WHERE tenant_id = $1 AND status IN ('queued','running')
             LIMIT 1
            """,
            user.tenant_id,
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="Já existe um disparo em andamento. Aguarde ou cancele antes de iniciar outro.",
            )

        # Resolve session_keys: filtragem feita no DB pra garantir que só
        # carrinhos com itens entrem (defesa contra payload manual sujo).
        await conn.execute(f"SET search_path = {schema}, public")
        try:
            if payload.session_keys:
                rows = await conn.fetch(
                    """
                    SELECT session_key FROM cart
                     WHERE session_key = ANY($1::text[])
                       AND (CASE WHEN jsonb_typeof(COALESCE(items, '[]'::jsonb)) = 'array'
                                 THEN jsonb_array_length(COALESCE(items, '[]'::jsonb))
                                 ELSE 0
                            END) > 0
                    """,
                    payload.session_keys,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT session_key FROM cart
                     WHERE (CASE WHEN jsonb_typeof(COALESCE(items, '[]'::jsonb)) = 'array'
                                 THEN jsonb_array_length(COALESCE(items, '[]'::jsonb))
                                 ELSE 0
                            END) > 0
                     ORDER BY updated_at DESC
                    """
                )
        except asyncpg.PostgresError as e:
            log.warning("recovery.trigger.query_failed",
                        tenant_id=str(user.tenant_id), error=str(e))
            raise HTTPException(
                status_code=500,
                detail="Não foi possível buscar carrinhos.",
            )

        keys = [r["session_key"] for r in rows]
        if not keys:
            raise HTTPException(
                status_code=400,
                detail="Nenhum carrinho elegível para disparo.",
            )

        # Cria o batch (volta pro search_path padrão pra escrever em public).
        await conn.execute("SET search_path = public")
        batch_row = await conn.fetchrow(
            """
            INSERT INTO public.recovery_batches
                   (tenant_id, schema_name, actor_email, total, session_keys)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id
            """,
            user.tenant_id, schema, user.email, len(keys),
            _json.dumps(keys),
        )
        batch_id = str(batch_row["id"])

    # Enfileira o task (lazy import pra não criar ciclo no startup do API).
    from workers.celery_app import process_recovery_batch_task
    process_recovery_batch_task.delay(batch_id)

    await log_event(
        action="recovery.trigger_enqueued", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="cart_recovery",
        meta={"batch_id": batch_id, "total": len(keys),
              "scope": "selected" if payload.session_keys else "all"},
    )
    return TriggerOut(batch_id=batch_id, total=len(keys))


@recovery_router.get("/batches", response_model=list[BatchOut])
async def list_batches(user: TenantUser) -> list[BatchOut]:
    """Últimos 20 batches do tenant — para mostrar histórico recente no portal."""
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status, total, sent, failed, skipped, actor_email,
                   created_at, started_at, finished_at, cancel_requested, error
              FROM public.recovery_batches
             WHERE tenant_id = $1
             ORDER BY created_at DESC
             LIMIT 20
            """,
            user.tenant_id,
        )
    return [_row_to_batch(r) for r in rows]


@recovery_router.get("/batches/{batch_id}", response_model=BatchOut)
async def get_batch(batch_id: str, user: TenantUser) -> BatchOut:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, status, total, sent, failed, skipped, actor_email,
                   created_at, started_at, finished_at, cancel_requested, error
              FROM public.recovery_batches
             WHERE id = $1 AND tenant_id = $2
            """,
            batch_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")
    return _row_to_batch(row)


@recovery_router.post("/batches/{batch_id}/cancel", response_model=BatchOut)
async def cancel_batch(batch_id: str, user: TenantUser) -> BatchOut:
    """Marca `cancel_requested = TRUE`. O worker checa antes do próximo envio
    e encerra o batch. Mensagens já entregues NÃO são desfeitas — o undo é
    quem reverte o marcador no carrinho.
    """
    user.assert_role("manager")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.recovery_batches
               SET cancel_requested = TRUE
             WHERE id = $1 AND tenant_id = $2 AND status IN ('queued','running')
            RETURNING id, status, total, sent, failed, skipped, actor_email,
                      created_at, started_at, finished_at, cancel_requested, error
            """,
            batch_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Batch não está em execução (já terminou ou não existe).",
        )
    await log_event(
        action="recovery.batch_cancel_requested", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="cart_recovery", meta={"batch_id": batch_id},
    )
    return _row_to_batch(row)


@recovery_router.post("/batches/{batch_id}/undo", response_model=BatchOut)
async def undo_batch(batch_id: str, user: TenantUser) -> BatchOut:
    """Reverte o marcador `sent_recovery_at`/`recovery_attempts` nos
    carrinhos que receberam mensagem neste batch — pra que voltem a ser
    elegíveis pelo job automático.

    NÃO desentrega mensagens já enviadas (impossível). Só limpa o estado
    interno. Usar quando o operador disparou em lote por engano ou quer
    permitir nova tentativa controlada.
    """
    user.assert_role("manager")
    import json as _json
    import asyncpg

    async with get_db_conn() as conn:
        batch = await conn.fetchrow(
            """
            SELECT id, schema_name, status, sent, sent_session_keys
              FROM public.recovery_batches
             WHERE id = $1 AND tenant_id = $2
            """,
            batch_id, user.tenant_id,
        )
        if not batch:
            raise HTTPException(status_code=404, detail="Batch não encontrado.")
        if batch["status"] not in ("completed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail="Só é possível desfazer um disparo que já terminou.",
            )

        raw = batch["sent_session_keys"]
        if isinstance(raw, str):
            try: sent_keys = _json.loads(raw)
            except _json.JSONDecodeError: sent_keys = []
        else:
            sent_keys = list(raw or [])

        if sent_keys:
            schema = batch["schema_name"]
            await conn.execute(f"SET search_path = {schema}, public")
            try:
                await conn.execute(
                    """
                    UPDATE cart
                       SET sent_recovery_at  = NULL,
                           recovery_attempts = GREATEST(COALESCE(recovery_attempts, 0) - 1, 0)
                     WHERE session_key = ANY($1::text[])
                    """,
                    sent_keys,
                )
            except asyncpg.PostgresError as e:
                log.warning("recovery.undo.update_failed",
                            tenant_id=str(user.tenant_id),
                            batch_id=batch_id, error=str(e))
                raise HTTPException(
                    status_code=500,
                    detail="Falha ao reverter marcador nos carrinhos.",
                )

        await conn.execute("SET search_path = public")
        row = await conn.fetchrow(
            """
            UPDATE public.recovery_batches
               SET status = 'undone', finished_at = COALESCE(finished_at, NOW())
             WHERE id = $1
            RETURNING id, status, total, sent, failed, skipped, actor_email,
                      created_at, started_at, finished_at, cancel_requested, error
            """,
            batch_id,
        )

    await log_event(
        action="recovery.batch_undone", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="cart_recovery",
        meta={"batch_id": batch_id, "reverted_carts": len(sent_keys)},
    )
    return _row_to_batch(row)
