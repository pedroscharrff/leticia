"""
Inventory connector service.
Supports: manual (CRUD), rest_api, sql, webhook, csv (import).
Each connector syncs products into the tenant's products table.
"""
from __future__ import annotations

import asyncio
import csv
import io
import json
import time
from typing import Any

import httpx
import structlog

from db.postgres import get_db_conn, tenant_conn
from services import secrets as sec_svc

log = structlog.get_logger()


# ── Connector base ────────────────────────────────────────────────────────────

class InventoryConnector:
    source: str = ""

    async def fetch_products(self, config: dict, credentials: dict) -> list[dict]:
        raise NotImplementedError

    async def sync(self, tenant_id: str, schema: str, config: dict) -> dict:
        """Run a full sync and log results."""
        start = time.monotonic()
        errors: list[str] = []
        records_in = records_upd = 0

        try:
            credentials = await _load_credentials(tenant_id, config.get("credentials_key", ""))
            products = await self.fetch_products(config, credentials)
            records_in = len(products)

            for product in products:
                try:
                    async with tenant_conn(schema) as conn:
                        await _upsert_product(conn, product, self.source)
                        records_upd += 1
                except Exception as exc:
                    errors.append(str(exc))
        except Exception as exc:
            errors.append(f"fetch_error: {exc}")

        duration = int((time.monotonic() - start) * 1000)
        status = "ok" if not errors else ("partial" if records_upd > 0 else "error")

        async with tenant_conn(schema) as conn:
            await conn.execute(
                """
                INSERT INTO inventory_sync_log (connector, status, records_in, records_upd, errors, duration_ms)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                self.source, status, records_in, records_upd, json.dumps(errors), duration,
            )

        log.info("inventory.sync", tenant=tenant_id, source=self.source,
                 records_in=records_in, records_upd=records_upd, errors=len(errors))
        return {"status": status, "records_in": records_in, "records_upd": records_upd, "errors": errors}


async def _load_credentials(tenant_id: str, key: str) -> dict:
    if not key:
        return {}
    raw = await sec_svc.get_secret(tenant_id, key)
    return json.loads(raw) if raw else {}


async def _upsert_product(conn, product: dict, source: str) -> None:
    await conn.execute(
        """
        INSERT INTO products (sku, name, brand, category, description, price, stock_qty, unit, barcode, source, tags, meta)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
        ON CONFLICT (sku) DO UPDATE SET
            name=EXCLUDED.name, brand=EXCLUDED.brand, category=EXCLUDED.category,
            description=EXCLUDED.description, price=EXCLUDED.price, stock_qty=EXCLUDED.stock_qty,
            unit=EXCLUDED.unit, barcode=EXCLUDED.barcode, tags=EXCLUDED.tags,
            meta=EXCLUDED.meta, source=EXCLUDED.source, updated_at=NOW()
        """,
        product.get("sku"), product.get("name", ""), product.get("brand"),
        product.get("category"), product.get("description"), product.get("price"),
        product.get("stock_qty", 0), product.get("unit", "un"), product.get("barcode"),
        source, product.get("tags", []), json.dumps(product.get("meta", {})),
    )


# ── REST API Connector ────────────────────────────────────────────────────────

class RestApiConnector(InventoryConnector):
    source = "rest_api"

    async def fetch_products(self, config: dict, credentials: dict) -> list[dict]:
        base_url: str = config["base_url"]
        endpoint: str = config.get("endpoint", "/products")
        auth_type: str = config.get("auth_type", "bearer")  # bearer | basic | api_key
        mapping: dict = config.get("field_mapping", {})

        headers: dict = {}
        if auth_type == "bearer":
            headers["Authorization"] = f"Bearer {credentials.get('token','')}"
        elif auth_type == "api_key":
            headers[credentials.get("header_name", "X-Api-Key")] = credentials.get("api_key", "")
        elif auth_type == "basic":
            import base64
            cred_str = f"{credentials.get('username','')}:{credentials.get('password','')}"
            encoded = base64.b64encode(cred_str.encode()).decode()
            headers["Authorization"] = f"Basic {encoded}"

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{base_url}{endpoint}", headers=headers)
        resp.raise_for_status()

        data = resp.json()
        items = data if isinstance(data, list) else data.get(config.get("list_key", "data"), [])

        return [_apply_mapping(item, mapping) for item in items]


# ── SQL Connector ─────────────────────────────────────────────────────────────

class SqlConnector(InventoryConnector):
    source = "sql"

    async def fetch_products(self, config: dict, credentials: dict) -> list[dict]:
        dsn: str = credentials.get("dsn", "")
        query: str = config.get("query", "SELECT * FROM products")
        mapping: dict = config.get("field_mapping", {})

        if not dsn:
            raise ValueError("SQL connector: DSN not configured")

        try:
            import asyncpg
            conn = await asyncpg.connect(dsn, command_timeout=30, statement_timeout=30000)
            try:
                rows = await conn.fetch(query)
            finally:
                await conn.close()
        except Exception as exc:
            raise RuntimeError(f"SQL sync failed: {exc}") from exc

        return [_apply_mapping(dict(r), mapping) for r in rows]


# ── CSV Connector (import from upload) ───────────────────────────────────────

class CsvConnector(InventoryConnector):
    source = "csv"

    async def import_csv(self, tenant_id: str, schema: str, content: bytes, mapping: dict) -> dict:
        """Parse a CSV file and import products. Called directly (not via sync job)."""
        reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
        products = [_apply_mapping(dict(row), mapping) for row in reader]
        errors: list[str] = []
        records_upd = 0

        for product in products:
            try:
                async with tenant_conn(schema) as conn:
                    await _upsert_product(conn, product, self.source)
                    records_upd += 1
            except Exception as exc:
                errors.append(str(exc))

        return {"records_in": len(products), "records_upd": records_upd, "errors": errors}

    async def fetch_products(self, config: dict, credentials: dict) -> list[dict]:
        return []  # CSV import is manual, not scheduled


# ── Mapping helper ────────────────────────────────────────────────────────────

def _apply_mapping(item: dict, mapping: dict) -> dict:
    """Translate external field names to internal schema using a mapping dict.
    If no mapping provided, use item as-is (assumes field names match).
    """
    if not mapping:
        return item
    return {internal: item.get(external) for internal, external in mapping.items() if external in item}


CONNECTOR_REGISTRY: dict[str, type[InventoryConnector]] = {
    "rest_api": RestApiConnector,
    "sql": SqlConnector,
    "csv": CsvConnector,
}
