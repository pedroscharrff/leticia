"""
Billing router — subscription management, invoices, usage, plan upgrade/downgrade.
Webhook endpoints for Stripe and Asaas.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Header, Request, status
from pydantic import BaseModel

from config import settings
from db.postgres import get_db_conn
from security import require_admin, require_tenant_user, TenantUserContext
from services.audit import log_event
from services.billing import get_billing_provider, get_usage, check_usage_allowed

log = structlog.get_logger()

router = APIRouter(tags=["billing"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]
AdminUser = Annotated[str, Depends(require_admin)]


# ── Models ────────────────────────────────────────────────────────────────────

class SubscriptionOut(BaseModel):
    tenant_id: str
    plan_name: str
    provider: str
    status: str
    trial_ends_at: datetime | None
    current_period_end: datetime | None


class InvoiceOut(BaseModel):
    id: str
    status: str
    amount_brl: float
    due_date: str | None
    paid_at: datetime | None
    invoice_url: str | None
    created_at: datetime


class UsageOut(BaseModel):
    msgs_this_month: int
    limit_msgs: int | None
    plan: str
    subscription_status: str


class ChangePlanBody(BaseModel):
    plan_name: str
    provider: str = "stripe"


class CreateSubscriptionBody(BaseModel):
    plan_name: str
    provider: str = "stripe"
    customer_name: str
    customer_email: str
    customer_doc: str | None = None


# ── Portal billing routes ─────────────────────────────────────────────────────

@router.get("/portal/billing/subscription", response_model=SubscriptionOut)
async def get_subscription(user: TenantUser) -> SubscriptionOut:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.subscriptions WHERE tenant_id = $1", user.tenant_id
        )
    if not row:
        # No subscription yet — return trial stub
        async with get_db_conn() as conn:
            tenant = await conn.fetchrow("SELECT plan FROM public.tenants WHERE id = $1", user.tenant_id)
        return SubscriptionOut(
            tenant_id=user.tenant_id,
            plan_name=tenant["plan"] if tenant else "basic",
            provider="manual",
            status="trialing",
            trial_ends_at=None,
            current_period_end=None,
        )
    return SubscriptionOut(
        tenant_id=str(row["tenant_id"]),
        plan_name=row["plan_name"],
        provider=row["provider"],
        status=row["status"],
        trial_ends_at=row["trial_ends_at"],
        current_period_end=row["current_period_end"],
    )


@router.get("/portal/billing/usage", response_model=UsageOut)
async def get_billing_usage(user: TenantUser) -> UsageOut:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT t.plan, p.limits,
                   COALESCE(s.status, 'trialing') AS sub_status
            FROM public.tenants t
            JOIN public.plans p ON p.plan_name = t.plan
            LEFT JOIN public.subscriptions s ON s.tenant_id = t.id
            WHERE t.id = $1
            """,
            user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404)

    msgs = await get_usage(user.tenant_id, "msgs")
    limits = row["limits"] or {}

    return UsageOut(
        msgs_this_month=msgs,
        limit_msgs=limits.get("msgs_month"),
        plan=row["plan"],
        subscription_status=row["sub_status"],
    )


@router.get("/portal/billing/invoices", response_model=list[InvoiceOut])
async def list_invoices(user: TenantUser) -> list[InvoiceOut]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.invoices WHERE tenant_id = $1 ORDER BY created_at DESC LIMIT 24",
            user.tenant_id,
        )
    return [
        InvoiceOut(
            id=str(r["id"]),
            status=r["status"],
            amount_brl=float(r["amount_brl"]),
            due_date=r["due_date"].isoformat() if r["due_date"] else None,
            paid_at=r["paid_at"],
            invoice_url=r["invoice_url"],
            created_at=r["created_at"],
        )
        for r in rows
    ]


@router.post("/portal/billing/subscribe", status_code=status.HTTP_201_CREATED)
async def create_or_upgrade_subscription(body: CreateSubscriptionBody, user: TenantUser) -> dict:
    """Create first subscription or upgrade/downgrade plan."""
    user.assert_role("owner")

    provider = get_billing_provider(body.provider)
    result = await provider.create_subscription(
        tenant_id=user.tenant_id,
        plan_name=body.plan_name,
        customer_data={
            "name": body.customer_name,
            "email": body.customer_email,
            "doc": body.customer_doc,
        },
    )

    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.subscriptions
                (tenant_id, plan_name, provider, external_id, status, trial_ends_at)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT (tenant_id) DO UPDATE SET
                plan_name = EXCLUDED.plan_name,
                provider = EXCLUDED.provider,
                external_id = EXCLUDED.external_id,
                status = EXCLUDED.status,
                trial_ends_at = EXCLUDED.trial_ends_at,
                updated_at = NOW()
            """,
            user.tenant_id, body.plan_name, body.provider,
            result.get("external_id"), result.get("status", "trialing"),
            result.get("trial_ends_at"),
        )
        # Also update tenant plan
        await conn.execute(
            "UPDATE public.tenants SET plan = $1 WHERE id = $2",
            body.plan_name, user.tenant_id,
        )

    await log_event("billing.subscribed", user.email, tenant_id=user.tenant_id,
                    meta={"plan": body.plan_name, "provider": body.provider})
    return result


@router.post("/portal/billing/cancel")
async def cancel_subscription(user: TenantUser) -> dict:
    user.assert_role("owner")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.subscriptions WHERE tenant_id = $1", user.tenant_id
        )
    if not row:
        raise HTTPException(status_code=404, detail="Sem assinatura ativa")

    provider = get_billing_provider(row["provider"])
    await provider.cancel_subscription(row["external_id"])

    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.subscriptions SET status = 'canceled', canceled_at = NOW(), updated_at = NOW() WHERE tenant_id = $1",
            user.tenant_id,
        )

    await log_event("billing.canceled", user.email, tenant_id=user.tenant_id)
    return {"status": "canceled"}


# ── Billing webhooks ──────────────────────────────────────────────────────────

@router.post("/billing/webhook/stripe", include_in_schema=False)
async def stripe_webhook(request: Request) -> dict:
    body = await request.body()
    signature = request.headers.get("stripe-signature", "")
    payload = json.loads(body)
    provider = get_billing_provider("stripe")
    return await provider.handle_webhook(payload, signature)


@router.post("/billing/webhook/asaas", include_in_schema=False)
async def asaas_webhook(request: Request) -> dict:
    payload = await request.json()
    provider = get_billing_provider("asaas")
    return await provider.handle_webhook(payload, "")


# ── Admin: list all subscriptions ─────────────────────────────────────────────

@router.get("/admin/billing/subscriptions")
async def admin_list_subscriptions(_admin: AdminUser) -> list[dict]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT s.*, t.name AS tenant_name
            FROM public.subscriptions s
            JOIN public.tenants t ON t.id = s.tenant_id
            ORDER BY s.updated_at DESC
            """
        )
    return [dict(r) for r in rows]
