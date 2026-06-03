"""
Job: expire_abandoned_carts (capability `sales.abandoned_cart`, config
`expire_minutes`).

Roda no beat a cada 2 min. Para cada tenant ativo com a capability ON e
`expire_minutes > 0`, varre carrinhos que:

  • já receberam a mensagem de recuperação (`sent_recovery_at IS NOT NULL`);
  • o cliente NÃO retornou desde então (`updated_at <= sent_recovery_at`);
  • o prazo de `expire_minutes` venceu;
  • ainda têm itens.

Para cada hit (em transação curta, com re-check via UPDATE/DELETE
condicional para vencer race "cliente respondeu entre SELECT e DELETE"):

  1) INSERT em `orders` com snapshot completo (status='expired').
  2) DELETE do cart.
  3) Fora da transação: encerra a sessão (`closed_at`, limpa histórico Redis)
     e envia a mensagem final de expiração via caminho do broker
     (mesmo path da recuperação — invariante [[proactive-uses-broker-path]]).

Sem retry automático: se algo falhar entre commit e envio, na próxima rodada
o cart já não está mais lá (foi deletado), então não há reenvio. Erros vão
para o log estruturado (`expire.send_failed` / `expire.tx_failed`).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import structlog

from db.postgres import get_db_conn, init_pool
from services import capabilities as cap_svc
from services.outbound import send_proactive_message
from services.persona import load_persona

log = structlog.get_logger()


# Default usado se o tenant ainda não tem `expire_message_template` salvo
# (capability criada antes da migration 052, ou tenant que herdou só o default
# velho do catálogo). Mesma string da migration — duplicada aqui pra resiliência.
DEFAULT_EXPIRE_TEMPLATE = (
    "Oi{nome_cliente}! Aqui é o(a) {agent_name}. "
    "Como não tive retorno, encerrei o atendimento por aqui — "
    "mas seu interesse por *{itens}*{mais_itens} fica registrado. "
    "Quando quiser retomar, é só me chamar. 👋"
)


def _load_items(raw) -> list:
    """Aceita array, string-double-encoded e None. Devolve list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


async def _expire_one(
    schema_name: str,
    session_key: str,
    persona: dict,
    template: str,
    tenant_id: str,
) -> bool:
    """Processa 1 cart expirado. Retorna True se efetivamente expirou.

    Re-check transacional: o DELETE só acontece se a janela ainda for válida
    (updated_at <= sent_recovery_at). Se o cliente respondeu entre o SELECT
    do varredor e este momento, `cart.updated_at` mudou e o DELETE retorna
    0 linhas — abortamos sem registrar order.
    """
    from workers.jobs.abandoned_cart import _build_message  # reusa render

    customer_phone: str | None = None
    customer_name: str | None = None
    items: list = []
    subtotal: float = 0.0
    order_id: str | None = None

    try:
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")
            async with conn.transaction():
                # 1) Lock + re-check da janela. SKIP LOCKED para não brigar com
                #    outras rodadas do mesmo beat caso uma demore.
                row = await conn.fetchrow(
                    """
                    SELECT items, COALESCE(subtotal, 0) AS subtotal,
                           sent_recovery_at, updated_at
                      FROM cart
                     WHERE session_key = $1
                       AND sent_recovery_at IS NOT NULL
                       AND updated_at <= sent_recovery_at
                       AND public.safe_jsonb_array_length(items) > 0
                       FOR UPDATE SKIP LOCKED
                    """,
                    session_key,
                )
                if not row:
                    return False  # já não é mais candidato

                items = _load_items(row["items"])
                subtotal = float(row["subtotal"] or 0)
                if not items:
                    return False

                # 2) Resolve customer (FK). LEFT JOIN igual ao do varredor.
                cust = await conn.fetchrow(
                    """
                    SELECT id, name, phone
                      FROM customers
                     WHERE phone = $1
                        OR phone = NULLIF(SPLIT_PART($1, ':', 2), '')
                     LIMIT 1
                    """,
                    session_key,
                )
                customer_id = cust["id"] if cust else None
                customer_phone = cust["phone"] if cust else None
                customer_name  = cust["name"]  if cust else None

                # 3) Snapshot em orders. items vai como jsonb — o codec do
                #    asyncpg (db/postgres.py) cuida da serialização, sem
                #    pré-dump (invariante [[jsonb-double-encoding]]).
                order = await conn.fetchrow(
                    """
                    INSERT INTO orders
                        (customer_id, session_key, items, subtotal, total,
                         status, notes)
                    VALUES ($1, $2, $3, $4, $4, 'expired', $5)
                    RETURNING id
                    """,
                    customer_id, session_key, items, subtotal,
                    "expirado por inatividade após recuperação",
                )
                order_id = str(order["id"])

                # 4) Apaga o cart. Re-checa janela no WHERE — se o cliente
                #    acabou de mexer no cart (entre FOR UPDATE e aqui é
                #    impossível dentro da transação, mas o WHERE adicional
                #    serve de defesa em profundidade) o DELETE retorna 0
                #    e a transação aborta no raise abaixo.
                deleted = await conn.execute(
                    """
                    DELETE FROM cart
                     WHERE session_key = $1
                       AND sent_recovery_at IS NOT NULL
                       AND updated_at <= sent_recovery_at
                    """,
                    session_key,
                )
                # asyncpg devolve "DELETE n" — abortamos se 0.
                if deleted.endswith(" 0"):
                    raise RuntimeError("cart desapareceu entre lock e delete")
    except Exception as exc:  # noqa: BLE001
        log.warning("expire.tx_failed",
                    tenant=tenant_id, session=session_key, exc=str(exc))
        return False

    # 5) Encerramento da sessão + mensagem final (best-effort, fora da txn).
    phone_for_send = customer_phone
    if not phone_for_send:
        # session_key normalmente já é o phone (só dígitos)
        sk = session_key or ""
        if sk.isdigit():
            phone_for_send = sk
        elif ":" in sk:
            for part in sk.split(":")[1:]:
                if part.isdigit():
                    phone_for_send = part
                    break

    if phone_for_send:
        try:
            from services import conversation_state as cs
            await cs.end_session(
                tenant_id, phone_for_send,
                by="system:recovery_expired",
                reason=f"expired_after_recovery:order={order_id}",
                clear_history=True,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("expire.end_session_failed",
                        tenant=tenant_id, session=session_key, exc=str(exc))

        body = _build_message(
            persona, items, customer_name,
            template=template, subtotal=subtotal,
        )
        try:
            ok = await send_proactive_message(
                tenant_id, phone_for_send, body,
                kind="cart_expired",
                extra={"session_key": session_key,
                       "order_id":    order_id,
                       "subtotal":    subtotal},
            )
            if not ok:
                log.warning("expire.send_failed",
                            tenant=tenant_id, session=session_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("expire.send_exception",
                        tenant=tenant_id, session=session_key, exc=str(exc))

    log.info("expire.cart_expired",
             tenant=tenant_id, session=session_key, order_id=order_id,
             items=len(items), subtotal=subtotal)
    return True


async def _process_tenant(tenant_id: str, schema_name: str) -> dict:
    """Expira carrinhos de 1 tenant. Retorna stats."""
    stats = {"checked": 0, "expired": 0, "skipped": 0, "errors": 0}

    enabled = await cap_svc.is_enabled(tenant_id, "sales.abandoned_cart")
    if not enabled:
        return stats

    cfg = await cap_svc.get_config(tenant_id, "sales.abandoned_cart")
    try:
        expire_minutes = int(cfg.get("expire_minutes", 0) or 0)
    except (TypeError, ValueError):
        expire_minutes = 0

    if expire_minutes <= 0:
        return stats  # tenant desativou explicitamente
    if expire_minutes > 240:
        expire_minutes = 240  # cap por segurança (UI limita, mas defense in depth)

    template = (cfg.get("expire_message_template") or "").strip() or DEFAULT_EXPIRE_TEMPLATE

    persona = {}
    try:
        persona = await load_persona(tenant_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("expire.persona_load_failed", tenant=tenant_id, exc=str(exc))

    cutoff_seconds = expire_minutes * 60

    try:
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")
            rows = await conn.fetch(
                """
                SELECT session_key
                  FROM cart c
                 WHERE sent_recovery_at IS NOT NULL
                   AND EXTRACT(EPOCH FROM (NOW() - sent_recovery_at)) >= $1
                   AND updated_at <= sent_recovery_at
                   AND public.safe_jsonb_array_length(items) > 0
                   -- Guard simétrico ao do varredor de recuperação: cart
                   -- com order posterior já foi fechado (balcão/ERP).
                   AND NOT EXISTS (
                       SELECT 1 FROM orders o
                        WHERE o.session_key = c.session_key
                          AND o.created_at >= c.updated_at
                   )
                 ORDER BY sent_recovery_at ASC
                 LIMIT 100
                """,
                cutoff_seconds,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("expire.scan_failed", tenant=tenant_id, exc=str(exc))
        return stats

    for r in rows:
        stats["checked"] += 1
        try:
            ok = await _expire_one(
                schema_name, r["session_key"], persona, template, tenant_id,
            )
            if ok:
                stats["expired"] += 1
            else:
                stats["skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("expire.iter_failed",
                        tenant=tenant_id, session=r["session_key"], exc=str(exc))
            stats["errors"] += 1

    return stats


async def _run_for_all_tenants() -> dict:
    # Celery roda via asyncio.run() → loop novo a cada task. O pool asyncpg
    # vive vinculado ao loop em que foi criado; sem init aqui a primeira
    # query falha com "cannot perform operation: another operation is in
    # progress" (pool órfão de outro loop). init_pool é idempotente para o
    # loop corrente. Ver [[pgbouncer-setup]].
    await init_pool()

    async with get_db_conn() as conn:
        tenants = await conn.fetch(
            "SELECT id::text, schema_name FROM public.tenants "
            "WHERE active = TRUE AND schema_name IS NOT NULL"
        )

    totals = {"tenants": 0, "checked": 0, "expired": 0,
              "skipped": 0, "errors": 0}
    for t in tenants:
        totals["tenants"] += 1
        try:
            s = await _process_tenant(t["id"], t["schema_name"])
            for k in ("checked", "expired", "skipped", "errors"):
                totals[k] += s.get(k, 0)
        except Exception as exc:  # noqa: BLE001
            log.warning("expire.tenant_failed",
                        tenant=t["id"], exc=str(exc))
            totals["errors"] += 1

    log.info("expire.abandoned_cart.done", **totals)
    return totals


def expire_abandoned_carts_sync() -> dict:
    """Entrypoint sync para Celery."""
    return asyncio.run(_run_for_all_tenants())
