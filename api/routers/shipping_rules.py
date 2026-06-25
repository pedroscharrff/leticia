"""
Regras de frete por CEP (capability `delivery.shipping_by_cep`).

Portal (manager+):
  GET    /portal/shipping-rules
  POST   /portal/shipping-rules
  PATCH  /portal/shipping-rules/{id}
  DELETE /portal/shipping-rules/{id}
"""
from __future__ import annotations

import re
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_tenant_user, TenantUserContext
from services.audit import log_event

log = structlog.get_logger()
router = APIRouter(prefix="/portal/shipping-rules", tags=["portal:shipping"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]

_CEP_RE = re.compile(r"^\d{5}-?\d{3}$")


def _validate_cep(cep: str, field: str) -> str:
    cep = (cep or "").strip()
    if not _CEP_RE.match(cep):
        raise HTTPException(
            status_code=422,
            detail=f"{field} inválido. Use formato 00000-000.",
        )
    digits = "".join(c for c in cep if c.isdigit())
    return f"{digits[:5]}-{digits[5:]}"


class ShippingRuleIn(BaseModel):
    label:        str = Field(min_length=1, max_length=120)
    cep_start:    str
    cep_end:      str
    valor:        float = Field(ge=0)
    prazo_dias:   int   = Field(ge=0, le=60)
    gratis_acima: float | None = Field(default=None, ge=0)
    active:       bool = True
    sort_order:   int  = 100


class ShippingRuleUpdate(BaseModel):
    label:        str | None = None
    cep_start:    str | None = None
    cep_end:      str | None = None
    valor:        float | None = Field(default=None, ge=0)
    prazo_dias:   int   | None = Field(default=None, ge=0, le=60)
    gratis_acima: float | None = Field(default=None, ge=0)
    active:       bool | None = None
    sort_order:   int  | None = None


class ShippingRuleOut(BaseModel):
    id:           str
    label:        str
    cep_start:    str
    cep_end:      str
    valor:        float
    prazo_dias:   int
    gratis_acima: float | None
    active:       bool
    sort_order:   int


def _row(r) -> ShippingRuleOut:
    return ShippingRuleOut(
        id=str(r["id"]),
        label=r["label"],
        cep_start=r["cep_start"],
        cep_end=r["cep_end"],
        valor=float(r["valor"] or 0),
        prazo_dias=int(r["prazo_dias"] or 0),
        gratis_acima=float(r["gratis_acima"]) if r["gratis_acima"] is not None else None,
        active=bool(r["active"]),
        sort_order=int(r["sort_order"] or 100),
    )


@router.get("", response_model=list[ShippingRuleOut])
async def list_rules(user: TenantUser) -> list[ShippingRuleOut]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.tenant_shipping_rules "
            "WHERE tenant_id = $1 ORDER BY sort_order, label",
            user.tenant_id,
        )
    return [_row(r) for r in rows]


@router.post("", response_model=ShippingRuleOut)
async def create_rule(
    payload: ShippingRuleIn,
    user: TenantUser,
) -> ShippingRuleOut:
    user.assert_role("manager")
    cep_start = _validate_cep(payload.cep_start, "cep_start")
    cep_end   = _validate_cep(payload.cep_end,   "cep_end")
    if cep_start > cep_end:
        raise HTTPException(status_code=422,
                            detail="cep_start deve ser ≤ cep_end")

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_shipping_rules
                (tenant_id, label, cep_start, cep_end, valor, prazo_dias,
                 gratis_acima, active, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            RETURNING *
            """,
            user.tenant_id, payload.label, cep_start, cep_end,
            payload.valor, payload.prazo_dias, payload.gratis_acima,
            payload.active, payload.sort_order,
        )

    await log_event(
        action="shipping_rule.create",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=str(row["id"]),
        meta={"label": payload.label, "valor": payload.valor},
    )
    return _row(row)


@router.patch("/{rule_id}", response_model=ShippingRuleOut)
async def update_rule(
    rule_id: str,
    payload: ShippingRuleUpdate,
    user: TenantUser,
) -> ShippingRuleOut:
    user.assert_role("manager")

    updates: list[str] = []
    params: list = []
    i = 1
    data = payload.model_dump(exclude_unset=True)

    if "cep_start" in data and data["cep_start"] is not None:
        data["cep_start"] = _validate_cep(data["cep_start"], "cep_start")
    if "cep_end" in data and data["cep_end"] is not None:
        data["cep_end"] = _validate_cep(data["cep_end"], "cep_end")

    for col in ("label", "cep_start", "cep_end", "valor", "prazo_dias",
                "gratis_acima", "active", "sort_order"):
        if col in data:
            updates.append(f"{col} = ${i}")
            params.append(data[col])
            i += 1

    if not updates:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")

    updates.append("updated_at = NOW()")
    params.extend([rule_id, user.tenant_id])
    sql = (f"UPDATE public.tenant_shipping_rules SET {', '.join(updates)} "
           f"WHERE id = ${i} AND tenant_id = ${i+1} RETURNING *")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(sql, *params)
    if not row:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    return _row(row)


@router.delete("/{rule_id}")
async def delete_rule(rule_id: str, user: TenantUser) -> Response:
    user.assert_role("manager")
    async with get_db_conn() as conn:
        ok = await conn.fetchval(
            "DELETE FROM public.tenant_shipping_rules "
            "WHERE id = $1 AND tenant_id = $2 RETURNING id",
            rule_id, user.tenant_id,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Regra não encontrada")
    await log_event(
        action="shipping_rule.delete",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=rule_id, meta={},
    )
    return Response(status_code=204)


# ═════════════════════════════════════════════════════════════════════════════
# Origem da farmácia + modo de frete (cep_table | distance)
# ═════════════════════════════════════════════════════════════════════════════

origin_router = APIRouter(prefix="/portal/shipping-origin", tags=["portal:shipping"])


class ShippingOriginIn(BaseModel):
    mode:            str = Field(default="cep_table", pattern="^(cep_table|distance)$")
    distance_source: str = Field(default="haversine", pattern="^(haversine|google)$")
    cep:             str | None = None


class ShippingOriginOut(BaseModel):
    mode:             str
    distance_source:  str
    cep:              str | None
    lat:              float | None
    lng:              float | None
    resolved_address: str | None
    geocoded:         bool
    google_available: bool   # plataforma tem chave do Google Maps configurada


def _google_available() -> bool:
    try:
        from config import settings
        return bool(getattr(settings, "google_maps_api_key", "") or "")
    except Exception:  # noqa: BLE001
        return False


def _origin_row(r) -> ShippingOriginOut:
    return ShippingOriginOut(
        mode=r["mode"],
        distance_source=r["distance_source"],
        cep=r["cep"],
        lat=float(r["lat"]) if r["lat"] is not None else None,
        lng=float(r["lng"]) if r["lng"] is not None else None,
        resolved_address=r["resolved_address"],
        geocoded=r["lat"] is not None and r["lng"] is not None,
        google_available=_google_available(),
    )


@origin_router.get("", response_model=ShippingOriginOut)
async def get_origin(user: TenantUser) -> ShippingOriginOut:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenant_shipping_origin WHERE tenant_id = $1",
            user.tenant_id,
        )
    if not row:
        return ShippingOriginOut(
            mode="cep_table", distance_source="haversine", cep=None,
            lat=None, lng=None, resolved_address=None, geocoded=False,
            google_available=_google_available(),
        )
    return _origin_row(row)


@origin_router.put("", response_model=ShippingOriginOut)
async def put_origin(payload: ShippingOriginIn, user: TenantUser) -> ShippingOriginOut:
    user.assert_role("manager")

    cep = _validate_cep(payload.cep, "cep") if payload.cep else None

    # Geocoda o CEP de origem (1x, na hora de salvar). Falha fechada: salva sem
    # coordenada e o tool cai para a tabela de CEP até o operador reenviar.
    lat = lng = None
    resolved = None
    geocoded_at = None
    if cep:
        try:
            from services.geocoding import geocode_cep
            from datetime import datetime, timezone
            pt = await geocode_cep(cep)
            if pt:
                lat, lng, resolved = pt.lat, pt.lng, pt.address
                geocoded_at = datetime.now(timezone.utc)
        except Exception as exc:  # noqa: BLE001
            log.warning("shipping_origin.geocode_failed", exc=str(exc))

    if payload.mode == "distance" and cep and lat is None:
        raise HTTPException(
            status_code=422,
            detail="Não consegui localizar as coordenadas desse CEP. "
                   "Confira o CEP da farmácia ou use o modo por faixa de CEP.",
        )

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_shipping_origin
                (tenant_id, mode, distance_source, cep, lat, lng,
                 resolved_address, geocoded_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (tenant_id) DO UPDATE SET
                mode = EXCLUDED.mode,
                distance_source = EXCLUDED.distance_source,
                cep = EXCLUDED.cep,
                lat = EXCLUDED.lat,
                lng = EXCLUDED.lng,
                resolved_address = EXCLUDED.resolved_address,
                geocoded_at = EXCLUDED.geocoded_at,
                updated_at = NOW()
            RETURNING *
            """,
            user.tenant_id, payload.mode, payload.distance_source, cep,
            lat, lng, resolved, geocoded_at,
        )

    await log_event(
        action="shipping_origin.update",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=user.tenant_id,
        meta={"mode": payload.mode, "distance_source": payload.distance_source,
              "geocoded": lat is not None},
    )
    return _origin_row(row)


# ═════════════════════════════════════════════════════════════════════════════
# Faixas de raio (km) → valor + prazo
# ═════════════════════════════════════════════════════════════════════════════

tiers_router = APIRouter(prefix="/portal/shipping-tiers", tags=["portal:shipping"])


class ShippingTierIn(BaseModel):
    label:           str   = Field(min_length=1, max_length=120)
    max_distance_km: float = Field(gt=0, le=500)
    valor:           float = Field(ge=0)
    prazo_dias:      int   = Field(ge=0, le=60)
    gratis_acima:    float | None = Field(default=None, ge=0)
    active:          bool  = True
    sort_order:      int   = 100


class ShippingTierUpdate(BaseModel):
    label:           str | None = None
    max_distance_km: float | None = Field(default=None, gt=0, le=500)
    valor:           float | None = Field(default=None, ge=0)
    prazo_dias:      int   | None = Field(default=None, ge=0, le=60)
    gratis_acima:    float | None = Field(default=None, ge=0)
    active:          bool  | None = None
    sort_order:      int   | None = None


class ShippingTierOut(BaseModel):
    id:              str
    label:           str
    max_distance_km: float
    valor:           float
    prazo_dias:      int
    gratis_acima:    float | None
    active:          bool
    sort_order:      int


def _tier_row(r) -> ShippingTierOut:
    return ShippingTierOut(
        id=str(r["id"]),
        label=r["label"],
        max_distance_km=float(r["max_distance_km"]),
        valor=float(r["valor"] or 0),
        prazo_dias=int(r["prazo_dias"] or 0),
        gratis_acima=float(r["gratis_acima"]) if r["gratis_acima"] is not None else None,
        active=bool(r["active"]),
        sort_order=int(r["sort_order"] or 100),
    )


@tiers_router.get("", response_model=list[ShippingTierOut])
async def list_tiers(user: TenantUser) -> list[ShippingTierOut]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.tenant_shipping_distance_tiers "
            "WHERE tenant_id = $1 ORDER BY max_distance_km, sort_order",
            user.tenant_id,
        )
    return [_tier_row(r) for r in rows]


@tiers_router.post("", response_model=ShippingTierOut)
async def create_tier(payload: ShippingTierIn, user: TenantUser) -> ShippingTierOut:
    user.assert_role("manager")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.tenant_shipping_distance_tiers
                (tenant_id, label, max_distance_km, valor, prazo_dias,
                 gratis_acima, active, sort_order)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING *
            """,
            user.tenant_id, payload.label, payload.max_distance_km, payload.valor,
            payload.prazo_dias, payload.gratis_acima, payload.active, payload.sort_order,
        )
    await log_event(
        action="shipping_tier.create",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=str(row["id"]),
        meta={"label": payload.label, "max_distance_km": payload.max_distance_km},
    )
    return _tier_row(row)


@tiers_router.patch("/{tier_id}", response_model=ShippingTierOut)
async def update_tier(
    tier_id: str, payload: ShippingTierUpdate, user: TenantUser,
) -> ShippingTierOut:
    user.assert_role("manager")
    updates: list[str] = []
    params: list = []
    i = 1
    data = payload.model_dump(exclude_unset=True)
    for col in ("label", "max_distance_km", "valor", "prazo_dias",
                "gratis_acima", "active", "sort_order"):
        if col in data:
            updates.append(f"{col} = ${i}")
            params.append(data[col])
            i += 1
    if not updates:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    updates.append("updated_at = NOW()")
    params.extend([tier_id, user.tenant_id])
    sql = (f"UPDATE public.tenant_shipping_distance_tiers SET {', '.join(updates)} "
           f"WHERE id = ${i} AND tenant_id = ${i+1} RETURNING *")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(sql, *params)
    if not row:
        raise HTTPException(status_code=404, detail="Faixa não encontrada")
    return _tier_row(row)


@tiers_router.delete("/{tier_id}")
async def delete_tier(tier_id: str, user: TenantUser) -> Response:
    user.assert_role("manager")
    async with get_db_conn() as conn:
        ok = await conn.fetchval(
            "DELETE FROM public.tenant_shipping_distance_tiers "
            "WHERE id = $1 AND tenant_id = $2 RETURNING id",
            tier_id, user.tenant_id,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Faixa não encontrada")
    await log_event(
        action="shipping_tier.delete",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=tier_id, meta={},
    )
    return Response(status_code=204)
