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
