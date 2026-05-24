"""
Job: nudge_continuous_meds_refill (capability `sales.continuous_refill_nudge`).

1x por dia, percorre tenants ativos. Para cada tenant onde a capability
estiver ON E `attendance.customer_memory` estiver ON:
  • Lê config (days_before_refill, time_of_day).
  • Busca clientes com `continuous_meds` cuja próxima cartela acabaria em
    até `days_before_refill` dias.
  • Dispara mensagem proativa personalizada e marca `last_nudge_at` no
    item do JSONB para evitar dupla notificação.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, date, timedelta, timezone

import structlog

from db.postgres import get_db_conn
from services import capabilities as cap_svc
from services.outbound import send_proactive_message
from services.persona import load_persona

log = structlog.get_logger()


def _parse_date(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:  # noqa: BLE001
        return None


def _build_message(persona: dict, customer_name: str | None,
                   med_name: str, days_left: int) -> str:
    agent_name = persona.get("agent_name") or "Atendente"
    greet = f"Oi {customer_name}!" if customer_name else "Oi!"
    when = "está acabando essa semana" if days_left <= 3 else f"acaba em ~{days_left} dias"
    return (
        f"{greet} 👋 Aqui é o(a) {agent_name}. Vi aqui que sua *{med_name}* "
        f"{when}. Quer que eu já separe uma reposição? Se quiser, posso fazer "
        "a entrega ou deixar separado pra você buscar — me diga o que prefere."
    )


async def _process_tenant(tenant_id: str, schema_name: str) -> dict:
    stats = {"checked": 0, "nudged": 0, "errors": 0}

    if not await cap_svc.is_enabled(tenant_id, "sales.continuous_refill_nudge"):
        return stats
    if not await cap_svc.is_enabled(tenant_id, "attendance.customer_memory"):
        # dep requirement — não roda sem memória do cliente
        return stats

    cfg = await cap_svc.get_config(tenant_id, "sales.continuous_refill_nudge")
    days_before = int(cfg.get("days_before_refill", 3))

    persona = {}
    try:
        persona = await load_persona(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("refill.persona_load_failed", tenant=tenant_id, exc=str(exc))

    today    = date.today()
    cutoff   = today + timedelta(days=days_before)
    today_iso = today.isoformat()

    try:
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")
            rows = await conn.fetch(
                """
                SELECT phone, name, continuous_meds
                  FROM customers
                 WHERE continuous_meds IS NOT NULL
                   AND jsonb_array_length(continuous_meds) > 0
                """
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("refill.query_failed", tenant=tenant_id, exc=str(exc))
        return stats

    for r in rows:
        stats["checked"] += 1
        phone = r["phone"]
        if not phone:
            continue

        meds_raw = r["continuous_meds"]
        if isinstance(meds_raw, str):
            try: meds = json.loads(meds_raw)
            except json.JSONDecodeError: continue
        else:
            meds = list(meds_raw or [])

        nudged_this_customer = False
        updated_meds = list(meds)

        for idx, m in enumerate(meds):
            if not isinstance(m, dict):
                continue
            freq = int(m.get("frequency_days") or 0)
            if freq <= 0:
                continue
            last_refill = _parse_date(m.get("last_refill_at"))
            if not last_refill:
                continue

            next_refill_due = last_refill + timedelta(days=freq)
            if next_refill_due > cutoff:
                continue

            # Evita nudge dobrado: só dispara se last_nudge_at é anterior ao
            # último refill (ou inexistente).
            last_nudge = _parse_date(m.get("last_nudge_at"))
            if last_nudge and last_nudge >= last_refill:
                continue

            days_left = (next_refill_due - today).days
            body = _build_message(persona, r["name"], m.get("name", "medicamento"),
                                   max(0, days_left))
            ok = await send_proactive_message(
                tenant_id, phone, body, kind="refill_nudge",
                extra={"medicamento": m.get("name"), "days_left": days_left},
            )
            if not ok:
                stats["errors"] += 1
                continue

            updated_meds[idx] = {**m, "last_nudge_at": today_iso}
            nudged_this_customer = True
            stats["nudged"] += 1

        if nudged_this_customer:
            try:
                async with get_db_conn() as conn:
                    await conn.execute(f"SET search_path = {schema_name}, public")
                    await conn.execute(
                        "UPDATE customers SET continuous_meds = $2::jsonb, "
                        "updated_at = NOW() WHERE phone = $1",
                        phone, json.dumps(updated_meds),
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("refill.mark_failed",
                            tenant=tenant_id, phone=phone, exc=str(exc))
                stats["errors"] += 1

    return stats


async def _run_for_all_tenants() -> dict:
    async with get_db_conn() as conn:
        tenants = await conn.fetch(
            "SELECT id::text, schema_name FROM public.tenants "
            "WHERE active = TRUE AND schema_name IS NOT NULL"
        )

    totals = {"tenants": 0, "checked": 0, "nudged": 0, "errors": 0}
    for t in tenants:
        totals["tenants"] += 1
        try:
            s = await _process_tenant(t["id"], t["schema_name"])
            for k in ("checked", "nudged", "errors"):
                totals[k] += s.get(k, 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("refill.tenant_failed", tenant=t["id"], exc=str(exc))
            totals["errors"] += 1

    log.info("refill.nudge.done", **totals)
    return totals


def nudge_continuous_refill_sync() -> dict:
    """Entrypoint sync para Celery."""
    return asyncio.run(_run_for_all_tenants())
