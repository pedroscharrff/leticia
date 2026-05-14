"""
Inventory management — products, inventory connectors, sync.
"""
from __future__ import annotations

import json
from typing import Annotated
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, status
from pydantic import BaseModel

from db.postgres import get_db_conn, tenant_conn
from security import require_tenant_user, TenantUserContext
from services.audit import log_event
from services.inventory import RestApiConnector, SqlConnector, CsvConnector, CONNECTOR_REGISTRY
from services import secrets as sec_svc

log = structlog.get_logger()
router = APIRouter(prefix="/portal/inventory", tags=["portal-inventory"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


# ── Models ────────────────────────────────────────────────────────────────────

class ProductOut(BaseModel):
    id: str
    sku: str | None
    name: str
    brand: str | None
    category: str | None
    description: str | None
    price: float | None
    stock_qty: int
    unit: str
    barcode: str | None
    source: str
    active: bool
    tags: list[str]
    updated_at: datetime


class ProductCreate(BaseModel):
    sku: str | None = None
    name: str
    brand: str | None = None
    category: str | None = None
    description: str | None = None
    price: float | None = None
    stock_qty: int = 0
    unit: str = "un"
    barcode: str | None = None
    tags: list[str] = []


class ProductUpdate(BaseModel):
    name: str | None = None
    brand: str | None = None
    category: str | None = None
    description: str | None = None
    price: float | None = None
    stock_qty: int | None = None
    unit: str | None = None
    barcode: str | None = None
    active: bool | None = None
    tags: list[str] | None = None


class ConnectorConfig(BaseModel):
    connector_type: str  # rest_api | sql | csv | webhook
    display_name: str | None = None
    config: dict = {}
    credentials: dict = {}  # stored encrypted
    schedule_cron: str | None = None  # e.g. "0 */6 * * *"


class SyncResult(BaseModel):
    status: str
    records_in: int
    records_upd: int
    errors: list[str]


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_schema(tenant_id: str) -> str:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE", tenant_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Farmácia não encontrada")
    return row["schema_name"]


# ── Products ──────────────────────────────────────────────────────────────────

@router.get("/products", response_model=list[ProductOut])
async def list_products(
    user: TenantUser,
    q: str | None = Query(None),
    category: str | None = Query(None),
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> list[ProductOut]:
    schema = await _get_schema(user.tenant_id)
    conditions = ["active = TRUE"]
    params: list = []
    i = 1
    if q:
        conditions.append(f"name ILIKE ${i}")
        params.append(f"%{q}%")
        i += 1
    if category:
        conditions.append(f"category = ${i}")
        params.append(category)
        i += 1

    where = " AND ".join(conditions)
    params += [limit, offset]

    try:
        async with tenant_conn(schema) as conn:
            rows = await conn.fetch(
                f"SELECT * FROM products WHERE {where} ORDER BY name LIMIT ${i} OFFSET ${i+1}",
                *params,
            )
    except Exception:
        return []  # table not yet created (migration pending)
    return [
        ProductOut(
            id=str(r["id"]), sku=r["sku"], name=r["name"], brand=r["brand"],
            category=r["category"], description=r["description"], price=float(r["price"]) if r["price"] else None,
            stock_qty=r["stock_qty"] or 0, unit=r["unit"], barcode=r["barcode"],
            source=r["source"], active=r["active"], tags=r["tags"] or [],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


@router.post("/products", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
async def create_product(body: ProductCreate, user: TenantUser) -> ProductOut:
    user.assert_role("operator")
    schema = await _get_schema(user.tenant_id)
    async with tenant_conn(schema) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO products (sku, name, brand, category, description, price, stock_qty, unit, barcode, tags, source)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'manual')
            RETURNING *
            """,
            body.sku, body.name, body.brand, body.category, body.description,
            body.price, body.stock_qty, body.unit, body.barcode, body.tags,
        )
    return ProductOut(
        id=str(row["id"]), sku=row["sku"], name=row["name"], brand=row["brand"],
        category=row["category"], description=row["description"],
        price=float(row["price"]) if row["price"] else None,
        stock_qty=row["stock_qty"] or 0, unit=row["unit"], barcode=row["barcode"],
        source=row["source"], active=row["active"], tags=row["tags"] or [],
        updated_at=row["updated_at"],
    )


@router.patch("/products/{product_id}", response_model=ProductOut)
async def update_product(product_id: str, body: ProductUpdate, user: TenantUser) -> ProductOut:
    user.assert_role("operator")
    schema = await _get_schema(user.tenant_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nenhum campo para atualizar")

    set_clauses = ", ".join(f"{k} = ${i+2}" for i, k in enumerate(updates))
    async with tenant_conn(schema) as conn:
        row = await conn.fetchrow(
            f"UPDATE products SET {set_clauses}, updated_at = NOW() WHERE id = $1 RETURNING *",
            product_id, *updates.values(),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Produto não encontrado")
    return ProductOut(
        id=str(row["id"]), sku=row["sku"], name=row["name"], brand=row["brand"],
        category=row["category"], description=row["description"],
        price=float(row["price"]) if row["price"] else None,
        stock_qty=row["stock_qty"] or 0, unit=row["unit"], barcode=row["barcode"],
        source=row["source"], active=row["active"], tags=row["tags"] or [],
        updated_at=row["updated_at"],
    )


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_product(product_id: str, user: TenantUser) -> None:
    user.assert_role("manager")
    schema = await _get_schema(user.tenant_id)
    async with tenant_conn(schema) as conn:
        await conn.execute("UPDATE products SET active = FALSE WHERE id = $1", product_id)


# ── CSV import ────────────────────────────────────────────────────────────────

@router.post("/products/import-csv", response_model=SyncResult)
async def import_csv(
    user: TenantUser,
    file: UploadFile = File(...),
    mapping: str = Query("{}", description="JSON field mapping"),
) -> SyncResult:
    user.assert_role("operator")
    schema = await _get_schema(user.tenant_id)
    content = await file.read()
    mapping_dict = json.loads(mapping)
    result = await CsvConnector().import_csv(user.tenant_id, schema, content, mapping_dict)
    await log_event("inventory.csv_import", user.email, tenant_id=user.tenant_id,
                    meta={"records_in": result["records_in"], "records_upd": result["records_upd"]})
    return SyncResult(**result)


# ── Connectors ────────────────────────────────────────────────────────────────

@router.post("/connectors", status_code=status.HTTP_201_CREATED)
async def create_connector(body: ConnectorConfig, user: TenantUser) -> dict:
    user.assert_role("manager")
    if body.connector_type not in CONNECTOR_REGISTRY:
        raise HTTPException(status_code=400, detail=f"Tipo '{body.connector_type}' não suportado")

    creds_key = f"connector_creds_{body.connector_type}"
    if body.credentials:
        await sec_svc.set_secret(user.tenant_id, creds_key, json.dumps(body.credentials))

    config = {**body.config, "credentials_key": creds_key}
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_secrets (tenant_id, key, value_enc)
            VALUES ($1, $2, $3)
            ON CONFLICT (tenant_id, key) DO UPDATE SET value_enc = EXCLUDED.value_enc, updated_at = NOW()
            RETURNING id
            """,
            user.tenant_id,
            f"connector_config_{body.connector_type}",
            __import__("services.secrets", fromlist=["encrypt"]).encrypt(json.dumps(config)),
        )

    await log_event("connector.created", user.email, tenant_id=user.tenant_id, target=body.connector_type)
    return {"connector_type": body.connector_type, "status": "created"}


@router.post("/connectors/{connector_type}/sync", response_model=SyncResult)
async def trigger_sync(connector_type: str, user: TenantUser) -> SyncResult:
    user.assert_role("operator")
    if connector_type not in CONNECTOR_REGISTRY:
        raise HTTPException(status_code=400, detail="Conector não encontrado")

    schema = await _get_schema(user.tenant_id)

    config_raw = await sec_svc.get_secret(user.tenant_id, f"connector_config_{connector_type}")
    if not config_raw:
        raise HTTPException(status_code=404, detail="Conector não configurado")
    config = json.loads(config_raw)

    connector = CONNECTOR_REGISTRY[connector_type]()
    result = await connector.sync(user.tenant_id, schema, config)
    return SyncResult(**result)


@router.get("/connectors/{connector_type}/logs")
async def connector_logs(connector_type: str, user: TenantUser, limit: int = 20) -> list[dict]:
    schema = await _get_schema(user.tenant_id)
    async with tenant_conn(schema) as conn:
        rows = await conn.fetch(
            "SELECT * FROM inventory_sync_log WHERE connector = $1 ORDER BY created_at DESC LIMIT $2",
            connector_type, limit,
        )
    return [dict(r) for r in rows]
