"""
Endpoints do portal para a capability `payments.pix_asaas`:

  GET    /portal/payments/status     — Asaas conectado? últimas cobranças?
  PUT    /portal/payments/asaas-key  — grava/atualiza o secret ASAAS_API_KEY
  DELETE /portal/payments/asaas-key  — remove

E para `sales.abandoned_cart` + `sales.continuous_refill_nudge`:
  GET    /portal/recovery/stats      — contadores das últimas execuções
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from db.postgres import get_db_conn
from security import require_tenant_user, TenantUserContext
from services import secrets as sec_svc
from services.audit import log_event

log = structlog.get_logger()

payments_router = APIRouter(prefix="/portal/payments", tags=["portal:payments"])
recovery_router = APIRouter(prefix="/portal/recovery", tags=["portal:recovery"])
order_summary_router = APIRouter(
    prefix="/portal/order-summary", tags=["portal:order_summary"],
)

TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


class AsaasKeyIn(BaseModel):
    api_key: str


class PaymentRow(BaseModel):
    id:          str
    order_id:    str | None
    phone:       str | None
    amount:      float
    status:      str
    created_at:  datetime
    paid_at:     datetime | None
    expires_at:  datetime | None


class PaymentsStatusOut(BaseModel):
    asaas_connected: bool
    pending_count:   int
    paid_last_30d:   int
    revenue_last_30d: float
    recent_charges:  list[PaymentRow]


@payments_router.get("/status", response_model=PaymentsStatusOut)
async def payments_status(user: TenantUser) -> PaymentsStatusOut:
    # Conexão: existe um secret ASAAS_API_KEY? Não decifra — apenas verifica.
    keys = await sec_svc.list_secret_keys(user.tenant_id)
    connected = "ASAAS_API_KEY" in keys

    since = datetime.now(timezone.utc) - timedelta(days=30)
    async with get_db_conn() as conn:
        pending = await conn.fetchval(
            "SELECT COUNT(*) FROM public.payments_log "
            "WHERE tenant_id = $1 AND status = 'pending'",
            user.tenant_id,
        )
        paid = await conn.fetchval(
            "SELECT COUNT(*) FROM public.payments_log "
            "WHERE tenant_id = $1 AND status = 'paid' AND paid_at >= $2",
            user.tenant_id, since,
        )
        revenue = await conn.fetchval(
            "SELECT COALESCE(SUM(amount), 0) FROM public.payments_log "
            "WHERE tenant_id = $1 AND status = 'paid' AND paid_at >= $2",
            user.tenant_id, since,
        )
        recent = await conn.fetch(
            "SELECT id, order_id, phone, amount, status, "
            "       created_at, paid_at, expires_at "
            "  FROM public.payments_log "
            " WHERE tenant_id = $1 "
            " ORDER BY created_at DESC LIMIT 20",
            user.tenant_id,
        )

    return PaymentsStatusOut(
        asaas_connected=connected,
        pending_count=int(pending or 0),
        paid_last_30d=int(paid or 0),
        revenue_last_30d=float(revenue or 0),
        recent_charges=[
            PaymentRow(
                id=str(r["id"]),
                order_id=str(r["order_id"]) if r["order_id"] else None,
                phone=r["phone"],
                amount=float(r["amount"] or 0),
                status=r["status"],
                created_at=r["created_at"],
                paid_at=r["paid_at"],
                expires_at=r["expires_at"],
            ) for r in recent
        ],
    )


@payments_router.put("/asaas-key")
async def set_asaas_key(payload: AsaasKeyIn, user: TenantUser) -> Response:
    user.assert_role("manager")
    key = (payload.api_key or "").strip()
    if not key or len(key) < 20:
        raise HTTPException(status_code=422,
                            detail="API key parece inválida.")
    await sec_svc.set_secret(user.tenant_id, "ASAAS_API_KEY", key)
    await log_event(
        action="payments.asaas_key_set", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="ASAAS_API_KEY", meta={},
    )
    return Response(status_code=204)


@payments_router.delete("/asaas-key")
async def delete_asaas_key(user: TenantUser) -> Response:
    user.assert_role("manager")
    await sec_svc.delete_secret(user.tenant_id, "ASAAS_API_KEY")
    await log_event(
        action="payments.asaas_key_removed", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="ASAAS_API_KEY", meta={},
    )
    return Response(status_code=204)


# ── Recovery (carrinho abandonado + recompra contínuo) ──────────────────────

class RecoveryStatsOut(BaseModel):
    carts_pending_recovery:   int     # carrinhos com itens > delay sem nudge
    carts_recovered_last_7d:  int     # carrinhos com sent_recovery_at recente
    refill_clients_total:     int     # clientes com continuous_meds não-vazio
    refills_nudged_last_30d:  int     # nudges enviados nos últimos 30 dias


@recovery_router.get("/stats", response_model=RecoveryStatsOut)
async def recovery_stats(user: TenantUser) -> RecoveryStatsOut:
    import asyncpg

    async def _safe_count(conn, sql: str, label: str) -> int:
        # Dois modos de falha:
        #   1) Schema drift (migrations 023/025 mudas): UndefinedColumn/Table.
        #   2) Dados sujos: ex. `cart.items` com valor escalar em vez de array
        #      dispara InvalidParameterValueError ("cannot get array length of
        #      a scalar"). O planner pode reordenar AND e avaliar
        #      jsonb_array_length antes do jsonb_typeof guard.
        # Em qualquer caso, melhor contar 0 e logar do que devolver 500.
        try:
            v = await conn.fetchval(sql)
            return int(v or 0)
        except asyncpg.PostgresError as e:
            log.warning("recovery.stats.query_failed",
                        tenant_id=str(user.tenant_id),
                        query=label,
                        error_type=type(e).__name__,
                        error=str(e))
            return 0

    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        schema = schema_row["schema_name"]

        await conn.execute(f"SET search_path = {schema}, public")

        # Self-heal: garante as colunas que as migrations 023/025 deveriam ter
        # adicionado. Idempotente; cobre tenants onde a migration foi
        # silenciosamente engolida pelo EXCEPTION WHEN OTHERS.
        try:
            await conn.execute(f"""
                ALTER TABLE {schema}.cart
                    ADD COLUMN IF NOT EXISTS sent_recovery_at  TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS recovery_attempts INTEGER NOT NULL DEFAULT 0
            """)
        except asyncpg.UndefinedTableError:
            log.warning("recovery.stats.no_cart_table", schema=schema)
        try:
            await conn.execute(f"""
                ALTER TABLE {schema}.customers
                    ADD COLUMN IF NOT EXISTS continuous_meds JSONB DEFAULT '[]'
            """)
        except asyncpg.UndefinedTableError:
            log.warning("recovery.stats.no_customers_table", schema=schema)

        # Carrinhos abandonados (itens > 0 + última atualização > 4h e sem nudge ainda)
        # CASE WHEN é o guard correto: jsonb_typeof num AND não impede o
        # planner de avaliar jsonb_array_length primeiro e estourar
        # InvalidParameterValueError quando `items` é um escalar/objeto.
        carts_pending = await _safe_count(conn, """
            SELECT COUNT(*) FROM cart
             WHERE public.safe_jsonb_array_length(items) > 0
               AND updated_at < NOW() - INTERVAL '4 hours'
               AND (sent_recovery_at IS NULL
                    OR sent_recovery_at < NOW() - INTERVAL '24 hours')
        """, "carts_pending")

        carts_recovered = await _safe_count(conn, """
            SELECT COUNT(*) FROM cart
             WHERE sent_recovery_at IS NOT NULL
               AND sent_recovery_at >= NOW() - INTERVAL '7 days'
        """, "carts_recovered")

        refill_clients = await _safe_count(conn, """
            SELECT COUNT(*) FROM customers
             WHERE public.safe_jsonb_array_length(continuous_meds) > 0
        """, "refill_clients")

        # Nudges enviados nos últimos 30 dias: contagem de last_nudge_at >= 30d
        refills_nudged = await _safe_count(conn, """
            SELECT COUNT(*) FROM customers c,
                 LATERAL jsonb_array_elements(
                     CASE WHEN jsonb_typeof(COALESCE(c.continuous_meds, '[]'::jsonb)) = 'array'
                          THEN COALESCE(c.continuous_meds, '[]'::jsonb)
                          ELSE '[]'::jsonb
                     END
                 ) m
             WHERE (m->>'last_nudge_at') IS NOT NULL
               AND (m->>'last_nudge_at')::timestamptz >= NOW() - INTERVAL '30 days'
        """, "refills_nudged")

    return RecoveryStatsOut(
        carts_pending_recovery=carts_pending,
        carts_recovered_last_7d=carts_recovered,
        refill_clients_total=refill_clients,
        refills_nudged_last_30d=refills_nudged,
    )


# ── Listagem de carrinhos (em andamento + já notificados) ───────────────────

class CartRowOut(BaseModel):
    session_key:        str
    phone:              str | None
    customer_name:      str | None
    items_count:        int
    items_preview:      list[dict]   # primeiros 5 itens normalizados {nome, quantidade, preco}
    subtotal:           float
    updated_at:         datetime
    sent_recovery_at:   datetime | None
    recovery_attempts:  int
    # Heurística simples de "status" para o portal:
    #   'recovered'  → sent_recovery_at preenchido nos últimos 7d
    #   'pending'    → tem itens, sem nudge OU nudge antigo
    #   'in_progress'→ atualizado nas últimas 4h (cliente ainda ativo)
    #   'expired'    → registro sintético vindo de orders.status='expired'
    #                  (cart já foi deletado pelo job de expiração)
    status:             str


@recovery_router.get("/carts", response_model=list[CartRowOut])
async def list_carts(user: TenantUser) -> list[CartRowOut]:
    """Lista até 100 carrinhos com pelo menos 1 item, ordenado por atividade.

    Inclui carrinhos em andamento (cliente ativo nas últimas horas) e os já
    notificados. O frontend usa o campo `status` para diferenciar.
    """
    import asyncpg

    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        schema = schema_row["schema_name"]

        await conn.execute(f"SET search_path = {schema}, public")

        try:
            # session_key padrão = phone (só dígitos), mas em alguns canais
            # antigos vinha como "<algo>:<phone>:<sufixo>". JOIN tenta os dois
            # formatos via LATERAL pra evitar duplicação de linhas. Também
            # devolve o jsonb `items` para o frontend renderizar a lista.
            #
            # UNION com orders.status='expired' dos últimos 7d para que
            # carrinhos expirados pelo job continuem aparecendo na página
            # de Recuperação como linhas sintéticas (session_key prefixado
            # com `expired:` para não colidir com PKs reais e sinalizar ao
            # frontend que a linha é read-only).
            rows = await conn.fetch(
                """
                SELECT * FROM (
                    SELECT c.session_key                                  AS session_key,
                           c.items                                        AS items_raw,
                           cu.name                                        AS customer_name,
                           cu.phone                                       AS customer_phone,
                           public.safe_jsonb_array_length(c.items)        AS items_count,
                           COALESCE(c.subtotal, 0)::float8                AS subtotal,
                           c.updated_at                                   AS updated_at,
                           c.sent_recovery_at                             AS sent_recovery_at,
                           COALESCE(c.recovery_attempts, 0)               AS recovery_attempts,
                           CASE
                             WHEN c.sent_recovery_at IS NOT NULL
                                  AND c.sent_recovery_at >= NOW() - INTERVAL '7 days'
                               THEN 'recovered'
                             WHEN c.updated_at >= NOW() - INTERVAL '4 hours'
                               THEN 'in_progress'
                             ELSE 'pending'
                           END                                            AS status
                      FROM cart c
                      LEFT JOIN LATERAL (
                           SELECT name, phone
                             FROM customers
                            WHERE phone = c.session_key
                               OR phone = NULLIF(SPLIT_PART(c.session_key, ':', 2), '')
                            LIMIT 1
                      ) cu ON TRUE
                     WHERE public.safe_jsonb_array_length(c.items) > 0

                    UNION ALL

                    SELECT ('expired:' || o.id::text)                    AS session_key,
                           o.items                                        AS items_raw,
                           cu.name                                        AS customer_name,
                           cu.phone                                       AS customer_phone,
                           public.safe_jsonb_array_length(o.items)        AS items_count,
                           COALESCE(o.subtotal, 0)::float8                AS subtotal,
                           o.created_at                                   AS updated_at,
                           o.created_at                                   AS sent_recovery_at,
                           0                                              AS recovery_attempts,
                           'expired'                                      AS status
                      FROM orders o
                      LEFT JOIN customers cu ON cu.id = o.customer_id
                     WHERE o.status = 'expired'
                       AND o.created_at >= NOW() - INTERVAL '7 days'
                ) AS combined
                 ORDER BY updated_at DESC
                 LIMIT 100
                """
            )
        except asyncpg.PostgresError as e:
            log.warning("recovery.list.query_failed",
                        tenant_id=str(user.tenant_id), error=str(e))
            return []

    import json as _json
    out: list[CartRowOut] = []
    for r in rows:
        # session_key normalmente é o próprio phone (só dígitos) — fallback
        # final do display caso o customer ainda não esteja cadastrado.
        phone_display = r["customer_phone"]
        if not phone_display:
            sk = (r["session_key"] or "")
            if sk.isdigit():
                phone_display = sk
            elif ":" in sk:
                parts = sk.split(":")
                phone_display = next((p for p in parts[1:] if p.isdigit()), None)

        # Normaliza items: aceita array, string-of-json (double-encoded
        # legado) e None. Devolve só primeiros 5 com campos do template
        # do vendedor (nome/quantidade/preco). Ver [[cart-lifecycle]].
        raw = r["items_raw"]
        items_list: list = []
        if isinstance(raw, list):
            items_list = raw
        elif isinstance(raw, str):
            try:
                parsed = _json.loads(raw)
                if isinstance(parsed, list):
                    items_list = parsed
            except _json.JSONDecodeError:
                items_list = []

        preview: list[dict] = []
        for it in items_list[:5]:
            if not isinstance(it, dict):
                continue
            # Vendedor usa chaves PT (nome/quantidade/preco). Aceita também
            # variantes EN (name/quantity/price) que aparecem em traces antigos.
            preview.append({
                "nome":       (it.get("nome") or it.get("name") or "").strip() or "—",
                "quantidade": int(it.get("quantidade") or it.get("qty") or it.get("quantity") or 1),
                "preco":      float(it.get("preco") or it.get("price") or 0),
            })

        out.append(CartRowOut(
            session_key=r["session_key"],
            phone=phone_display,
            customer_name=r["customer_name"],
            items_count=int(r["items_count"] or 0),
            items_preview=preview,
            subtotal=float(r["subtotal"] or 0),
            updated_at=r["updated_at"],
            sent_recovery_at=r["sent_recovery_at"],
            recovery_attempts=int(r["recovery_attempts"] or 0),
            status=r["status"],
        ))
    return out


# ── Template da mensagem de recuperação ─────────────────────────────────────
# Operador edita o texto que sai no disparo (manual e automático). Salvo na
# config da capability `sales.abandoned_cart`. Endpoints aqui em vez de no
# router genérico de capabilities pra dar UI dedicada na própria página de
# Recuperação (placeholder list + preview com carrinho real).

PLACEHOLDERS = [
    ("saudacao",     "\"Oi Maria!\" se houver nome, senão \"Oi!\""),
    ("nome_cliente", "Nome do cliente, ou vazio"),
    ("agent_name",   "Nome do agente configurado na persona"),
    ("itens",        "Até 3 nomes de produtos, ex: \"Dipirona, Tylenol\""),
    ("qtde_itens",   "Número total de itens no carrinho"),
    ("mais_itens",   "\" e mais N item(ns)\" quando passar de 3, senão vazio"),
    ("subtotal",     "Subtotal formatado, ex: \"R$ 89,90\""),
]


class TemplateOut(BaseModel):
    template:      str
    is_default:    bool       # True se o tenant nunca customizou (usa default do catálogo)
    default:       str        # Default do catálogo — exposto pra UI ter "Restaurar"
    placeholders:  list[dict]


class TemplateIn(BaseModel):
    template: str


class TemplatePreviewIn(BaseModel):
    template:     str | None = None  # None → usa o salvo
    session_key:  str | None = None  # quando dado, renderiza com cart real desse cliente


class TemplatePreviewOut(BaseModel):
    rendered:     str
    used_sample:  bool        # True se não havia session_key e caímos no sample


async def _get_abandoned_cart_defaults() -> tuple[str, dict, dict]:
    """Retorna (default_template, default_config, current_catalog_row).

    Default vem do `capability_catalog.default_config.message_template`.
    """
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT default_config FROM public.capability_catalog WHERE key = 'sales.abandoned_cart'"
        )
    default_cfg = {}
    if row and row["default_config"]:
        v = row["default_config"]
        if isinstance(v, dict):
            default_cfg = v
        elif isinstance(v, str):
            try:
                import json as _json
                parsed = _json.loads(v)
                default_cfg = parsed if isinstance(parsed, dict) else {}
            except Exception:
                default_cfg = {}
    # Fallback final caso a migration 051 ainda não tenha rodado
    from workers.jobs.abandoned_cart import DEFAULT_MESSAGE_TEMPLATE
    return (
        str(default_cfg.get("message_template") or DEFAULT_MESSAGE_TEMPLATE),
        default_cfg,
        dict(row) if row else {},
    )


@recovery_router.get("/template", response_model=TemplateOut)
async def get_template(user: TenantUser) -> TemplateOut:
    from services import capabilities as cap_svc
    tenant_cfg = await cap_svc.get_config(user.tenant_id, "sales.abandoned_cart")
    default_tpl, _, _ = await _get_abandoned_cart_defaults()

    tenant_tpl = tenant_cfg.get("message_template")
    # is_default = tenant não sobrescreveu OU sobrescreveu com o mesmo valor
    is_default = (tenant_tpl is None) or (str(tenant_tpl).strip() == default_tpl.strip())

    return TemplateOut(
        template=str(tenant_tpl or default_tpl),
        is_default=is_default,
        default=default_tpl,
        placeholders=[{"key": k, "desc": d} for k, d in PLACEHOLDERS],
    )


@recovery_router.put("/template", response_model=TemplateOut)
async def update_template(payload: TemplateIn, user: TenantUser) -> TemplateOut:
    """Salva o template novo na config da capability. Aceita string vazia para
    'voltar ao default' (limpa o override do tenant)."""
    user.assert_role("manager")
    from services import capabilities as cap_svc

    new_tpl = (payload.template or "").strip()
    # Sanity: tenta renderizar com sample pra não salvar template quebrado.
    try:
        _render_sample(new_tpl)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Template inválido: {e}",
        )

    # Lê estado atual e atualiza só o message_template, preservando demais
    # campos (delay_hours, max_attempts, quiet_*).
    current = await cap_svc.list_for_tenant(user.tenant_id)
    cap = next((c for c in current if c["key"] == "sales.abandoned_cart"), None)
    if not cap:
        raise HTTPException(status_code=404, detail="Capacidade não encontrada.")

    new_config = dict(cap.get("config") or {})
    if new_tpl:
        new_config["message_template"] = new_tpl
    else:
        # String vazia → remove override, herda default do catálogo
        new_config.pop("message_template", None)

    await cap_svc.set_enabled(
        tenant_id=str(user.tenant_id),
        key="sales.abandoned_cart",
        enabled=bool(cap.get("enabled")),
        config=new_config,
        user_id=user.email,
    )

    await log_event(
        action="recovery.template_updated", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="sales.abandoned_cart",
        meta={"length": len(new_tpl), "reset_to_default": not bool(new_tpl)},
    )

    return await get_template(user)  # devolve estado renovado


def _render_sample(template: str | None) -> str:
    """Render usando um sample fixo — usado pelo validador e pela preview
    sem session_key."""
    from workers.jobs.abandoned_cart import _build_message
    sample_persona = {"agent_name": "Ana"}
    sample_items = [
        {"nome": "Dipirona 500mg", "quantidade": 2, "preco": 7.50},
        {"nome": "Tylenol",        "quantidade": 1, "preco": 18.90},
    ]
    return _build_message(
        sample_persona, sample_items, "Maria",
        template=template, subtotal=33.90,
    )


@recovery_router.post("/template/preview", response_model=TemplatePreviewOut)
async def preview_template(
    payload: TemplatePreviewIn, user: TenantUser,
) -> TemplatePreviewOut:
    """Renderiza preview da mensagem.

    Se `session_key` for fornecido, busca o cart real desse cliente — assim o
    operador vê exatamente como a mensagem vai sair pra UMA pessoa específica.
    Senão, usa sample (Maria + Dipirona + Tylenol).
    """
    import json as _json
    from workers.jobs.abandoned_cart import _build_message
    from services.persona import load_persona
    from services import capabilities as cap_svc

    # Template: param > salvo > default
    if payload.template is not None:
        tpl = payload.template
    else:
        cfg = await cap_svc.get_config(user.tenant_id, "sales.abandoned_cart")
        tpl = cfg.get("message_template")
        if not tpl:
            tpl, _, _ = await _get_abandoned_cart_defaults()

    if not payload.session_key:
        return TemplatePreviewOut(
            rendered=_render_sample(tpl),
            used_sample=True,
        )

    # Render com cart real
    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        await conn.execute(f"SET search_path = {schema_row['schema_name']}, public")
        row = await conn.fetchrow(
            """
            SELECT c.items, c.subtotal,
                   cu.name AS customer_name
              FROM cart c
              LEFT JOIN LATERAL (
                   SELECT name FROM customers
                    WHERE phone = c.session_key
                       OR phone = NULLIF(SPLIT_PART(c.session_key, ':', 2), '')
                    LIMIT 1
              ) cu ON TRUE
             WHERE c.session_key = $1
            """,
            payload.session_key,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Carrinho não encontrado.")

    raw = row["items"]
    if isinstance(raw, str):
        try: items = _json.loads(raw)
        except _json.JSONDecodeError: items = []
    else:
        items = list(raw or [])

    persona = {}
    try:
        persona = await load_persona(user.tenant_id)
    except Exception:
        pass

    rendered = _build_message(
        persona, items, row["customer_name"],
        template=tpl,
        subtotal=float(row["subtotal"] or 0),
    )
    return TemplatePreviewOut(rendered=rendered, used_sample=False)


# ── Régua da recuperação (delay + tentativas + horário silencioso) ─────────
# Antes ficava só editável pelo card genérico de capability em Vendas ›
# Recursos. Trouxemos pra cá pra centralizar toda a régua de recuperação
# na mesma página. delay_minutes é o canônico desde a migration 054
# (substitui delay_hours; fallback preservado no job).

DEFAULT_RECOVERY_DELAY_MINUTES = 240    # = 4h (compat com delay_hours antigo)
DEFAULT_RECOVERY_MAX_ATTEMPTS  = 1
DEFAULT_RECOVERY_QUIET_START   = "21:00"
DEFAULT_RECOVERY_QUIET_END     = "08:00"


def _coerce_hhmm(value, fallback: str) -> str:
    """Normaliza "HH:MM" — aceita também "H:MM" e ints (== hora cheia)."""
    if value is None or value == "":
        return fallback
    s = str(value).strip()
    if ":" not in s:
        try:
            h = int(s)
            if 0 <= h <= 23:
                return f"{h:02d}:00"
        except ValueError:
            return fallback
        return fallback
    try:
        hh, mm = s.split(":", 1)
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return f"{h:02d}:{m:02d}"
    except ValueError:
        pass
    return fallback


class RecoveryConfigOut(BaseModel):
    delay_minutes:   int       # 1..1440
    max_attempts:    int       # 1..5
    quiet_start:     str       # "HH:MM"
    quiet_end:       str       # "HH:MM"
    default_minutes: int


class RecoveryConfigIn(BaseModel):
    delay_minutes: int
    max_attempts:  int
    quiet_start:   str
    quiet_end:     str


@recovery_router.get("/config", response_model=RecoveryConfigOut)
async def get_recovery_config(user: TenantUser) -> RecoveryConfigOut:
    from services import capabilities as cap_svc
    cfg = await cap_svc.get_config(user.tenant_id, "sales.abandoned_cart")

    # Lê delay_minutes; fallback delay_hours*60; fallback default.
    try:
        delay = int(cfg.get("delay_minutes") or 0)
    except (TypeError, ValueError):
        delay = 0
    if delay <= 0:
        try:
            delay = int(cfg.get("delay_hours") or 0) * 60
        except (TypeError, ValueError):
            delay = 0
    if delay <= 0:
        delay = DEFAULT_RECOVERY_DELAY_MINUTES

    try:
        attempts = int(cfg.get("max_attempts") or DEFAULT_RECOVERY_MAX_ATTEMPTS)
    except (TypeError, ValueError):
        attempts = DEFAULT_RECOVERY_MAX_ATTEMPTS

    return RecoveryConfigOut(
        delay_minutes=delay,
        max_attempts=attempts,
        quiet_start=_coerce_hhmm(cfg.get("quiet_start"), DEFAULT_RECOVERY_QUIET_START),
        quiet_end=_coerce_hhmm(cfg.get("quiet_end"),   DEFAULT_RECOVERY_QUIET_END),
        default_minutes=DEFAULT_RECOVERY_DELAY_MINUTES,
    )


@recovery_router.put("/config", response_model=RecoveryConfigOut)
async def update_recovery_config(payload: RecoveryConfigIn,
                                 user: TenantUser) -> RecoveryConfigOut:
    user.assert_role("manager")
    if payload.delay_minutes < 1 or payload.delay_minutes > 1440:
        raise HTTPException(status_code=422,
            detail="delay_minutes deve estar entre 1 e 1440 (1 min a 24h).")
    if payload.max_attempts < 1 or payload.max_attempts > 5:
        raise HTTPException(status_code=422,
            detail="max_attempts deve estar entre 1 e 5.")

    qs = _coerce_hhmm(payload.quiet_start, DEFAULT_RECOVERY_QUIET_START)
    qe = _coerce_hhmm(payload.quiet_end,   DEFAULT_RECOVERY_QUIET_END)

    from services import capabilities as cap_svc
    current = await cap_svc.list_for_tenant(user.tenant_id)
    cap = next((c for c in current if c["key"] == "sales.abandoned_cart"), None)
    if not cap:
        raise HTTPException(status_code=404, detail="Capacidade não encontrada.")

    new_config = dict(cap.get("config") or {})
    new_config["delay_minutes"] = int(payload.delay_minutes)
    new_config["max_attempts"]  = int(payload.max_attempts)
    new_config["quiet_start"]   = qs
    new_config["quiet_end"]     = qe
    # Mantém delay_hours alinhado pra tenants que olham o campo antigo
    # em algum dashboard externo. Arredonda pra cima.
    new_config["delay_hours"] = max(1, (int(payload.delay_minutes) + 59) // 60)

    await cap_svc.set_enabled(
        tenant_id=str(user.tenant_id),
        key="sales.abandoned_cart",
        enabled=bool(cap.get("enabled")),
        config=new_config,
        user_id=user.email,
    )
    await log_event(
        action="recovery.config_updated", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="sales.abandoned_cart",
        meta={"delay_minutes": int(payload.delay_minutes),
              "max_attempts":  int(payload.max_attempts),
              "quiet_start":   qs, "quiet_end": qe},
    )
    return await get_recovery_config(user)


# ── Expiração de carrinho após mensagem de recuperação ─────────────────────
# Tempo (`expire_minutes`) e template final (`expire_message_template`) ficam
# na MESMA config da capability `sales.abandoned_cart`. Endpoints separados
# do template de recuperação só por UX — mesma fonte de verdade.
# Job que consome: workers/jobs/expire_carts.py.


DEFAULT_EXPIRE_MINUTES = 60


async def _get_expire_defaults() -> tuple[int, str]:
    """Retorna (default_minutes, default_template) do catálogo."""
    from workers.jobs.expire_carts import DEFAULT_EXPIRE_TEMPLATE
    _, default_cfg, _ = await _get_abandoned_cart_defaults()
    try:
        d_min = int(default_cfg.get("expire_minutes", DEFAULT_EXPIRE_MINUTES))
    except (TypeError, ValueError):
        d_min = DEFAULT_EXPIRE_MINUTES
    d_tpl = str(default_cfg.get("expire_message_template") or DEFAULT_EXPIRE_TEMPLATE)
    return d_min, d_tpl


class ExpireConfigOut(BaseModel):
    expire_minutes:  int   # 0 = desativado, 1..240
    default_minutes: int
    min_minutes:     int = 0
    max_minutes:     int = 240


class ExpireConfigIn(BaseModel):
    expire_minutes: int


@recovery_router.get("/expire-config", response_model=ExpireConfigOut)
async def get_expire_config(user: TenantUser) -> ExpireConfigOut:
    from services import capabilities as cap_svc
    cfg = await cap_svc.get_config(user.tenant_id, "sales.abandoned_cart")
    d_min, _ = await _get_expire_defaults()
    try:
        cur = int(cfg.get("expire_minutes", d_min))
    except (TypeError, ValueError):
        cur = d_min
    return ExpireConfigOut(expire_minutes=cur, default_minutes=d_min)


@recovery_router.put("/expire-config", response_model=ExpireConfigOut)
async def update_expire_config(payload: ExpireConfigIn,
                               user: TenantUser) -> ExpireConfigOut:
    user.assert_role("manager")
    if payload.expire_minutes < 0 or payload.expire_minutes > 240:
        raise HTTPException(
            status_code=422,
            detail="expire_minutes deve estar entre 0 e 240 (0 = desativado).",
        )

    from services import capabilities as cap_svc
    current = await cap_svc.list_for_tenant(user.tenant_id)
    cap = next((c for c in current if c["key"] == "sales.abandoned_cart"), None)
    if not cap:
        raise HTTPException(status_code=404, detail="Capacidade não encontrada.")

    new_config = dict(cap.get("config") or {})
    new_config["expire_minutes"] = int(payload.expire_minutes)

    await cap_svc.set_enabled(
        tenant_id=str(user.tenant_id),
        key="sales.abandoned_cart",
        enabled=bool(cap.get("enabled")),
        config=new_config,
        user_id=user.email,
    )
    await log_event(
        action="recovery.expire_config_updated", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="sales.abandoned_cart",
        meta={"expire_minutes": int(payload.expire_minutes)},
    )
    return await get_expire_config(user)


@recovery_router.get("/expire-template", response_model=TemplateOut)
async def get_expire_template(user: TenantUser) -> TemplateOut:
    from services import capabilities as cap_svc
    cfg = await cap_svc.get_config(user.tenant_id, "sales.abandoned_cart")
    _, default_tpl = await _get_expire_defaults()
    tenant_tpl = cfg.get("expire_message_template")
    is_default = (tenant_tpl is None) or (str(tenant_tpl).strip() == default_tpl.strip())
    return TemplateOut(
        template=str(tenant_tpl or default_tpl),
        is_default=is_default,
        default=default_tpl,
        placeholders=[{"key": k, "desc": d} for k, d in PLACEHOLDERS],
    )


@recovery_router.put("/expire-template", response_model=TemplateOut)
async def update_expire_template(payload: TemplateIn,
                                 user: TenantUser) -> TemplateOut:
    user.assert_role("manager")
    new_tpl = (payload.template or "").strip()
    try:
        _render_sample(new_tpl)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Template inválido: {e}")

    from services import capabilities as cap_svc
    current = await cap_svc.list_for_tenant(user.tenant_id)
    cap = next((c for c in current if c["key"] == "sales.abandoned_cart"), None)
    if not cap:
        raise HTTPException(status_code=404, detail="Capacidade não encontrada.")

    new_config = dict(cap.get("config") or {})
    if new_tpl:
        new_config["expire_message_template"] = new_tpl
    else:
        new_config.pop("expire_message_template", None)

    await cap_svc.set_enabled(
        tenant_id=str(user.tenant_id),
        key="sales.abandoned_cart",
        enabled=bool(cap.get("enabled")),
        config=new_config,
        user_id=user.email,
    )
    await log_event(
        action="recovery.expire_template_updated", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="sales.abandoned_cart",
        meta={"length": len(new_tpl), "reset_to_default": not bool(new_tpl)},
    )
    return await get_expire_template(user)


@recovery_router.post("/expire-template/preview", response_model=TemplatePreviewOut)
async def preview_expire_template(payload: TemplatePreviewIn,
                                  user: TenantUser) -> TemplatePreviewOut:
    """Espelha /template/preview, mas usando o template de expiração."""
    import json as _json
    from workers.jobs.abandoned_cart import _build_message
    from services import capabilities as cap_svc

    if payload.template is not None:
        tpl = payload.template
    else:
        cfg = await cap_svc.get_config(user.tenant_id, "sales.abandoned_cart")
        tpl = cfg.get("expire_message_template")
        if not tpl:
            _, tpl = await _get_expire_defaults()

    if not payload.session_key:
        return TemplatePreviewOut(rendered=_render_sample(tpl), used_sample=True)

    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        await conn.execute(f"SET search_path = {schema_row['schema_name']}, public")
        row = await conn.fetchrow(
            """
            SELECT c.items, c.subtotal,
                   cu.name AS customer_name
              FROM cart c
              LEFT JOIN LATERAL (
                   SELECT name FROM customers
                    WHERE phone = c.session_key
                       OR phone = NULLIF(SPLIT_PART(c.session_key, ':', 2), '')
                    LIMIT 1
              ) cu ON TRUE
             WHERE c.session_key = $1
            """,
            payload.session_key,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Carrinho não encontrado.")

    raw = row["items"]
    if isinstance(raw, str):
        try: items = _json.loads(raw)
        except _json.JSONDecodeError: items = []
    else:
        items = list(raw or [])

    persona = {}
    try:
        persona = await load_persona(user.tenant_id)
    except Exception:
        pass

    rendered = _build_message(
        persona, items, row["customer_name"],
        template=tpl, subtotal=float(row["subtotal"] or 0),
    )
    return TemplatePreviewOut(rendered=rendered, used_sample=False)


# ── Template do resumo de pedido enviado no handoff ────────────────────────
# Endpoints do template da capability `sales.order_summary_after_handoff`
# (migration 044). Mora aqui pra ficar perto dos outros editores de mensagem
# proativa, mas tem página dedicada (PortalResumoPedido) no portal. Toda a
# config (header / item / total / footer) vai num único endpoint, porque a
# UI edita os 5 campos juntos com 1 botão Salvar.

ORDER_SUMMARY_KEY = "sales.order_summary_after_handoff"

ORDER_SUMMARY_PLACEHOLDERS = [
    ("nome",        "Nome do produto"),
    ("quantidade",  "Quantidade pedida"),
    ("preco_unit",  "Preço unitário (R$ x,xx). Vazio se pré-atendimento."),
    ("preco_total", "Preço × quantidade (R$ x,xx). Vazio se pré-atendimento."),
    ("preco",       "Alias de preco_unit."),
]


# Campos do template editáveis. Fonte única para os merges/loops abaixo —
# adicionar campo novo = incluir aqui + no model + no schema da migration.
ORDER_SUMMARY_FIELDS = (
    "header_text", "item_template", "show_total", "total_label",
    "show_payment", "payment_label", "show_address", "address_label",
    "footer_text",
)


class OrderSummaryConfigOut(BaseModel):
    header_text:    str
    item_template:  str
    show_total:     bool
    total_label:    str
    show_payment:   bool
    payment_label:  str
    show_address:   bool
    address_label:  str
    footer_text:    str
    is_default:     bool                 # nenhum campo foi customizado
    defaults:       dict                 # do catálogo — usado pelo "Restaurar"
    placeholders:   list[dict]
    enabled:        bool                 # toggle da capability


class OrderSummaryConfigIn(BaseModel):
    header_text:    str | None = None
    item_template:  str | None = None
    show_total:     bool | None = None
    total_label:    str | None = None
    show_payment:   bool | None = None
    payment_label:  str | None = None
    show_address:   bool | None = None
    address_label:  str | None = None
    footer_text:    str | None = None


class OrderSummaryPreviewIn(BaseModel):
    # Qualquer campo None → cai pro valor salvo. Tudo None → preview do salvo.
    header_text:    str | None = None
    item_template:  str | None = None
    show_total:     bool | None = None
    total_label:    str | None = None
    show_payment:   bool | None = None
    payment_label:  str | None = None
    show_address:   bool | None = None
    address_label:  str | None = None
    footer_text:    str | None = None
    # Sample por padrão; quando fornecido um session_key real, busca cart.
    session_key:    str | None = None
    # Força preview no modo pré-atendimento (sem preços). Default: auto.
    no_prices:      bool | None = None


class OrderSummaryPreviewOut(BaseModel):
    rendered:    str
    used_sample: bool


async def _get_order_summary_defaults() -> dict:
    """Lê `default_config` do catálogo (migration 044)."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT default_config FROM public.capability_catalog WHERE key = $1",
            ORDER_SUMMARY_KEY,
        )
    defaults: dict = {}
    if row and row["default_config"]:
        v = row["default_config"]
        if isinstance(v, dict):
            defaults = v
        elif isinstance(v, str):
            try:
                import json as _json
                parsed = _json.loads(v)
                defaults = parsed if isinstance(parsed, dict) else {}
            except Exception:
                defaults = {}
    # Fallbacks finais — o catálogo deveria ter, mas defesa em profundidade
    defaults.setdefault("header_text",   "📋 *Resumo do seu pedido:*")
    defaults.setdefault("item_template", "• {quantidade}x {nome} — {preco_total}")
    defaults.setdefault("show_total",    True)
    defaults.setdefault("total_label",   "*Total*")
    defaults.setdefault("show_payment",  True)
    defaults.setdefault("payment_label", "*Pagamento*")
    defaults.setdefault("show_address",  True)
    defaults.setdefault("address_label", "*Entrega*")
    defaults.setdefault("footer_text",   "")
    return defaults


def _merge_summary_cfg(saved: dict, defaults: dict) -> dict:
    """Para cada campo do template, prefere o tenant, cai pro default."""
    merged = {k: saved.get(k, defaults[k]) for k in ORDER_SUMMARY_FIELDS}
    # Campos booleanos precisam de coerção (config pode trazer string/None).
    for b in ("show_total", "show_payment", "show_address"):
        merged[b] = bool(merged[b])
    return merged


@order_summary_router.get("/config", response_model=OrderSummaryConfigOut)
async def get_order_summary_config(user: TenantUser) -> OrderSummaryConfigOut:
    from services import capabilities as cap_svc

    defaults = await _get_order_summary_defaults()
    tenant_cfg = await cap_svc.get_config(user.tenant_id, ORDER_SUMMARY_KEY) or {}
    merged = _merge_summary_cfg(tenant_cfg, defaults)

    is_default = all(
        tenant_cfg.get(k) is None or tenant_cfg.get(k) == defaults.get(k)
        for k in ORDER_SUMMARY_FIELDS
    )

    enabled = False
    try:
        enabled = await cap_svc.is_enabled(user.tenant_id, ORDER_SUMMARY_KEY)
    except Exception:
        pass

    return OrderSummaryConfigOut(
        **merged,
        is_default=is_default,
        defaults=defaults,
        placeholders=[{"key": k, "desc": d} for k, d in ORDER_SUMMARY_PLACEHOLDERS],
        enabled=enabled,
    )


def _render_summary_preview(cfg: dict, *, no_prices: bool) -> str:
    """Renderiza o resumo com cart sample. Função pura — sem I/O."""
    from services.order_summary import build_summary_text
    # Endereço sample p/ demonstrar a linha de entrega em ambos os modos.
    address = "Av. Paulista, 1000, Bela Vista, São Paulo/SP, CEP 01310-100"
    if no_prices:
        # Pré-atendimento: sem preço e sem forma de pagamento (resolvidos no balcão).
        items = [
            {"nome": "Dipirona 500mg",      "quantidade": 2, "preco": 0},
            {"nome": "Soro fisiológico",    "quantidade": 1, "preco": 0},
        ]
        cart = {"items": items, "subtotal": 0, "address": address}
    else:
        items = [
            {"nome": "Dipirona 500mg", "quantidade": 2, "preco": 7.50},
            {"nome": "Tylenol",        "quantidade": 1, "preco": 18.90},
        ]
        cart = {"items": items, "subtotal": 33.90,
                "payment": "PIX", "address": address}
    return build_summary_text(cart, cfg) or ""


@order_summary_router.put("/config", response_model=OrderSummaryConfigOut)
async def update_order_summary_config(
    payload: OrderSummaryConfigIn, user: TenantUser,
) -> OrderSummaryConfigOut:
    """Salva os campos do template. Campo None = remove o override (volta ao
    default do catálogo). String vazia em header/footer é VÁLIDA — quem quer
    omitir literalmente."""
    user.assert_role("manager")
    from services import capabilities as cap_svc

    # Sanity: tenta renderizar com sample antes de salvar, evita template
    # quebrado em prod (igual ao /template do abandoned_cart).
    defaults = await _get_order_summary_defaults()
    candidate = {
        k: (getattr(payload, k) if getattr(payload, k) is not None else defaults[k])
        for k in ORDER_SUMMARY_FIELDS
    }
    try:
        _render_summary_preview(candidate, no_prices=False)
        _render_summary_preview(candidate, no_prices=True)
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Template inválido: {e}")

    current = await cap_svc.list_for_tenant(user.tenant_id)
    cap = next((c for c in current if c["key"] == ORDER_SUMMARY_KEY), None)
    if not cap:
        raise HTTPException(status_code=404, detail="Capacidade não encontrada.")

    new_config = dict(cap.get("config") or {})
    # Para cada campo: valor != default → grava override; igual ao default →
    # remove a chave do override (mantém a config esparsa, como em SPEC 04).
    for key in ORDER_SUMMARY_FIELDS:
        value = getattr(payload, key)
        if value is None:
            new_config.pop(key, None)
        elif value == defaults.get(key):
            new_config.pop(key, None)
        else:
            new_config[key] = value

    await cap_svc.set_enabled(
        tenant_id=str(user.tenant_id),
        key=ORDER_SUMMARY_KEY,
        enabled=bool(cap.get("enabled")),
        config=new_config,
        user_id=user.email,
    )
    await log_event(
        action="order_summary.template_updated", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target=ORDER_SUMMARY_KEY,
        meta={"reset_to_default": all(v is None for v in payload.dict().values())},
    )
    return await get_order_summary_config(user)


@order_summary_router.post("/preview", response_model=OrderSummaryPreviewOut)
async def preview_order_summary(
    payload: OrderSummaryPreviewIn, user: TenantUser,
) -> OrderSummaryPreviewOut:
    """Renderiza preview combinando os campos do payload com o salvo.

    Modos:
      • session_key fornecido → usa cart real desse cliente (preço se houver).
      • Senão → cart sample. `no_prices=True` mostra como fica em pré-atendimento.
    """
    from services import capabilities as cap_svc

    defaults = await _get_order_summary_defaults()
    saved = await cap_svc.get_config(user.tenant_id, ORDER_SUMMARY_KEY) or {}
    merged = _merge_summary_cfg(saved, defaults)
    # Sobrescreve com o que veio no payload (campos None mantêm o salvo)
    for k in ORDER_SUMMARY_FIELDS:
        v = getattr(payload, k)
        if v is not None:
            merged[k] = v

    if not payload.session_key:
        no_prices = bool(payload.no_prices) if payload.no_prices is not None else False
        rendered = _render_summary_preview(merged, no_prices=no_prices)
        return OrderSummaryPreviewOut(rendered=rendered, used_sample=True)

    # Preview com cart real
    import json as _json
    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        await conn.execute(f"SET search_path = {schema_row['schema_name']}, public")
        row = await conn.fetchrow(
            "SELECT items, subtotal FROM cart WHERE session_key = $1",
            payload.session_key,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Carrinho não encontrado.")

    raw = row["items"]
    if isinstance(raw, str):
        try: items_raw = _json.loads(raw)
        except _json.JSONDecodeError: items_raw = []
    else:
        items_raw = list(raw or [])

    # Normaliza chaves EN→PT antes de mandar ao build_summary_text
    items = []
    for it in items_raw:
        if not isinstance(it, dict):
            continue
        items.append({
            "nome":       it.get("nome")       or it.get("name")  or "",
            "quantidade": it.get("quantidade") or it.get("qty")   or 1,
            "preco":      it.get("preco")      or it.get("price") or 0,
        })

    from services.order_summary import build_summary_text
    rendered = build_summary_text(
        {"items": items, "subtotal": float(row["subtotal"] or 0)},
        merged,
    ) or ""
    return OrderSummaryPreviewOut(rendered=rendered, used_sample=False)


# ── Disparo manual em lote ──────────────────────────────────────────────────

class TriggerAllOut(BaseModel):
    checked:        int   # carrinhos elegíveis encontrados
    sent:           int   # disparos OK
    skipped_no_phone: int # session_key sem telefone parseável
    errors:         int   # falhas de envio


# Disparo manual é assíncrono: o endpoint enfileira um Celery task e
# devolve 202 com o batch_id. O frontend polla /batches/{id} pra mostrar
# progresso. Permite cancelar e desfazer (reverter marcador de envio).

class TriggerIn(BaseModel):
    # Lista opcional de session_keys. Vazio/None → todos os carrinhos com itens.
    session_keys: list[str] | None = None


class TriggerOut(BaseModel):
    batch_id: str
    total:    int


class BatchOut(BaseModel):
    id:              str
    status:          str
    total:           int
    sent:            int
    failed:          int
    skipped:         int
    actor_email:     str | None
    created_at:      datetime
    started_at:      datetime | None
    finished_at:     datetime | None
    cancel_requested: bool
    error:           str | None


def _row_to_batch(r) -> BatchOut:
    return BatchOut(
        id=str(r["id"]), status=r["status"],
        total=int(r["total"]), sent=int(r["sent"]),
        failed=int(r["failed"]), skipped=int(r["skipped"]),
        actor_email=r["actor_email"],
        created_at=r["created_at"], started_at=r["started_at"],
        finished_at=r["finished_at"],
        cancel_requested=bool(r["cancel_requested"]),
        error=r["error"],
    )


@recovery_router.post("/trigger", response_model=TriggerOut, status_code=202)
async def trigger_recovery(payload: TriggerIn, user: TenantUser) -> TriggerOut:
    """Enfileira um batch de envio. Não bloqueia: o Celery worker processa
    em background com rate-limit, e o frontend mostra progresso.

    Se `session_keys` vier vazio, seleciona TODOS os carrinhos do tenant que
    têm pelo menos 1 item (independente de status).
    """
    user.assert_role("manager")

    import asyncpg
    import json as _json

    async with get_db_conn() as conn:
        schema_row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1",
            user.tenant_id,
        )
        if not schema_row:
            raise HTTPException(status_code=404, detail="Farmácia não encontrada.")
        schema = schema_row["schema_name"]

        # Recusa se já houver batch em andamento — evita duplicar envio.
        active = await conn.fetchrow(
            """
            SELECT id FROM public.recovery_batches
             WHERE tenant_id = $1 AND status IN ('queued','running')
             LIMIT 1
            """,
            user.tenant_id,
        )
        if active:
            raise HTTPException(
                status_code=409,
                detail="Já existe um disparo em andamento. Aguarde ou cancele antes de iniciar outro.",
            )

        # Resolve session_keys: filtragem feita no DB pra garantir que só
        # carrinhos com itens entrem (defesa contra payload manual sujo).
        await conn.execute(f"SET search_path = {schema}, public")
        try:
            if payload.session_keys:
                rows = await conn.fetch(
                    """
                    SELECT session_key FROM cart
                     WHERE session_key = ANY($1::text[])
                       AND (CASE WHEN jsonb_typeof(COALESCE(items, '[]'::jsonb)) = 'array'
                                 THEN jsonb_array_length(COALESCE(items, '[]'::jsonb))
                                 ELSE 0
                            END) > 0
                    """,
                    payload.session_keys,
                )
            else:
                rows = await conn.fetch(
                    """
                    SELECT session_key FROM cart
                     WHERE (CASE WHEN jsonb_typeof(COALESCE(items, '[]'::jsonb)) = 'array'
                                 THEN jsonb_array_length(COALESCE(items, '[]'::jsonb))
                                 ELSE 0
                            END) > 0
                     ORDER BY updated_at DESC
                    """
                )
        except asyncpg.PostgresError as e:
            log.warning("recovery.trigger.query_failed",
                        tenant_id=str(user.tenant_id), error=str(e))
            raise HTTPException(
                status_code=500,
                detail="Não foi possível buscar carrinhos.",
            )

        keys = [r["session_key"] for r in rows]
        if not keys:
            raise HTTPException(
                status_code=400,
                detail="Nenhum carrinho elegível para disparo.",
            )

        # Cria o batch (volta pro search_path padrão pra escrever em public).
        await conn.execute("SET search_path = public")
        batch_row = await conn.fetchrow(
            """
            INSERT INTO public.recovery_batches
                   (tenant_id, schema_name, actor_email, total, session_keys)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id
            """,
            # Codec do asyncpg (db/postgres.py) já serializa jsonb — passar a
            # lista crua. Ver [[jsonb-double-encoding]].
            user.tenant_id, schema, user.email, len(keys),
            keys,
        )
        batch_id = str(batch_row["id"])

    # Enfileira o task (lazy import pra não criar ciclo no startup do API).
    from workers.celery_app import process_recovery_batch_task
    process_recovery_batch_task.delay(batch_id)

    await log_event(
        action="recovery.trigger_enqueued", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="cart_recovery",
        meta={"batch_id": batch_id, "total": len(keys),
              "scope": "selected" if payload.session_keys else "all"},
    )
    return TriggerOut(batch_id=batch_id, total=len(keys))


@recovery_router.get("/batches", response_model=list[BatchOut])
async def list_batches(user: TenantUser) -> list[BatchOut]:
    """Últimos 20 batches do tenant — para mostrar histórico recente no portal."""
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, status, total, sent, failed, skipped, actor_email,
                   created_at, started_at, finished_at, cancel_requested, error
              FROM public.recovery_batches
             WHERE tenant_id = $1
             ORDER BY created_at DESC
             LIMIT 20
            """,
            user.tenant_id,
        )
    return [_row_to_batch(r) for r in rows]


@recovery_router.get("/batches/{batch_id}", response_model=BatchOut)
async def get_batch(batch_id: str, user: TenantUser) -> BatchOut:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, status, total, sent, failed, skipped, actor_email,
                   created_at, started_at, finished_at, cancel_requested, error
              FROM public.recovery_batches
             WHERE id = $1 AND tenant_id = $2
            """,
            batch_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Batch não encontrado.")
    return _row_to_batch(row)


@recovery_router.post("/batches/{batch_id}/cancel", response_model=BatchOut)
async def cancel_batch(batch_id: str, user: TenantUser) -> BatchOut:
    """Marca `cancel_requested = TRUE`. O worker checa antes do próximo envio
    e encerra o batch. Mensagens já entregues NÃO são desfeitas — o undo é
    quem reverte o marcador no carrinho.
    """
    user.assert_role("manager")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.recovery_batches
               SET cancel_requested = TRUE
             WHERE id = $1 AND tenant_id = $2 AND status IN ('queued','running')
            RETURNING id, status, total, sent, failed, skipped, actor_email,
                      created_at, started_at, finished_at, cancel_requested, error
            """,
            batch_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Batch não está em execução (já terminou ou não existe).",
        )
    await log_event(
        action="recovery.batch_cancel_requested", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="cart_recovery", meta={"batch_id": batch_id},
    )
    return _row_to_batch(row)


@recovery_router.post("/batches/{batch_id}/dismiss", response_model=BatchOut)
async def dismiss_batch(batch_id: str, user: TenantUser) -> BatchOut:
    """Força encerramento de um batch travado em `queued`/`running`.

    Diferente de cancel (que SINALIZA pro worker via cancel_requested), dismiss
    marca direto como `failed` no DB. Usar quando o worker morreu antes de
    processar (crash sem mark-failed, deploy interrompido, etc.) e o batch
    está bloqueando novos disparos.

    NÃO desfaz envios já marcados em sent_session_keys — pra reverter o
    marcador no cart, usar `/undo` depois.
    """
    user.assert_role("manager")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.recovery_batches
               SET status      = 'failed',
                   finished_at = NOW(),
                   error       = COALESCE(error,
                                          'Descartado manualmente — worker pode estar travado.')
             WHERE id = $1 AND tenant_id = $2 AND status IN ('queued','running')
            RETURNING id, status, total, sent, failed, skipped, actor_email,
                      created_at, started_at, finished_at, cancel_requested, error
            """,
            batch_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Batch não está em execução (já terminou ou não existe).",
        )
    await log_event(
        action="recovery.batch_dismissed", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="cart_recovery", meta={"batch_id": batch_id},
    )
    return _row_to_batch(row)


@recovery_router.post("/batches/{batch_id}/undo", response_model=BatchOut)
async def undo_batch(batch_id: str, user: TenantUser) -> BatchOut:
    """Reverte o marcador `sent_recovery_at`/`recovery_attempts` nos
    carrinhos que receberam mensagem neste batch — pra que voltem a ser
    elegíveis pelo job automático.

    NÃO desentrega mensagens já enviadas (impossível). Só limpa o estado
    interno. Usar quando o operador disparou em lote por engano ou quer
    permitir nova tentativa controlada.
    """
    user.assert_role("manager")
    import json as _json
    import asyncpg

    async with get_db_conn() as conn:
        batch = await conn.fetchrow(
            """
            SELECT id, schema_name, status, sent, sent_session_keys
              FROM public.recovery_batches
             WHERE id = $1 AND tenant_id = $2
            """,
            batch_id, user.tenant_id,
        )
        if not batch:
            raise HTTPException(status_code=404, detail="Batch não encontrado.")
        if batch["status"] not in ("completed", "cancelled"):
            raise HTTPException(
                status_code=409,
                detail="Só é possível desfazer um disparo que já terminou.",
            )

        raw = batch["sent_session_keys"]
        if isinstance(raw, str):
            try: sent_keys = _json.loads(raw)
            except _json.JSONDecodeError: sent_keys = []
        else:
            sent_keys = list(raw or [])

        if sent_keys:
            schema = batch["schema_name"]
            await conn.execute(f"SET search_path = {schema}, public")
            try:
                await conn.execute(
                    """
                    UPDATE cart
                       SET sent_recovery_at  = NULL,
                           recovery_attempts = GREATEST(COALESCE(recovery_attempts, 0) - 1, 0)
                     WHERE session_key = ANY($1::text[])
                    """,
                    sent_keys,
                )
            except asyncpg.PostgresError as e:
                log.warning("recovery.undo.update_failed",
                            tenant_id=str(user.tenant_id),
                            batch_id=batch_id, error=str(e))
                raise HTTPException(
                    status_code=500,
                    detail="Falha ao reverter marcador nos carrinhos.",
                )

        await conn.execute("SET search_path = public")
        row = await conn.fetchrow(
            """
            UPDATE public.recovery_batches
               SET status = 'undone', finished_at = COALESCE(finished_at, NOW())
             WHERE id = $1
            RETURNING id, status, total, sent, failed, skipped, actor_email,
                      created_at, started_at, finished_at, cancel_requested, error
            """,
            batch_id,
        )

    await log_event(
        action="recovery.batch_undone", actor_id=user.email,
        actor_type="user", tenant_id=user.tenant_id,
        target="cart_recovery",
        meta={"batch_id": batch_id, "reverted_carts": len(sent_keys)},
    )
    return _row_to_batch(row)
