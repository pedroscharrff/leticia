"""
Customer CRM router — tenant portal.
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from db.postgres import get_db_conn, tenant_conn
from security import require_tenant_user, TenantUserContext
from services.audit import log_event

log = structlog.get_logger()
router = APIRouter(prefix="/portal/customers", tags=["portal-customers"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


class Address(BaseModel):
    cep: str | None = None
    street: str | None = None
    street_number: str | None = None
    complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None


class CustomerOut(BaseModel):
    id: str
    phone: str
    name: str | None
    email: str | None
    doc: str | None = None
    birth_date: str | None = None
    address: Address = Address()
    tags: list[str]
    notes: str | None = None
    last_contact_at: datetime | None
    total_orders: int
    total_spent: float
    auto_created: bool = False
    lgpd_consent_at: datetime | None
    created_at: datetime


class CustomerUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    doc: str | None = None
    birth_date: str | None = None
    tags: list[str] | None = None
    notes: str | None = None
    # Address
    cep: str | None = None
    street: str | None = None
    street_number: str | None = None
    complement: str | None = None
    neighborhood: str | None = None
    city: str | None = None
    state: str | None = None


class OrderItem(BaseModel):
    produto_id: str | None = None
    sku: str | None = None
    name: str | None = None
    qty: int = 0
    price: float = 0.0


class OrderOut(BaseModel):
    id: str
    session_key: str | None
    status: str
    items: list[OrderItem] = []
    subtotal: float
    discount: float
    total: float
    notes: str | None
    created_at: datetime


class ConversationLogOut(BaseModel):
    session_key: str
    role: str
    content: str
    skill_used: str | None
    created_at: datetime


async def _get_schema(tenant_id: str) -> str:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE", tenant_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")
    return row["schema_name"]


@router.get("", response_model=list[CustomerOut])
async def list_customers(
    user: TenantUser,
    q: str | None = Query(None, description="Busca por nome ou telefone"),
    tag: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> list[CustomerOut]:
    schema = await _get_schema(user.tenant_id)
    conditions: list[str] = []
    params: list = []
    i = 1

    if q:
        conditions.append(f"(name ILIKE ${i} OR phone ILIKE ${i})")
        params.append(f"%{q}%")
        i += 1
    if tag:
        conditions.append(f"${i} = ANY(tags)")
        params.append(tag)
        i += 1

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params += [limit, offset]

    try:
        async with tenant_conn(schema) as conn:
            rows = await conn.fetch(
                f"SELECT * FROM customers {where} ORDER BY last_contact_at DESC NULLS LAST LIMIT ${i} OFFSET ${i+1}",
                *params,
            )
    except Exception:
        return []  # table not yet created (migration pending)
    return [_row_to_out(r) for r in rows]


@router.get("/{customer_id}", response_model=CustomerOut)
async def get_customer(customer_id: str, user: TenantUser) -> CustomerOut:
    schema = await _get_schema(user.tenant_id)
    async with tenant_conn(schema) as conn:
        row = await conn.fetchrow("SELECT * FROM customers WHERE id = $1", customer_id)
    if not row:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return _row_to_out(row)


@router.patch("/{customer_id}", response_model=CustomerOut)
async def update_customer(customer_id: str, body: CustomerUpdate, user: TenantUser) -> CustomerOut:
    user.assert_role("operator")
    schema = await _get_schema(user.tenant_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    async with tenant_conn(schema) as conn:
        row = await conn.fetchrow(
            f"UPDATE customers SET {set_clauses}, updated_at = NOW() WHERE id = $1 RETURNING *",
            customer_id, *updates.values(),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return _row_to_out(row)


# ── LGPD: export and consent endpoints ───────────────────────────────────────

@router.get("/{customer_id}/export")
async def export_customer_data(customer_id: str, user: TenantUser) -> dict:
    """LGPD Art. 18 — export all data held about a customer."""
    user.assert_role("manager")
    schema = await _get_schema(user.tenant_id)

    async with tenant_conn(schema) as conn:
        customer = await conn.fetchrow("SELECT * FROM customers WHERE id = $1", customer_id)
        orders = await conn.fetch("SELECT * FROM orders WHERE customer_id = $1", customer_id)
        sessions = await conn.fetch(
            "SELECT session_key, phone, turn_count, created_at FROM sessions WHERE phone = $1",
            customer["phone"] if customer else "",
        )

    if not customer:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    await log_event("lgpd.export", user.email, tenant_id=user.tenant_id, target=customer_id)

    return {
        "customer": dict(customer),
        "orders": [dict(o) for o in orders],
        "sessions_count": len(sessions),
    }


@router.delete("/{customer_id}/data", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_customer_data(customer_id: str, user: TenantUser) -> None:
    """LGPD Art. 18 — erase PII. Keeps anonymized order records."""
    user.assert_role("owner")
    schema = await _get_schema(user.tenant_id)

    async with tenant_conn(schema) as conn:
        await conn.execute(
            """
            UPDATE customers SET
                name = 'REMOVIDO', email = NULL, doc = NULL,
                birth_date = NULL, notes = NULL, tags = '{}',
                lgpd_consent_at = NULL, updated_at = NOW()
            WHERE id = $1
            """,
            customer_id,
        )

    await log_event("lgpd.delete", user.email, tenant_id=user.tenant_id, target=customer_id)


def _row_to_out(r) -> CustomerOut:
    # Tolerate older rows that don't have v2 address columns yet.
    def _g(key, default=None):
        try:
            return r[key]
        except (KeyError, IndexError):
            return default
    return CustomerOut(
        id=str(r["id"]),
        phone=r["phone"],
        name=r["name"],
        email=r["email"],
        doc=_g("doc"),
        birth_date=str(_g("birth_date")) if _g("birth_date") else None,
        address=Address(
            cep=_g("cep"),
            street=_g("street"),
            street_number=_g("street_number"),
            complement=_g("complement"),
            neighborhood=_g("neighborhood"),
            city=_g("city"),
            state=_g("state"),
        ),
        tags=r["tags"] or [],
        notes=_g("notes"),
        last_contact_at=r["last_contact_at"],
        total_orders=r["total_orders"] or 0,
        total_spent=float(r["total_spent"]) if r["total_spent"] else 0.0,
        auto_created=bool(_g("auto_created", False)),
        lgpd_consent_at=r["lgpd_consent_at"],
        created_at=r["created_at"],
    )


# ── Orders history & conversation history ───────────────────────────────────

@router.get("/{customer_id}/orders", response_model=list[OrderOut])
async def list_customer_orders(
    customer_id: str,
    user: TenantUser,
    limit: int = Query(50, le=200),
    offset: int = 0,
    status_filter: str | None = Query(default=None, alias="status"),
) -> list[OrderOut]:
    """Histórico de compras de um cliente, do mais recente pro mais antigo."""
    schema = await _get_schema(user.tenant_id)

    conds = ["customer_id = $1"]
    params: list = [customer_id]
    if status_filter:
        conds.append(f"status = ${len(params)+1}")
        params.append(status_filter)
    where = " AND ".join(conds)
    params += [limit, offset]

    async with tenant_conn(schema) as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, session_key, items, subtotal, discount, total, status, notes, created_at
              FROM orders
             WHERE {where}
          ORDER BY created_at DESC
             LIMIT ${len(params)-1} OFFSET ${len(params)}
            """,
            *params,
        )

    import json as _json

    def _parse_items(raw):
        if raw is None:
            return []
        if isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
            except _json.JSONDecodeError:
                return []
            return parsed if isinstance(parsed, list) else []
        if isinstance(raw, list):
            return raw
        return []

    out: list[OrderOut] = []
    for r in rows:
        items = _parse_items(r["items"])
        out.append(OrderOut(
            id=str(r["id"]),
            session_key=r["session_key"],
            status=r["status"],
            items=[
                OrderItem(**{k: v for k, v in i.items() if k in OrderItem.model_fields})
                for i in items if isinstance(i, dict)
            ],
            subtotal=float(r["subtotal"] or 0),
            discount=float(r["discount"] or 0),
            total=float(r["total"] or 0),
            notes=r["notes"],
            created_at=r["created_at"],
        ))
    return out


@router.get("/{customer_id}/conversations", response_model=list[ConversationLogOut])
async def list_customer_conversations(
    customer_id: str,
    user: TenantUser,
    limit: int = Query(100, le=500),
) -> list[ConversationLogOut]:
    """
    Mensagens recentes trocadas com o cliente (todas as sessões),
    do mais recente pro mais antigo. Útil pro operador entender o
    contexto antes de assumir o atendimento.
    """
    schema = await _get_schema(user.tenant_id)
    async with tenant_conn(schema) as conn:
        cust = await conn.fetchrow("SELECT phone FROM customers WHERE id = $1", customer_id)
        if not cust:
            raise HTTPException(status_code=404, detail="Cliente não encontrado")
        rows = await conn.fetch(
            """
            SELECT session_key, role, content, skill_used, created_at
              FROM conversation_logs
             WHERE session_key LIKE '%' || $1
          ORDER BY created_at DESC
             LIMIT $2
            """,
            cust["phone"], limit,
        )
    return [
        ConversationLogOut(
            session_key=r["session_key"],
            role=r["role"],
            content=r["content"],
            skill_used=r["skill_used"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.get("/{customer_id}/summary")
async def customer_summary(customer_id: str, user: TenantUser) -> dict:
    """
    Painel rápido: cadastro + agregados + últimos N pedidos +
    contagem de conversas. Bom pra abrir a tela do cliente no portal.
    """
    schema = await _get_schema(user.tenant_id)
    async with tenant_conn(schema) as conn:
        cust = await conn.fetchrow("SELECT * FROM customers WHERE id = $1", customer_id)
        if not cust:
            raise HTTPException(status_code=404, detail="Cliente não encontrado")
        recent_orders = await conn.fetch(
            "SELECT id, status, total, created_at FROM orders "
            "WHERE customer_id = $1 ORDER BY created_at DESC LIMIT 5",
            customer_id,
        )
        conv_count = await conn.fetchval(
            "SELECT COUNT(*) FROM conversation_logs WHERE session_key LIKE '%' || $1",
            cust["phone"],
        )

    return {
        "customer": _row_to_out(cust).model_dump(),
        "recent_orders": [
            {"id": str(o["id"]), "status": o["status"],
             "total": float(o["total"] or 0),
             "created_at": o["created_at"].isoformat()}
            for o in recent_orders
        ],
        "conversation_messages_total": int(conv_count or 0),
    }
