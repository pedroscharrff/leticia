"""
Orders router — tenant portal.

Endpoints (all under /portal/orders, require tenant user):
  GET    /portal/orders/metrics        — aggregate counters + revenue
  GET    /portal/orders                — list with filters (status, q, date)
  GET    /portal/orders/{id}           — full detail incl. customer
  PATCH  /portal/orders/{id}           — update status / notes (operator+)
"""
from __future__ import annotations

import json
from datetime import datetime, date
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from db.postgres import get_db_conn, tenant_conn
from security import require_tenant_user, TenantUserContext
from services.audit import log_event

log = structlog.get_logger()
router = APIRouter(prefix="/portal/orders", tags=["portal-orders"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


VALID_STATUSES = ["pending", "confirmed", "processing", "shipped", "delivered", "cancelled"]
OPEN_STATUSES = ["pending", "confirmed", "processing", "shipped"]
CLOSED_STATUSES = ["delivered", "cancelled"]


# ── Schemas ──────────────────────────────────────────────────────────────────

class OrderItem(BaseModel):
    produto_id: str | None = None
    sku: str | None = None
    name: str | None = None
    qty: int = 0
    price: float = 0.0
    prescription_required: bool = False


class OrderCustomer(BaseModel):
    id: str | None = None
    name: str | None = None
    phone: str | None = None
    email: str | None = None
    doc: str | None = None
    address: dict | None = None


class OrderListItem(BaseModel):
    id: str
    status: str
    items_count: int
    total: float
    customer_name: str | None
    customer_phone: str | None
    created_at: datetime


class OrderDetail(BaseModel):
    id: str
    status: str
    session_key: str | None
    items: list[OrderItem]
    subtotal: float
    discount: float
    total: float
    notes: str | None
    requires_prescription: bool
    customer: OrderCustomer
    created_at: datetime
    updated_at: datetime


class OrderMetrics(BaseModel):
    by_status: dict[str, int]
    open_count: int
    closed_count: int
    total_orders: int
    revenue_today: float
    revenue_week: float
    revenue_month: float
    avg_ticket_month: float
    top_products_month: list[dict]


class OrderUpdate(BaseModel):
    status: str | None = Field(default=None)
    notes: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _schema_for(user: TenantUserContext) -> str:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE",
            user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")
    return row["schema_name"]


def _parse_items(raw) -> list[dict]:
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            return json.loads(raw) or []
        except Exception:
            return []
    return list(raw)


# ── Metrics ──────────────────────────────────────────────────────────────────

@router.get("/metrics", response_model=OrderMetrics)
async def order_metrics(user: TenantUser) -> OrderMetrics:
    schema = await _schema_for(user)
    try:
        async with tenant_conn(schema) as conn:
            by_status_rows = await conn.fetch(
                "SELECT status, COUNT(*) AS n FROM orders GROUP BY status"
            )
            rev_rows = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(total) FILTER (WHERE created_at::date = CURRENT_DATE), 0)                       AS rev_today,
                    COALESCE(SUM(total) FILTER (WHERE created_at >= NOW() - INTERVAL '7 days'), 0)               AS rev_week,
                    COALESCE(SUM(total) FILTER (WHERE date_trunc('month', created_at) = date_trunc('month', NOW())), 0) AS rev_month,
                    COALESCE(AVG(total) FILTER (WHERE date_trunc('month', created_at) = date_trunc('month', NOW())
                                                AND status <> 'cancelled'), 0)                                  AS avg_month
                FROM orders
                """
            )
            # Top products in current month — flatten items JSONB
            top_rows = await conn.fetch(
                """
                SELECT
                    item->>'name'  AS name,
                    item->>'sku'   AS sku,
                    SUM((item->>'qty')::int)                                AS qty,
                    SUM((item->>'qty')::int * (item->>'price')::numeric)    AS revenue
                FROM orders, jsonb_array_elements(items) AS item
                WHERE date_trunc('month', created_at) = date_trunc('month', NOW())
                  AND status <> 'cancelled'
                  AND item ? 'name'
                GROUP BY item->>'name', item->>'sku'
                ORDER BY qty DESC
                LIMIT 5
                """
            )
    except Exception as e:
        log.warning("orders.metrics_failed", error=str(e))
        return OrderMetrics(
            by_status={s: 0 for s in VALID_STATUSES},
            open_count=0, closed_count=0, total_orders=0,
            revenue_today=0, revenue_week=0, revenue_month=0,
            avg_ticket_month=0, top_products_month=[],
        )

    by_status = {s: 0 for s in VALID_STATUSES}
    for r in by_status_rows:
        if r["status"] in by_status:
            by_status[r["status"]] = int(r["n"])

    return OrderMetrics(
        by_status=by_status,
        open_count=sum(by_status[s] for s in OPEN_STATUSES),
        closed_count=sum(by_status[s] for s in CLOSED_STATUSES),
        total_orders=sum(by_status.values()),
        revenue_today=float(rev_rows["rev_today"] or 0),
        revenue_week=float(rev_rows["rev_week"] or 0),
        revenue_month=float(rev_rows["rev_month"] or 0),
        avg_ticket_month=float(rev_rows["avg_month"] or 0),
        top_products_month=[
            {
                "name": r["name"], "sku": r["sku"],
                "qty": int(r["qty"] or 0),
                "revenue": float(r["revenue"] or 0),
            } for r in top_rows
        ],
    )


# ── List ─────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[OrderListItem])
async def list_orders(
    user: TenantUser,
    status: str | None = Query(None, description="Filtra por status (ou 'open'/'closed')"),
    q: str | None = Query(None, description="Busca por nome ou telefone do cliente, ou ID parcial"),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> list[OrderListItem]:
    schema = await _schema_for(user)

    conditions: list[str] = []
    params: list = []
    i = 1

    if status == "open":
        conditions.append(f"o.status = ANY(${i}::text[])")
        params.append(OPEN_STATUSES); i += 1
    elif status == "closed":
        conditions.append(f"o.status = ANY(${i}::text[])")
        params.append(CLOSED_STATUSES); i += 1
    elif status:
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=422, detail=f"Status inválido: {status}")
        conditions.append(f"o.status = ${i}")
        params.append(status); i += 1

    if q:
        conditions.append(f"(c.name ILIKE ${i} OR c.phone ILIKE ${i} OR o.id::text ILIKE ${i})")
        params.append(f"%{q}%"); i += 1
    if date_from:
        conditions.append(f"o.created_at >= ${i}")
        params.append(date_from); i += 1
    if date_to:
        conditions.append(f"o.created_at < (${i}::date + INTERVAL '1 day')")
        params.append(date_to); i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    try:
        async with tenant_conn(schema) as conn:
            rows = await conn.fetch(
                f"""
                SELECT o.id, o.status, o.items, o.total, o.created_at,
                       c.name AS customer_name, c.phone AS customer_phone
                  FROM orders o
                  LEFT JOIN customers c ON c.id = o.customer_id
                  {where}
                 ORDER BY o.created_at DESC
                 LIMIT ${i} OFFSET ${i+1}
                """,
                *params,
            )
    except Exception as e:
        log.warning("orders.list_failed", error=str(e))
        return []

    return [
        OrderListItem(
            id=str(r["id"]),
            status=r["status"],
            items_count=sum(int(it.get("qty", 0)) for it in _parse_items(r["items"])),
            total=float(r["total"] or 0),
            customer_name=r["customer_name"],
            customer_phone=r["customer_phone"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


# ── Detail ───────────────────────────────────────────────────────────────────

@router.get("/{order_id}", response_model=OrderDetail)
async def get_order(order_id: str, user: TenantUser) -> OrderDetail:
    schema = await _schema_for(user)
    async with tenant_conn(schema) as conn:
        row = await conn.fetchrow(
            """
            SELECT o.*,
                   c.id    AS c_id,    c.name  AS c_name,  c.phone AS c_phone,
                   c.email AS c_email, c.doc   AS c_doc,
                   c.cep, c.street, c.street_number, c.complement,
                   c.neighborhood, c.city, c.state
              FROM orders o
              LEFT JOIN customers c ON c.id = o.customer_id
             WHERE o.id::text = $1
            """,
            order_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    items = _parse_items(row["items"])
    return OrderDetail(
        id=str(row["id"]),
        status=row["status"],
        session_key=row["session_key"],
        items=[OrderItem(**{k: v for k, v in it.items() if k in OrderItem.model_fields}) for it in items],
        subtotal=float(row["subtotal"] or 0),
        discount=float(row["discount"] or 0),
        total=float(row["total"] or 0),
        notes=row["notes"],
        requires_prescription=any(it.get("prescription_required") for it in items),
        customer=OrderCustomer(
            id=str(row["c_id"]) if row["c_id"] else None,
            name=row["c_name"],
            phone=row["c_phone"],
            email=row["c_email"],
            doc=row["c_doc"],
            address={
                "cep": row["cep"], "street": row["street"],
                "street_number": row["street_number"], "complement": row["complement"],
                "neighborhood": row["neighborhood"], "city": row["city"],
                "state": row["state"],
            },
        ),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ── Update status / notes ────────────────────────────────────────────────────

@router.patch("/{order_id}", response_model=OrderDetail)
async def update_order(order_id: str, body: OrderUpdate, user: TenantUser) -> OrderDetail:
    user.assert_role("operator")
    if body.status and body.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Status inválido. Permitidos: {VALID_STATUSES}",
        )

    schema = await _schema_for(user)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return await get_order(order_id, user)

    # Read previous status to detect a transition (avoid notifying on no-op).
    async with tenant_conn(schema) as conn:
        prev = await conn.fetchrow(
            "SELECT status FROM orders WHERE id::text = $1", order_id,
        )
    if not prev:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    async with tenant_conn(schema) as conn:
        row = await conn.fetchrow(
            f"UPDATE orders SET {set_clauses}, updated_at = NOW() "
            f"WHERE id::text = $1 RETURNING id",
            order_id, *updates.values(),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Pedido não encontrado")

    await log_event(
        action="order.updated",
        actor_id=user.email,
        tenant_id=user.tenant_id,
        target=order_id,
        meta={"changed": list(updates.keys()), "new_status": updates.get("status")},
    )

    # Notify customer when status actually changes
    new_status = updates.get("status")
    notification_sent = False
    if new_status and new_status != prev["status"]:
        notification_sent = await _notify_status_change(user.tenant_id, schema, order_id, new_status)

    detail = await get_order(order_id, user)
    if notification_sent:
        log.info("order.status_notification_sent", order=order_id, status=new_status)
    return detail


async def _notify_status_change(
    tenant_id: str, schema: str, order_id: str, new_status: str,
) -> bool:
    """Pulls the order + customer + tenant callback, then renders + sends."""
    from services.order_status import send_status_notification

    try:
        async with get_db_conn() as conn:
            tenant_row = await conn.fetchrow(
                "SELECT callback_url FROM public.tenants WHERE id = $1", tenant_id,
            )
            persona_row = await conn.fetchrow(
                "SELECT pharmacy_name FROM public.tenant_persona WHERE tenant_id = $1",
                tenant_id,
            )
        callback_url = tenant_row["callback_url"] if tenant_row else None
        pharmacy_name = persona_row["pharmacy_name"] if persona_row else None
    except Exception as exc:
        log.warning("order_status.tenant_lookup_failed", tenant=tenant_id, error=str(exc))
        return False

    async with tenant_conn(schema) as conn:
        order = await conn.fetchrow(
            """
            SELECT o.id, o.session_key, o.items, o.total,
                   c.name AS customer_name, c.phone AS customer_phone
              FROM orders o
              LEFT JOIN customers c ON c.id = o.customer_id
             WHERE o.id::text = $1
            """,
            order_id,
        )
    if not order or not order["customer_phone"]:
        return False

    return await send_status_notification(
        tenant_id=tenant_id,
        callback_url=callback_url or "",
        phone=order["customer_phone"],
        new_status=new_status,
        order_ctx={
            "order_id": str(order["id"]),
            "session_key": order["session_key"],
            "customer_name": order["customer_name"],
            "total": float(order["total"] or 0),
            "items": _parse_items(order["items"]),
            "pharmacy_name": pharmacy_name,
        },
    )
