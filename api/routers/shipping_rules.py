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
