"""
Job: recover_abandoned_carts (capability `sales.abandoned_cart`).

A cada hora, percorre TODOS os tenants ativos. Para cada um:
  • Se a capability `sales.abandoned_cart` está OFF → pula.
  • Lê config (delay_hours, max_attempts, quiet_start, quiet_end).
  • Busca carrinhos parados por > delay_hours, com itens, e que ainda
    não esgotaram max_attempts.
  • Para cada candidato, gera mensagem personalizada e dispara via
    callback_url do tenant.
  • Marca `sent_recovery_at = NOW()` e incrementa `recovery_attempts`.

Tolerante a falhas: erros num tenant não afetam os outros.
Respeita business_hours/quiet_hours — não envia de madrugada.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, timedelta, time
from zoneinfo import ZoneInfo

import structlog

from db.postgres import get_db_conn
from services import capabilities as cap_svc
from services.outbound import send_proactive_message
from services.persona import load_persona

log = structlog.get_logger()


def _parse_hhmm(value, default: time) -> time:
    if not value:
        return default
    try:
        hh, mm = str(value).split(":")
        return time(int(hh), int(mm))
    except Exception:  # noqa: BLE001
        return default


def _is_quiet_hour(now: datetime, quiet_start: time, quiet_end: time) -> bool:
    """Retorna True se NOW está dentro da janela de silêncio."""
    n = now.time()
    if quiet_start <= quiet_end:
        return quiet_start <= n < quiet_end
    # janela cruza meia-noite (ex: 21:00 → 08:00)
    return n >= quiet_start or n < quiet_end


# Template default — usado se a capability `sales.abandoned_cart` não trouxer
# `message_template` em config (ex: tenant criado antes da migration 051).
# Mesmo texto que estava hardcoded antes.
DEFAULT_MESSAGE_TEMPLATE = (
    "{saudacao} Aqui é o(a) {agent_name}. 👋 "
    "Vi que você deixou *{itens}*{mais_itens} no carrinho mais cedo. "
    "Quer que eu finalize o pedido pra você, ou prefere ajustar algo?"
)


class _SafeDict(dict):
    """dict que devolve "" para chaves ausentes — evita KeyError no format()."""
    def __missing__(self, key: str) -> str:
        return ""


def _normalize_item_names(cart_items: list) -> list[str]:
    """Aceita itens com chave PT (nome) ou EN (name); ignora vazios."""
    out: list[str] = []
    for i in cart_items or []:
        if not isinstance(i, dict):
            continue
        nome = (i.get("nome") or i.get("name") or "").strip()
        if nome:
            out.append(nome)
    return out


def _fmt_brl(v: float) -> str:
    try:
        return f"R$ {float(v):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "R$ 0,00"


def _build_message(
    persona: dict,
    cart_items: list,
    customer_name: str | None,
    *,
    template: str | None = None,
    subtotal: float | None = None,
) -> str:
    """Renderiza a mensagem de recuperação a partir de um template.

    Template vem do config da capability `sales.abandoned_cart` (editável
    no portal — ver `routers/payments.py /portal/recovery/template`).
    Se `template` for vazio ou None, usa `DEFAULT_MESSAGE_TEMPLATE`.

    Placeholders ausentes viram string vazia (não levanta).
    """
    tpl = (template or "").strip() or DEFAULT_MESSAGE_TEMPLATE

    agent_name = (persona.get("agent_name") or "Atendente").strip()
    nome_cli = (customer_name or "").strip()
    saudacao = f"Oi {nome_cli}!" if nome_cli else "Oi!"

    names = _normalize_item_names(cart_items)
    itens_preview = ", ".join(names[:3])
    mais = f" e mais {len(names) - 3} item(ns)" if len(names) > 3 else ""

    ctx = _SafeDict(
        saudacao=saudacao,
        nome_cliente=nome_cli,
        agent_name=agent_name,
        itens=itens_preview,
        qtde_itens=len(names),
        mais_itens=mais,
        subtotal=_fmt_brl(subtotal) if subtotal is not None else "",
    )
    try:
        return tpl.format_map(ctx).strip()
    except Exception:
        # Template malformado (ex: { sem fechar) — cai no default em vez de
        # quebrar o disparo inteiro.
        return DEFAULT_MESSAGE_TEMPLATE.format_map(ctx).strip()


async def _process_tenant(tenant_id: str, schema_name: str) -> dict:
    """Roda recuperação para 1 tenant. Retorna stats."""
    stats = {"checked": 0, "sent": 0, "skipped_quiet": 0, "errors": 0}

    enabled = await cap_svc.is_enabled(tenant_id, "sales.abandoned_cart")
    if not enabled:
        return stats

    cfg = await cap_svc.get_config(tenant_id, "sales.abandoned_cart")
    delay_hours      = int(cfg.get("delay_hours", 4))
    max_attempts     = int(cfg.get("max_attempts", 1))
    quiet_start      = _parse_hhmm(cfg.get("quiet_start"), time(21, 0))
    quiet_end        = _parse_hhmm(cfg.get("quiet_end"),   time(8,  0))
    message_template = cfg.get("message_template")  # None → usa default

    # Quiet hours são configurados em horário local do tenant (Brasil).
    # Container roda em UTC — sem conversão, "21h–08h" virava "18h–05h"
    # de Brasília, silenciando na hora errada. Default America/Sao_Paulo
    # cobre 100% dos tenants atuais; quando houver tenant fora do BR, esta
    # zona vira config de tenant.
    _BR_TZ = ZoneInfo("America/Sao_Paulo")
    now = datetime.now(tz=_BR_TZ)
    if _is_quiet_hour(now, quiet_start, quiet_end):
        stats["skipped_quiet"] = 1
        return stats

    persona = {}
    try:
        persona = await load_persona(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("recover.persona_load_failed", tenant=tenant_id, exc=str(exc))

    cutoff = datetime.now(timezone.utc) - timedelta(hours=delay_hours)

    try:
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")
            rows = await conn.fetch(
                """
                SELECT c.session_key, c.items, c.subtotal, c.recovery_attempts,
                       cu.phone, cu.name
                  FROM cart c
                  LEFT JOIN customers cu ON cu.phone = SPLIT_PART(c.session_key, ':', 2)
                 WHERE c.updated_at < $1
                   -- Helper trata array, string (double-encoded) e escalar.
                   -- Ver [[jsonb-array-typeof-guard]] + [[jsonb-double-encoding]].
                   AND public.safe_jsonb_array_length(c.items) > 0
                   AND c.recovery_attempts < $2
                   AND (c.sent_recovery_at IS NULL OR c.sent_recovery_at < $1)
                 ORDER BY c.updated_at DESC
                 LIMIT 50
                """,
                cutoff, max_attempts,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("recover.cart_query_failed", tenant=tenant_id, exc=str(exc))
        return stats

    for r in rows:
        stats["checked"] += 1
        phone = r["phone"]
        if not phone:
            # session_key pode não conter telefone parseável — pula
            continue

        items_raw = r["items"]
        if isinstance(items_raw, str):
            try: items = json.loads(items_raw)
            except json.JSONDecodeError: items = []
        else:
            items = list(items_raw or [])

        body = _build_message(
            persona, items, r["name"],
            template=message_template,
            subtotal=float(r["subtotal"] or 0),
        )
        ok = await send_proactive_message(
            tenant_id, phone, body,
            kind="cart_recovery",
            extra={"session_key": r["session_key"],
                   "subtotal": float(r["subtotal"] or 0)},
        )
        if not ok:
            stats["errors"] += 1
            continue

        # Marca tentativa (incrementa attempts)
        try:
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")
                await conn.execute(
                    """
                    UPDATE cart
                       SET sent_recovery_at  = NOW(),
                           recovery_attempts = recovery_attempts + 1
                     WHERE session_key = $1
                    """,
                    r["session_key"],
                )
            stats["sent"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("recover.mark_failed",
                        tenant=tenant_id, session=r["session_key"], exc=str(exc))
            stats["errors"] += 1

    return stats


async def _run_for_all_tenants() -> dict:
    async with get_db_conn() as conn:
        tenants = await conn.fetch(
            "SELECT id::text, schema_name FROM public.tenants "
            "WHERE active = TRUE AND schema_name IS NOT NULL"
        )

    totals = {"tenants": 0, "checked": 0, "sent": 0,
              "skipped_quiet": 0, "errors": 0}
    for t in tenants:
        totals["tenants"] += 1
        try:
            s = await _process_tenant(t["id"], t["schema_name"])
            for k in ("checked", "sent", "skipped_quiet", "errors"):
                totals[k] += s.get(k, 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("recover.tenant_failed",
                        tenant=t["id"], exc=str(exc))
            totals["errors"] += 1

    log.info("recover.abandoned_cart.done", **totals)
    return totals


def recover_abandoned_carts_sync() -> dict:
    """Entrypoint sync para Celery."""
    return asyncio.run(_run_for_all_tenants())
