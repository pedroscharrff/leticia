"""
Billing service — abstract provider + Stripe + Asaas implementations.
Manages subscriptions, invoices, and usage enforcement via Redis counters.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, date

import structlog

from config import settings
from db.postgres import get_db_conn
from db.redis_client import get_redis

log = structlog.get_logger()


# ── Usage enforcement (Redis counters) ────────────────────────────────────────

async def increment_usage(tenant_id: str, metric: str = "msgs") -> int:
    """Increment monthly usage counter. Returns new count."""
    period = date.today().strftime("%Y-%m")
    redis = get_redis()
    key = f"usage:{tenant_id}:{metric}:{period}"
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60 * 60 * 24 * 40)  # 40 days TTL
    return count


async def get_usage(tenant_id: str, metric: str = "msgs") -> int:
    period = date.today().strftime("%Y-%m")
    redis = get_redis()
    val = await redis.get(f"usage:{tenant_id}:{metric}:{period}")
    return int(val) if val else 0


async def check_usage_allowed(tenant_id: str) -> tuple[bool, str]:
    """Returns (allowed, reason). Check subscription status + monthly limit."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT s.status, p.limits, t.plan
            FROM public.subscriptions s
            JOIN public.plans p ON p.plan_name = s.plan_name
            JOIN public.tenants t ON t.id = s.tenant_id
            WHERE s.tenant_id = $1
            """,
            tenant_id,
        )

    if not row:
        return True, "ok"  # no subscription yet = allow (trial or onboarding)

    if row["status"] in ("canceled", "paused"):
        return False, f"Assinatura {row['status']}"

    if row["status"] == "past_due":
        return False, "Pagamento pendente — acesso suspenso"

    limits = row["limits"] or {}
    msgs_limit = limits.get("msgs_month")
    if msgs_limit is not None:
        current = await get_usage(tenant_id, "msgs")
        if current >= msgs_limit:
            return False, f"Limite mensal de {msgs_limit} mensagens atingido"

    return True, "ok"


# ── Billing provider interface ────────────────────────────────────────────────

class BillingProvider(ABC):
    @abstractmethod
    async def create_subscription(self, tenant_id: str, plan_name: str, customer_data: dict) -> dict:
        ...

    @abstractmethod
    async def cancel_subscription(self, external_id: str) -> None:
        ...

    @abstractmethod
    async def get_payment_url(self, external_id: str) -> str | None:
        ...

    @abstractmethod
    async def handle_webhook(self, payload: dict, signature: str) -> dict:
        ...


# ── Stripe provider ───────────────────────────────────────────────────────────

class StripeProvider(BillingProvider):
    def __init__(self):
        import stripe
        stripe.api_key = settings.stripe_api_key
        self._stripe = stripe

    async def create_subscription(self, tenant_id: str, plan_name: str, customer_data: dict) -> dict:
        import asyncio
        loop = asyncio.get_event_loop()

        async with get_db_conn() as conn:
            price_row = await conn.fetchrow(
                "SELECT stripe_price_id, price_brl FROM public.plans WHERE plan_name = $1", plan_name
            )
        if not price_row or not price_row["stripe_price_id"]:
            raise ValueError(f"Stripe price ID not configured for plan '{plan_name}'")

        customer = await loop.run_in_executor(None, lambda: self._stripe.Customer.create(
            email=customer_data.get("email"),
            name=customer_data.get("name"),
            metadata={"tenant_id": tenant_id},
        ))

        subscription = await loop.run_in_executor(None, lambda: self._stripe.Subscription.create(
            customer=customer.id,
            items=[{"price": price_row["stripe_price_id"]}],
            trial_period_days=settings.default_trial_days,
            metadata={"tenant_id": tenant_id, "plan": plan_name},
        ))

        return {
            "provider": "stripe",
            "external_id": subscription.id,
            "customer_id": customer.id,
            "status": subscription.status,
            "trial_ends_at": datetime.fromtimestamp(subscription.trial_end) if subscription.trial_end else None,
        }

    async def cancel_subscription(self, external_id: str) -> None:
        import asyncio
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: self._stripe.Subscription.cancel(external_id))

    async def get_payment_url(self, external_id: str) -> str | None:
        return None  # handled via Stripe hosted portal

    async def handle_webhook(self, payload: dict, signature: str) -> dict:
        import stripe
        try:
            event = stripe.Webhook.construct_event(
                json.dumps(payload).encode(), signature, settings.stripe_webhook_secret
            )
        except stripe.error.SignatureVerificationError:
            return {"error": "invalid_signature"}

        event_type = event["type"]
        data = event["data"]["object"]
        tenant_id = data.get("metadata", {}).get("tenant_id")

        if not tenant_id:
            return {"ignored": True}

        new_status = None
        if event_type == "customer.subscription.updated":
            new_status = data.get("status")
        elif event_type == "customer.subscription.deleted":
            new_status = "canceled"
        elif event_type == "invoice.payment_failed":
            new_status = "past_due"
        elif event_type == "invoice.payment_succeeded":
            new_status = "active"
            await _record_invoice_paid(tenant_id, data)

        if new_status:
            async with get_db_conn() as conn:
                await conn.execute(
                    "UPDATE public.subscriptions SET status = $1, updated_at = NOW() WHERE tenant_id = $2",
                    new_status, tenant_id,
                )
            log.info("billing.subscription_updated", tenant=tenant_id, status=new_status)

        return {"processed": event_type}


# ── Asaas provider (BR) ───────────────────────────────────────────────────────

class AsaasProvider(BillingProvider):
    def __init__(self):
        import httpx
        self._base = settings.asaas_base_url
        self._key = settings.asaas_api_key
        self._headers = {"access_token": self._key, "Content-Type": "application/json"}

    async def create_subscription(self, tenant_id: str, plan_name: str, customer_data: dict) -> dict:
        import httpx
        async with get_db_conn() as conn:
            price_row = await conn.fetchrow(
                "SELECT asaas_plan_id, price_brl FROM public.plans WHERE plan_name = $1", plan_name
            )

        async with httpx.AsyncClient(timeout=30) as client:
            # Create customer
            cust_resp = await client.post(
                f"{self._base}/customers",
                headers=self._headers,
                json={"name": customer_data.get("name"), "email": customer_data.get("email"),
                      "cpfCnpj": customer_data.get("doc", ""), "externalReference": tenant_id},
            )
            cust_resp.raise_for_status()
            cust_id = cust_resp.json()["id"]

            # Create subscription
            amount = float(price_row["price_brl"]) if price_row else 0
            sub_resp = await client.post(
                f"{self._base}/subscriptions",
                headers=self._headers,
                json={
                    "customer": cust_id,
                    "billingType": "BOLETO",
                    "value": amount,
                    "nextDueDate": datetime.now().strftime("%Y-%m-28"),
                    "cycle": "MONTHLY",
                    "description": f"Plano {plan_name}",
                    "externalReference": tenant_id,
                },
            )
            sub_resp.raise_for_status()
            sub_data = sub_resp.json()

        return {
            "provider": "asaas",
            "external_id": sub_data["id"],
            "customer_id": cust_id,
            "status": "trialing",
            "trial_ends_at": None,
        }

    async def cancel_subscription(self, external_id: str) -> None:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            await client.delete(f"{self._base}/subscriptions/{external_id}", headers=self._headers)

    async def get_payment_url(self, external_id: str) -> str | None:
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(f"{self._base}/subscriptions/{external_id}/payments", headers=self._headers)
        data = resp.json()
        payments = data.get("data", [])
        if payments:
            return payments[0].get("invoiceUrl")
        return None

    async def handle_webhook(self, payload: dict, signature: str) -> dict:
        event = payload.get("event", "")
        data = payload.get("payment", {})
        tenant_id = data.get("externalReference")

        if not tenant_id:
            return {"ignored": True}

        if event in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
            async with get_db_conn() as conn:
                await conn.execute(
                    "UPDATE public.subscriptions SET status = 'active', updated_at = NOW() WHERE tenant_id = $1",
                    tenant_id,
                )
            await _record_invoice_paid(tenant_id, data)

        elif event in ("PAYMENT_OVERDUE",):
            async with get_db_conn() as conn:
                await conn.execute(
                    "UPDATE public.subscriptions SET status = 'past_due', updated_at = NOW() WHERE tenant_id = $1",
                    tenant_id,
                )

        return {"processed": event}


# ── Helper ────────────────────────────────────────────────────────────────────

async def _record_invoice_paid(tenant_id: str, data: dict) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.invoices (tenant_id, provider, external_id, status, amount_brl, paid_at)
            VALUES ($1, $2, $3, 'paid', $4, NOW())
            ON CONFLICT DO NOTHING
            """,
            tenant_id,
            data.get("provider", "unknown"),
            str(data.get("id", "")),
            float(data.get("value", data.get("amount_total", 0)) or 0) / 100,
        )


def get_billing_provider(provider: str = "stripe") -> BillingProvider:
    if provider == "stripe":
        return StripeProvider()
    if provider == "asaas":
        return AsaasProvider()
    raise ValueError(f"Unknown billing provider: {provider}")
