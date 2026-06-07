"""
Webhook broker router.

Public ingest:
  POST /hooks/{tenant_token}/{integration_slug}
    - Accepts ANY JSON body from any source system.
    - Persists raw payload, picks a matching mapping, builds canonical event,
      dispatches outbound targets. Always returns 200 fast.

Portal (tenant manager+):
  GET    /portal/broker/integrations
  POST   /portal/broker/integrations
  PATCH  /portal/broker/integrations/{id}
  DELETE /portal/broker/integrations/{id}

  GET    /portal/broker/integrations/{id}/mappings
  POST   /portal/broker/integrations/{id}/mappings
  PATCH  /portal/broker/mappings/{mapping_id}
  DELETE /portal/broker/mappings/{mapping_id}

  GET    /portal/broker/integrations/{id}/outbound
  POST   /portal/broker/integrations/{id}/outbound
  PATCH  /portal/broker/outbound/{target_id}
  DELETE /portal/broker/outbound/{target_id}

  POST   /portal/broker/preview         — run a mapping against sample payload
  POST   /portal/broker/discover        — extract a list of paths from a sample
  GET    /portal/broker/raw-events      — recent events log
  POST   /portal/broker/raw-events/{id}/replay
"""
from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import Response
from pydantic import BaseModel, Field

from config import settings
from db.postgres import get_db_conn
from security import require_tenant_user, TenantUserContext
from services import broker
from services.audit import log_event

log = structlog.get_logger()


# ── Schemas ──────────────────────────────────────────────────────────────────

class IntegrationIn(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9_\-]+$")
    name: str = Field(min_length=2, max_length=120)
    direction: str = Field(default="inbound", pattern=r"^(inbound|outbound|both)$")
    hmac_secret: str | None = None
    hmac_header: str | None = None
    hmac_algorithm: str | None = "sha256"
    enabled: bool = True
    config_json: dict[str, Any] = Field(default_factory=dict)


class IntegrationPatch(BaseModel):
    name: str | None = None
    direction: str | None = None
    hmac_secret: str | None = None
    hmac_header: str | None = None
    hmac_algorithm: str | None = None
    enabled: bool | None = None
    config_json: dict[str, Any] | None = None


class IntegrationOut(BaseModel):
    id: str
    slug: str
    name: str
    direction: str
    hmac_header: str | None
    hmac_algorithm: str | None
    has_secret: bool
    enabled: bool
    inbound_url: str
    config_json: dict = Field(default_factory=dict)
    # Simplified flow config
    inbound_field_map: dict = Field(default_factory=dict)
    reply_mode: str = "response"
    reply_url: str | None = None
    reply_method: str = "POST"
    reply_headers: dict = Field(default_factory=dict)
    reply_body_template: dict = Field(default_factory=dict)
    reply_status_code: int = 200
    bundle_enabled: bool = False
    bundle_window_seconds: int = 10
    skip_rules: list[dict] = Field(default_factory=list)
    handoff_config: dict = Field(default_factory=dict)
    session_config: dict = Field(default_factory=dict)
    handoff_pause_minutes: int = 240
    human_handoff_detection: dict = Field(default_factory=dict)


class FlowConfigIn(BaseModel):
    """Body for PUT /portal/broker/integrations/{id}/flow — saves the whole flow at once."""
    inbound_field_map: dict[str, Any] = Field(default_factory=dict)
    reply_mode: str = Field(default="response", pattern=r"^(response|forward)$")
    reply_url: str | None = None
    reply_method: str = Field(default="POST", pattern=r"^(POST|PUT|PATCH)$")
    reply_headers: dict[str, str] = Field(default_factory=dict)
    reply_body_template: dict[str, Any] = Field(default_factory=dict)
    reply_status_code: int = Field(default=200, ge=100, le=599)
    bundle_enabled: bool = False
    bundle_window_seconds: int = Field(default=10, ge=2, le=120)
    skip_rules: list[dict[str, Any]] = Field(default_factory=list)
    handoff_config: dict[str, Any] = Field(default_factory=dict)
    session_config: dict[str, Any] = Field(default_factory=dict)
    handoff_pause_minutes: int = Field(default=240, ge=0, le=10080)
    human_handoff_detection: dict[str, Any] = Field(default_factory=dict)


class MappingIn(BaseModel):
    canonical_event: str = Field(min_length=1, max_length=120)
    match_rules: dict[str, Any] = Field(default_factory=dict)
    field_map: dict[str, Any] = Field(default_factory=dict)
    direction: str = Field(default="inbound", pattern=r"^(inbound|outbound)$")
    enabled: bool = True


class MappingPatch(BaseModel):
    canonical_event: str | None = None
    match_rules: dict[str, Any] | None = None
    field_map: dict[str, Any] | None = None
    enabled: bool | None = None


class MappingOut(BaseModel):
    id: str
    integration_id: str
    canonical_event: str
    match_rules: dict
    field_map: dict
    direction: str
    enabled: bool
    version: int


class OutboundIn(BaseModel):
    canonical_event: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=8)
    method: str = Field(default="POST", pattern=r"^(POST|PUT|PATCH)$")
    headers: dict[str, str] = Field(default_factory=dict)
    field_map: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class OutboundPatch(BaseModel):
    canonical_event: str | None = None
    url: str | None = None
    method: str | None = None
    headers: dict[str, str] | None = None
    field_map: dict[str, Any] | None = None
    enabled: bool | None = None


class OutboundOut(BaseModel):
    id: str
    canonical_event: str
    url: str
    method: str
    headers: dict
    field_map: dict
    enabled: bool


class PreviewIn(BaseModel):
    payload: Any
    match_rules: dict[str, Any] = Field(default_factory=dict)
    field_map: dict[str, Any] = Field(default_factory=dict)


class PreviewOut(BaseModel):
    matched: bool
    result: dict


class DiscoverIn(BaseModel):
    payload: Any


class RawEventOut(BaseModel):
    id: str
    integration_slug: str
    direction: str
    status: str
    canonical_event: str | None
    error: str | None
    attempts: int
    created_at: str
    idempotency_key: str | None = None
    payload_preview: str | None = None  # primeiros 200 chars do payload
    forward_status_code: int | None = None  # status HTTP do POST pra reply_url (forward mode)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _build_ingest_url(request: Request, tenant_api_key: str, slug: str) -> str:
    # Prefer the configured PUBLIC_API_URL (always correct in dev/prod).
    # Fall back to request.base_url which works only for direct access (no proxy).
    base = (settings.public_api_url or str(request.base_url)).rstrip("/")
    return f"{base}/hooks/{tenant_api_key}/{slug}"


async def _own_integration(integration_id: str, tenant_id: str) -> dict:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.tenant_integrations WHERE id = $1 AND tenant_id = $2",
            integration_id, tenant_id,
        )
    if not row:
        raise HTTPException(404, "Integração não encontrada")
    return dict(row)


async def _own_mapping(mapping_id: str, tenant_id: str) -> dict:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT m.*
            FROM public.integration_mappings m
            JOIN public.tenant_integrations i ON i.id = m.integration_id
            WHERE m.id = $1 AND i.tenant_id = $2
            """,
            mapping_id, tenant_id,
        )
    if not row:
        raise HTTPException(404, "Mapeamento não encontrado")
    return dict(row)


async def _own_outbound(target_id: str, tenant_id: str) -> dict:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT o.*
            FROM public.broker_outbound_targets o
            JOIN public.tenant_integrations i ON i.id = o.integration_id
            WHERE o.id = $1 AND i.tenant_id = $2
            """,
            target_id, tenant_id,
        )
    if not row:
        raise HTTPException(404, "Destino não encontrado")
    return dict(row)


def _as_jsonb(value: Any, *, default: Any = None):
    """Coerce um valor de coluna jsonb pra dict/list nativo.

    Blindagem contra double-encoding legado (ver [[jsonb-double-encoding]]): se a
    coluna foi gravada como JSON string ("{...}"), o codec do asyncpg devolve uma
    `str` Python em vez de dict/list. Aqui desempacotamos (json.loads) até no
    máximo 2 níveis. Nunca levanta — em qualquer falha cai no `default`.
    """
    if default is None:
        default = {}
    if value is None:
        return default
    seen = 0
    while isinstance(value, str) and seen < 2:
        try:
            value = json.loads(value)
        except Exception:
            return default
        seen += 1
    if isinstance(value, (dict, list)):
        return value
    return default


def _integration_out(row: dict, ingest_url: str) -> IntegrationOut:
    return IntegrationOut(
        id=str(row["id"]),
        slug=row["slug"],
        name=row["name"],
        direction=row["direction"],
        hmac_header=row.get("hmac_header"),
        hmac_algorithm=row.get("hmac_algorithm"),
        has_secret=bool(row.get("hmac_secret")),
        enabled=row["enabled"],
        inbound_url=ingest_url,
        inbound_field_map=_as_jsonb(row.get("inbound_field_map")),
        reply_mode=row.get("reply_mode") or "response",
        reply_url=row.get("reply_url"),
        reply_method=row.get("reply_method") or "POST",
        reply_headers=_as_jsonb(row.get("reply_headers")),
        reply_body_template=_as_jsonb(row.get("reply_body_template")),
        reply_status_code=row.get("reply_status_code") or 200,
        bundle_enabled=bool(row.get("bundle_enabled")),
        bundle_window_seconds=row.get("bundle_window_seconds") or 10,
        skip_rules=_as_jsonb(row.get("skip_rules"), default=[]),
        handoff_config=_as_jsonb(row.get("handoff_config")),
        session_config=_as_jsonb(row.get("session_config")),
        handoff_pause_minutes=row.get("handoff_pause_minutes") or 240,
        human_handoff_detection=_as_jsonb(row.get("human_handoff_detection")),
        config_json=_as_jsonb(row.get("config_json")),
    )


# ── Ingest (public) ──────────────────────────────────────────────────────────

ingest_router = APIRouter(prefix="/hooks", tags=["broker-ingest"])


@ingest_router.get("/{tenant_token}/{integration_slug}/ping")
async def ingest_ping(tenant_token: str, integration_slug: str):
    """Diagnóstico: confirma que a rota existe e a integração está configurada."""
    async with get_db_conn() as conn:
        tenant = await conn.fetchrow(
            "SELECT id FROM public.tenants WHERE api_key = $1 AND active = TRUE",
            tenant_token,
        )
        if not tenant:
            return {"ok": False, "error": "Token de tenant inválido ou inativo"}
        integration = await conn.fetchrow(
            "SELECT slug, enabled FROM public.tenant_integrations "
            "WHERE tenant_id = $1 AND slug = $2",
            tenant["id"], integration_slug,
        )
        if not integration:
            return {"ok": False, "error": f"Integração '{integration_slug}' não existe nesse tenant"}
        if not integration["enabled"]:
            return {"ok": False, "error": "Integração existe mas está desativada"}
    return {"ok": True, "message": "Tudo certo — pode enviar POST nessa URL"}


@ingest_router.post("/{tenant_token}/{integration_slug}", status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    tenant_token: str,
    integration_slug: str,
    request: Request,
):
    log.info("hooks.received",
             slug=integration_slug,
             token_prefix=tenant_token[:8],
             content_type=request.headers.get("content-type"),
             content_length=request.headers.get("content-length"))

    raw_body = await request.body()

    # Parse body — try JSON first, then form-encoded, then keep raw as string
    payload: Any
    content_type = (request.headers.get("content-type") or "").lower()
    if "application/json" in content_type or raw_body.startswith(b"{") or raw_body.startswith(b"["):
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception as exc:
            log.warning("hooks.json_parse_failed", error=str(exc))
            payload = {"_raw": raw_body.decode("utf-8", errors="replace")}
    elif "form" in content_type:
        from urllib.parse import parse_qs
        parsed = parse_qs(raw_body.decode("utf-8", errors="replace"))
        payload = {k: (v[0] if len(v) == 1 else v) for k, v in parsed.items()}
    else:
        try:
            payload = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            payload = {"_raw": raw_body.decode("utf-8", errors="replace")}

    async with get_db_conn() as conn:
        tenant = await conn.fetchrow(
            "SELECT id, schema_name FROM public.tenants WHERE api_key = $1 AND active = TRUE",
            tenant_token,
        )
        if not tenant:
            log.warning("hooks.tenant_not_found", token_prefix=tenant_token[:8])
            raise HTTPException(401, "Token inválido")

        integration = await conn.fetchrow(
            """
            SELECT * FROM public.tenant_integrations
            WHERE tenant_id = $1 AND slug = $2 AND enabled = TRUE
            """,
            tenant["id"], integration_slug,
        )
        if not integration:
            log.warning("hooks.integration_not_found",
                        tenant=str(tenant["id"]), slug=integration_slug)
            raise HTTPException(404, "Integração não configurada")

        # ── Skip rules: ignora payloads que casarem com qualquer regra ───────
        # Crítico pra evitar loop bot ↔ gateway (ex: Z-API ecoa nossas msgs)
        skip_rules = integration.get("skip_rules") or []
        for rule in skip_rules:
            path = rule.get("path")
            expected = rule.get("equals")
            if not path:
                continue
            actual = broker.resolve_path(payload, path)
            # Comparação case-sensitive como string (mesma semântica do match_rules)
            if str(actual) == str(expected):
                log.info("hooks.skipped_by_rule",
                         tenant=str(tenant["id"]), slug=integration_slug,
                         rule_path=path, rule_value=str(expected)[:80])
                # Persiste o evento como skipped pra ficar visível na UI
                idem = broker.idempotency_hash(payload)
                await conn.execute(
                    """
                    INSERT INTO public.broker_raw_events
                      (tenant_id, integration_id, integration_slug, direction,
                       payload, headers, idempotency_key, status, error,
                       canonical_event, processed_at)
                    VALUES ($1,$2,$3,'inbound',$4,$5,$6,'skipped',$7,'skipped.by_rule',NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    tenant["id"], integration["id"], integration_slug,
                    payload, {}, f"skip:{idem}",
                    f"Ignorado por regra: {path} == {expected}",
                )
                return {
                    "accepted": True,
                    "skipped": True,
                    "reason": rule.get("comment") or f"Ignorado: {path} == {expected}",
                }

        # ── Detecção de resposta HUMANA → pausa a IA ─────────────────────────
        # Gateways que ecoam mensagens de SAÍDA (TalkFarma/ClickMassa/WAHA/...)
        # devolvem tanto as respostas do BOT quanto as do ATENDENTE pelo mesmo
        # número. Quando o atendente humano responde, a IA deve calar a boca
        # (janela rolante). Distinção bot×humano via fingerprint efêmero
        # (services.bot_echo) — o cerne do "dilema do auto-eco". Ver SPEC 05.
        hhd = integration.get("human_handoff_detection") or {}
        if isinstance(hhd, str):
            try:
                hhd = json.loads(hhd) or {}
            except Exception:
                hhd = {}
        om = hhd.get("outbound_match") or {}
        om_path = om.get("path")
        if hhd.get("enabled") and om_path and \
                str(broker.resolve_path(payload, om_path)) == str(om.get("equals")):
            # É mensagem de SAÍDA (do bot OU do atendente humano).
            idem = broker.idempotency_hash(payload)
            out_key = f"out:{idem}"
            # Dedup: se já processamos este eco, NÃO reavaliar (is_echo consome o
            # fingerprint; um retry do gateway leria o eco do bot como "humano").
            existing_out = await conn.fetchrow(
                "SELECT id FROM public.broker_raw_events "
                "WHERE tenant_id=$1 AND integration_slug=$2 AND idempotency_key=$3",
                tenant["id"], integration_slug, out_key,
            )
            if existing_out:
                return {"accepted": True, "duplicate": True,
                        "reason": "Evento de saída já processado"}

            cust_path = hhd.get("customer_phone_path")
            cust_raw = broker.resolve_path(payload, cust_path) if cust_path else None
            customer_phone = "".join(c for c in str(cust_raw or "") if c.isdigit())[:20]
            # Texto da msg: reaproveita o inbound_field_map do tenant (o corpo
            # costuma estar no mesmo caminho em inbound e outbound).
            try:
                canon = broker.apply_mapping(integration.get("inbound_field_map") or {}, payload)
                out_text = (canon.get("message") or "") if isinstance(canon, dict) else ""
            except Exception:
                out_text = ""

            if not customer_phone:
                log.warning("hooks.outbound.no_customer_phone",
                            tenant=str(tenant["id"]), slug=integration_slug,
                            cust_path=cust_path)
                await conn.execute(
                    """
                    INSERT INTO public.broker_raw_events
                      (tenant_id, integration_id, integration_slug, direction,
                       payload, headers, idempotency_key, status, error,
                       canonical_event, processed_at)
                    VALUES ($1,$2,$3,'outbound',$4,$5,$6,'skipped',$7,'outbound.no_customer_phone',NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    tenant["id"], integration["id"], integration_slug,
                    payload, {}, out_key, "Saída sem telefone do cliente",
                )
                return {"accepted": True, "skipped": True,
                        "reason": "Mensagem de saída sem telefone do cliente"}

            from services import bot_echo
            if await bot_echo.is_echo(str(tenant["id"]), customer_phone, out_text):
                # Eco da própria mensagem do bot → IA segue ativa.
                log.info("hooks.outbound.bot_echo",
                         tenant=str(tenant["id"]), slug=integration_slug,
                         phone=customer_phone[:4])
                await conn.execute(
                    """
                    INSERT INTO public.broker_raw_events
                      (tenant_id, integration_id, integration_slug, direction,
                       payload, headers, idempotency_key, status, error,
                       canonical_event, processed_at)
                    VALUES ($1,$2,$3,'outbound',$4,$5,$6,'skipped',$7,'outbound.bot_echo',NOW())
                    ON CONFLICT DO NOTHING
                    """,
                    tenant["id"], integration["id"], integration_slug,
                    payload, {}, out_key, "Eco da mensagem do próprio bot",
                )
                return {"accepted": True, "skipped": True,
                        "reason": "Eco da mensagem do próprio bot (IA segue ativa)"}

            # Atendente HUMANO respondeu → pausa a IA por handoff_pause_minutes.
            # Cada msg do humano renova a janela (pause() faz UPSERT de paused_until).
            try:
                from services import conversation_state as cs
                pause_min = int(integration["handoff_pause_minutes"] or 240)
                await cs.pause(
                    str(tenant["id"]), customer_phone,
                    until_minutes=pause_min,
                    by="auto:human_reply",
                    reason="atendente humano respondeu",
                )
                log.info("broker.human_reply.ai_paused",
                         tenant=str(tenant["id"]), slug=integration_slug,
                         phone=customer_phone[:4], pause_minutes=pause_min)
            except Exception as exc:  # noqa: BLE001
                log.warning("broker.human_reply.pause_failed",
                            tenant=str(tenant["id"]), exc=str(exc))
            await conn.execute(
                """
                INSERT INTO public.broker_raw_events
                  (tenant_id, integration_id, integration_slug, direction,
                   payload, headers, idempotency_key, status, error,
                   canonical_event, processed_at)
                VALUES ($1,$2,$3,'outbound',$4,$5,$6,'processed',NULL,'human_reply.ai_paused',NOW())
                ON CONFLICT DO NOTHING
                """,
                tenant["id"], integration["id"], integration_slug,
                payload, {}, out_key,
            )
            return {"accepted": True, "paused_ai": True,
                    "reason": "Atendente humano respondeu — IA pausada"}

        log.info("hooks.persisting", tenant=str(tenant["id"]), slug=integration_slug)

        # HMAC verification (opt-in)
        if integration["hmac_secret"] and integration["hmac_header"]:
            signature = request.headers.get(integration["hmac_header"])
            ok = broker.verify_hmac(
                integration["hmac_secret"],
                integration["hmac_algorithm"] or "sha256",
                signature,
                raw_body,
            )
            if not ok:
                raise HTTPException(401, "Assinatura HMAC inválida")

        # Idempotency: caller-supplied key takes precedence; otherwise hash the
        # payload AND a 60s time bucket so retries within the same minute are
        # deduplicated but identical user replies ("ok", "sim") sent legitimately
        # later are NOT treated as duplicates.
        explicit_idem = request.headers.get("x-idempotency-key")
        if explicit_idem:
            idem = explicit_idem
        else:
            import time as _time
            bucket = int(_time.time() // 60)  # changes every 60s
            idem = f"{bucket}:{broker.idempotency_hash(payload)}"

        headers_to_keep = {k: v for k, v in request.headers.items()
                           if k.lower() in {"user-agent", "content-type", "x-event-type",
                                            "x-idempotency-key"}}

        # Insert raw event (idempotent within the 60s bucket)
        existing = await conn.fetchrow(
            """
            SELECT id, status, created_at, payload FROM public.broker_raw_events
            WHERE tenant_id = $1 AND integration_slug = $2 AND idempotency_key = $3
            """,
            tenant["id"], integration_slug, idem,
        )
        if existing:
            # Log detalhado pra diagnosticar deduplicações inesperadas
            log.warning(
                "hooks.duplicate_detected",
                slug=integration_slug,
                idem_key=idem,
                explicit_header=bool(explicit_idem),
                original_event_id=str(existing["id"]),
                original_created_at=existing["created_at"].isoformat(),
                new_payload_preview=json.dumps(payload, ensure_ascii=False)[:300],
                original_payload_preview=json.dumps(existing["payload"], ensure_ascii=False)[:300],
                payloads_identical=(payload == existing["payload"]),
            )
            return {
                "accepted": True,
                "duplicate": True,
                "event_id": str(existing["id"]),
                "original_received_at": existing["created_at"].isoformat(),
                "idempotency_key": idem,
                "reason": (
                    "Payload idêntico recebido nos últimos 60s. "
                    "Se o sistema externo está mandando mensagens diferentes mas caindo aqui, "
                    "verifique se o payload tem algum campo único (timestamp, message_id)."
                ),
            }

        event_row = await conn.fetchrow(
            """
            INSERT INTO public.broker_raw_events
              (tenant_id, integration_id, integration_slug, direction,
               payload, headers, idempotency_key, status)
            VALUES ($1, $2, $3, 'inbound', $4, $5, $6, 'pending')
            RETURNING id
            """,
            tenant["id"], integration["id"], integration_slug,
            payload, headers_to_keep, idem,
        )
        event_id = event_row["id"]

    # ── New simplified flow (preferred) ───────────────────────────────────
    # If the integration has inbound_field_map configured, use the per-
    # integration flow instead of the multi-mapping system.
    inbound_map = integration.get("inbound_field_map") or {}
    if inbound_map:
        try:
            canonical_input = broker.apply_mapping(inbound_map, payload)
            # Auto-injeta media_* se o payload bruto contém áudio/imagem
            # e o mapping ainda não cobriu — funciona out-of-the-box para
            # Z-API, WhatsApp Cloud, WAHA e variantes comuns.
            from services.media_detect import enrich_canonical_with_media
            enrich_canonical_with_media(canonical_input, payload)
            if canonical_input.get("media_type"):
                log.info("broker.media_detected",
                         media_type=canonical_input["media_type"],
                         has_url=bool(canonical_input.get("media_url")),
                         has_id=bool(canonical_input.get("media_id")),
                         event_id=str(event_id))
            else:
                log.info("broker.no_media_in_payload",
                         payload_keys=list(payload.keys())
                                      if isinstance(payload, dict) else None,
                         event_id=str(event_id))
        except Exception as exc:
            async with get_db_conn() as conn:
                await conn.execute(
                    "UPDATE public.broker_raw_events SET status='failed', "
                    "error=$2, attempts=attempts+1, processed_at=NOW() WHERE id=$1",
                    event_id, f"input transform error: {exc}",
                )
            raise HTTPException(500, f"Erro extraindo campos de entrada: {exc}")

        reply_mode = integration.get("reply_mode") or "response"

        # ── Async (forward) mode: dispatch to Celery, return 202 immediately ─
        # The worker will run the agent graph and POST the reply to reply_url.
        if reply_mode == "forward" and integration.get("reply_url"):
            # ── Bundling (debounce): agrupa mensagens picadas ────────────────
            # Cada mensagem entra num buffer Redis e agenda uma task com
            # countdown. A task verifica se chegou mensagem nova depois dela;
            # se sim, desiste — outra task mais recente vai processar o bundle.
            if integration.get("bundle_enabled"):
                from db.redis_client import get_redis
                from workers.celery_app import process_bundled_message
                import time as _time, json as _json
                window = int(integration.get("bundle_window_seconds") or 10)
                phone = canonical_input.get("phone") or "unknown"
                bundle_key = f"bundle:{tenant['id']}:{integration['id']}:{phone}"
                now = _time.time()
                try:
                    redis = get_redis()
                    await redis.rpush(bundle_key, _json.dumps({
                        "msg": canonical_input.get("message") or "",
                        "ts": now,
                        "input": canonical_input,
                        "event_id": str(event_id),
                    }))
                    await redis.set(f"{bundle_key}:last_seen", str(now),
                                    ex=window + 60)
                    await redis.expire(bundle_key, window + 60)
                    process_bundled_message.apply_async(
                        kwargs={
                            "tenant_id": str(tenant["id"]),
                            "integration_id": str(integration["id"]),
                            "bundle_key": bundle_key,
                            "scheduled_for_ts": now,
                        },
                        countdown=window,
                    )
                    log.info("broker.bundled",
                             tenant=str(tenant["id"]),
                             window=window, event_id=str(event_id))
                    return {
                        "accepted": True,
                        "event_id": str(event_id),
                        "mode": "forward",
                        "bundled": True,
                        "window_seconds": window,
                        "info": f"Mensagem agrupada. Aguardando {window}s de silêncio antes de processar.",
                    }
                except Exception as exc:
                    log.warning("broker.bundle_failed_falling_back", error=str(exc))
                    # Cai pro fluxo normal abaixo se Redis falhar

            from workers.celery_app import process_broker_message
            try:
                process_broker_message.delay(
                    tenant_id=str(tenant["id"]),
                    integration_id=str(integration["id"]),
                    raw_event_id=str(event_id),
                    canonical_input=canonical_input,
                )
            except Exception as exc:
                # Broker (RabbitMQ) indisponível — marca como failed pra dar feedback
                log.error("broker.celery_dispatch_failed", error=str(exc))
                async with get_db_conn() as conn:
                    await conn.execute(
                        "UPDATE public.broker_raw_events SET status='failed', "
                        "error=$2, attempts=attempts+1, processed_at=NOW() WHERE id=$1",
                        event_id,
                        f"Worker indisponível (RabbitMQ): {exc}. Suba o RabbitMQ + Celery worker.",
                    )
                raise HTTPException(
                    status_code=503,
                    detail=(
                        "Fila de processamento (RabbitMQ) indisponível. "
                        "Confira se o RabbitMQ e o worker Celery estão rodando. "
                        "Alternativa: use o modo 'Responder na mesma URL' que não depende do worker."
                    ),
                )
            log.info("broker.dispatched_to_agent",
                     tenant=str(tenant["id"]), event_id=str(event_id))
            return {
                "accepted": True,
                "event_id": str(event_id),
                "mode": "forward",
                "info": "Agente processando em background. Resposta será enviada para reply_url.",
            }

        # ── Sync (response) mode: invoke the agent inline ─────────────────────
        # The HTTP connection blocks until the agent finishes (typically 2-8s).
        # Required by gateways that expect the reply in the same HTTP request.
        try:
            agent_reply = await _invoke_agent_sync(
                tenant_id=str(tenant["id"]),
                schema_name=tenant["schema_name"],
                canonical_input=canonical_input,
                event_id=str(event_id),
            )
        except Exception as exc:
            log.error("broker.sync_agent_failed", error=str(exc))
            agent_reply = "Ocorreu um erro no atendimento. Por favor, tente novamente."

        reply_context = {
            "input": canonical_input,
            "reply": agent_reply,
            "phone": canonical_input.get("phone"),
            "message": canonical_input.get("message"),
            "name": canonical_input.get("name"),
            "session_id": canonical_input.get("session_id") or str(event_id),
            "event_id": str(event_id),
        }
        reply_template = integration.get("reply_body_template") or {}
        try:
            reply_body = broker.apply_mapping(reply_template, reply_context) \
                if reply_template else {"reply": agent_reply}
        except Exception as exc:
            log.warning("broker.reply_template_failed", error=str(exc))
            reply_body = {"reply": agent_reply, "_template_error": str(exc)}

        async with get_db_conn() as conn:
            await conn.execute(
                "UPDATE public.broker_raw_events "
                "SET status='processed', canonical_event='agent.message', "
                "canonical_payload=$2, attempts=attempts+1, processed_at=NOW() "
                "WHERE id=$1",
                event_id, {**reply_context, "_reply_body": reply_body},
            )
        from fastapi.responses import JSONResponse
        # Headers customizados configurados pelo tenant são aplicados ao response.
        # Aceita strings simples; nginx/fastapi cuidam dos cabeçalhos padrão.
        custom_headers = {
            str(k): str(v)
            for k, v in (integration.get("reply_headers") or {}).items()
            if k and v
        }
        return JSONResponse(
            status_code=integration.get("reply_status_code") or 200,
            content=reply_body,
            headers=custom_headers or None,
        )

    # ── Legacy mapping/outbound flow (still supported for advanced users) ─
    async with get_db_conn() as conn:
        mappings = [dict(r) for r in await conn.fetch(
            """
            SELECT * FROM public.integration_mappings
            WHERE integration_id = $1 AND direction = 'inbound' AND enabled = TRUE
            ORDER BY version DESC, created_at ASC
            """,
            integration["id"],
        )]

    matched = broker.pick_mapping(mappings, payload)
    if not matched:
        async with get_db_conn() as conn:
            await conn.execute(
                "UPDATE public.broker_raw_events SET status='skipped', "
                "error='no flow or mapping configured', processed_at=NOW() WHERE id=$1",
                event_id,
            )
        return {"accepted": True, "event_id": str(event_id), "matched": False}

    canonical = broker.apply_mapping(matched["field_map"], payload)
    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.broker_raw_events SET status='processed', "
            "canonical_event=$2, canonical_payload=$3, matched_mapping_id=$4, "
            "attempts=attempts+1, processed_at=NOW() WHERE id=$1",
            event_id, matched["canonical_event"], canonical, matched["id"],
        )
    return {"accepted": True, "event_id": str(event_id), "canonical_event": matched["canonical_event"]}


# ── Sync agent invocation (for response mode) ────────────────────────────────

async def _invoke_agent_sync(
    tenant_id: str,
    schema_name: str,
    canonical_input: dict[str, Any],
    event_id: str,
) -> str:
    """
    Runs the LangGraph synchronously and returns the agent's text reply.
    Used by the 'response' mode where the HTTP caller waits for the reply.
    Blocks for the agent's full processing time (usually 2-8s).
    """
    from db.redis_client import get_redis
    from agents.graph_builder import build_graph_for_tenant, TenantConfig
    from services.llm_config import load_tenant_llm_config

    phone = canonical_input.get("phone") or ""
    phone_clean = "".join(c for c in phone if c.isdigit())[:20] or "unknown"
    message = canonical_input.get("message") or ""
    session_id = canonical_input.get("session_id") or phone_clean

    async with get_db_conn() as conn:
        await conn.execute(f"SET search_path = {schema_name}, public")
        rows = await conn.fetch("SELECT skill_name FROM skills_config WHERE ativo = TRUE")
        active_skills = [r["skill_name"] for r in rows]

    llm_cfg = await load_tenant_llm_config(tenant_id)
    tenant_cfg = TenantConfig(
        tenant_id=tenant_id,
        schema_name=schema_name,
        callback_url="",
        skills_active=active_skills,
        **llm_cfg,
    )

    graph = build_graph_for_tenant(tenant_cfg, get_redis())

    initial_state = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "phone": phone_clean,
        "schema_name": schema_name,
        "current_message": message,
        "messages": [],
        "intent": "",
        "selected_skill": "",
        "confidence": 0.0,
        "retry_count": 0,
        "customer_profile": "indefinido",
        "cart": {"items": [], "subtotal": 0.0},
        "stock_mode": "catalogo",
        "available_skills": active_skills,
        "analyst_approved": False,
        "final_response": "",
        "escalate": False,
        "callback_url": "",
        "trace_steps": [],
        "persona": {},
        "skill_prompts": {},
    }

    config = {"configurable": {"thread_id": session_id}}
    import time as _time
    t0 = _time.monotonic()
    final_state: dict | None = None
    err: str | None = None
    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        return final_state.get("final_response") or "Como posso ajudar?"
    except Exception as exc:  # noqa: BLE001
        err = str(exc)
        raise
    finally:
        from services.agent_traces import persist_trace
        await persist_trace(
            schema_name=schema_name,
            session_key=session_id,
            phone=phone_clean,
            message_in=message,
            final_state=final_state,
            latency_ms=int((_time.monotonic() - t0) * 1000),
            error=err,
        )


# ── Portal CRUD ──────────────────────────────────────────────────────────────

portal_router = APIRouter(prefix="/portal/broker", tags=["broker-portal"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


# Integrations -----------------------------------------------------------------

@portal_router.get("/integrations", response_model=list[IntegrationOut])
async def list_integrations(request: Request, user: TenantUser):
    async with get_db_conn() as conn:
        tenant = await conn.fetchrow(
            "SELECT api_key FROM public.tenants WHERE id = $1", user.tenant_id,
        )
        rows = await conn.fetch(
            "SELECT * FROM public.tenant_integrations WHERE tenant_id = $1 ORDER BY created_at DESC",
            user.tenant_id,
        )
    return [
        _integration_out(dict(r), _build_ingest_url(request, tenant["api_key"], r["slug"]))
        for r in rows
    ]


@portal_router.post("/integrations", response_model=IntegrationOut, status_code=201)
async def create_integration(body: IntegrationIn, request: Request, user: TenantUser):
    user.assert_role("manager")
    async with get_db_conn() as conn:
        tenant = await conn.fetchrow(
            "SELECT api_key FROM public.tenants WHERE id = $1", user.tenant_id,
        )
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO public.tenant_integrations
                  (tenant_id, slug, name, direction, hmac_secret, hmac_header, hmac_algorithm, enabled, config_json)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING *
                """,
                user.tenant_id, body.slug, body.name, body.direction,
                body.hmac_secret, body.hmac_header, body.hmac_algorithm, body.enabled,
                body.config_json or {},
            )
        except Exception as exc:
            if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
                raise HTTPException(409, "Slug já existe para esse tenant")
            raise
    await log_event("broker.integration.created", actor_id=user.email,
                    tenant_id=user.tenant_id, target=body.slug, meta={})
    return _integration_out(dict(row), _build_ingest_url(request, tenant["api_key"], row["slug"]))


@portal_router.patch("/integrations/{integration_id}", response_model=IntegrationOut)
async def update_integration(
    integration_id: str, body: IntegrationPatch, request: Request, user: TenantUser,
):
    user.assert_role("manager")
    await _own_integration(integration_id, user.tenant_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Nada para atualizar")
    cols = list(updates.keys())
    set_clauses = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.tenant_integrations SET {set_clauses}, updated_at = NOW() "
            f"WHERE id = $1 RETURNING *",
            integration_id, *updates.values(),
        )
        tenant = await conn.fetchrow(
            "SELECT api_key FROM public.tenants WHERE id = $1", user.tenant_id,
        )
    return _integration_out(dict(row), _build_ingest_url(request, tenant["api_key"], row["slug"]))


@portal_router.delete("/integrations/{integration_id}")
async def delete_integration(integration_id: str, user: TenantUser) -> Response:
    user.assert_role("manager")
    await _own_integration(integration_id, user.tenant_id)
    async with get_db_conn() as conn:
        await conn.execute(
            "DELETE FROM public.tenant_integrations WHERE id = $1", integration_id,
        )
    return Response(status_code=204)


# Flow (simplified per-integration config) ------------------------------------

@portal_router.put("/integrations/{integration_id}/flow", response_model=IntegrationOut)
async def save_flow(
    integration_id: str, body: FlowConfigIn, request: Request, user: TenantUser,
):
    """Saves entrada + resposta config in one shot — what the simple UI uses."""
    user.assert_role("manager")
    await _own_integration(integration_id, user.tenant_id)

    if body.reply_mode == "forward" and not body.reply_url:
        raise HTTPException(422, "URL de destino é obrigatória no modo 'forward'")

    # Validação opcional do bloco de handoff: se enabled=True, exige base_url/token/queue_id.
    handoff = body.handoff_config or {}
    if handoff.get("enabled"):
        missing = [k for k in ("base_url", "token", "queue_id") if not handoff.get(k)]
        if missing:
            raise HTTPException(
                422,
                f"Para ativar transferência ao atendente, preencha: {', '.join(missing)}",
            )

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.tenant_integrations
            SET inbound_field_map      = $2,
                reply_mode             = $3,
                reply_url              = $4,
                reply_method           = $5,
                reply_headers          = $6,
                reply_body_template    = $7,
                reply_status_code      = $8,
                bundle_enabled         = $9,
                bundle_window_seconds  = $10,
                skip_rules             = $11,
                handoff_config         = $12,
                session_config         = $13,
                handoff_pause_minutes  = $14,
                human_handoff_detection = $15,
                updated_at             = NOW()
            WHERE id = $1
            RETURNING *
            """,
            integration_id,
            body.inbound_field_map,
            body.reply_mode,
            body.reply_url,
            body.reply_method,
            body.reply_headers,
            body.reply_body_template,
            body.reply_status_code,
            body.bundle_enabled,
            body.bundle_window_seconds,
            body.skip_rules,
            body.handoff_config,
            body.session_config,
            body.handoff_pause_minutes,
            body.human_handoff_detection,
        )
        tenant = await conn.fetchrow(
            "SELECT api_key FROM public.tenants WHERE id = $1", user.tenant_id,
        )

    await log_event("broker.flow.saved", actor_id=user.email,
                    tenant_id=user.tenant_id, target=str(integration_id),
                    meta={"reply_mode": body.reply_mode})

    return _integration_out(dict(row), _build_ingest_url(request, tenant["api_key"], row["slug"]))


# Mappings ---------------------------------------------------------------------

@portal_router.get("/integrations/{integration_id}/mappings", response_model=list[MappingOut])
async def list_mappings(integration_id: str, user: TenantUser):
    await _own_integration(integration_id, user.tenant_id)
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.integration_mappings WHERE integration_id = $1 "
            "ORDER BY created_at ASC",
            integration_id,
        )
    return [MappingOut(**{**dict(r), "id": str(r["id"]), "integration_id": str(r["integration_id"])})
            for r in rows]


@portal_router.post("/integrations/{integration_id}/mappings", response_model=MappingOut, status_code=201)
async def create_mapping(integration_id: str, body: MappingIn, user: TenantUser):
    user.assert_role("manager")
    await _own_integration(integration_id, user.tenant_id)
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.integration_mappings
              (integration_id, canonical_event, match_rules, field_map, direction, enabled)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING *
            """,
            integration_id, body.canonical_event, body.match_rules,
            body.field_map, body.direction, body.enabled,
        )
    return MappingOut(**{**dict(row), "id": str(row["id"]), "integration_id": str(row["integration_id"])})


@portal_router.patch("/mappings/{mapping_id}", response_model=MappingOut)
async def update_mapping(mapping_id: str, body: MappingPatch, user: TenantUser):
    user.assert_role("manager")
    await _own_mapping(mapping_id, user.tenant_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Nada para atualizar")
    cols = list(updates.keys())
    set_clauses = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.integration_mappings "
            f"SET {set_clauses}, version = version + 1, updated_at = NOW() "
            f"WHERE id = $1 RETURNING *",
            mapping_id, *updates.values(),
        )
    return MappingOut(**{**dict(row), "id": str(row["id"]), "integration_id": str(row["integration_id"])})


@portal_router.delete("/mappings/{mapping_id}")
async def delete_mapping(mapping_id: str, user: TenantUser) -> Response:
    user.assert_role("manager")
    await _own_mapping(mapping_id, user.tenant_id)
    async with get_db_conn() as conn:
        await conn.execute(
            "DELETE FROM public.integration_mappings WHERE id = $1", mapping_id,
        )
    return Response(status_code=204)


# Outbound targets -------------------------------------------------------------

@portal_router.get("/integrations/{integration_id}/outbound", response_model=list[OutboundOut])
async def list_outbound(integration_id: str, user: TenantUser):
    await _own_integration(integration_id, user.tenant_id)
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            "SELECT * FROM public.broker_outbound_targets WHERE integration_id = $1 "
            "ORDER BY created_at ASC",
            integration_id,
        )
    return [OutboundOut(**{**dict(r), "id": str(r["id"])}) for r in rows]


@portal_router.post("/integrations/{integration_id}/outbound", response_model=OutboundOut, status_code=201)
async def create_outbound(integration_id: str, body: OutboundIn, user: TenantUser):
    user.assert_role("manager")
    await _own_integration(integration_id, user.tenant_id)
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.broker_outbound_targets
              (integration_id, canonical_event, url, method, headers, field_map, enabled)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            integration_id, body.canonical_event, body.url, body.method,
            body.headers, body.field_map, body.enabled,
        )
    return OutboundOut(**{**dict(row), "id": str(row["id"])})


@portal_router.patch("/outbound/{target_id}", response_model=OutboundOut)
async def update_outbound(target_id: str, body: OutboundPatch, user: TenantUser):
    user.assert_role("manager")
    await _own_outbound(target_id, user.tenant_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(400, "Nada para atualizar")
    cols = list(updates.keys())
    set_clauses = ", ".join(f"{c} = ${i + 2}" for i, c in enumerate(cols))
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.broker_outbound_targets SET {set_clauses} "
            f"WHERE id = $1 RETURNING *",
            target_id, *updates.values(),
        )
    return OutboundOut(**{**dict(row), "id": str(row["id"])})


@portal_router.delete("/outbound/{target_id}")
async def delete_outbound(target_id: str, user: TenantUser) -> Response:
    user.assert_role("manager")
    await _own_outbound(target_id, user.tenant_id)
    async with get_db_conn() as conn:
        await conn.execute(
            "DELETE FROM public.broker_outbound_targets WHERE id = $1", target_id,
        )
    return Response(status_code=204)


# Preview / Discover / Logs ----------------------------------------------------

class HandoffTestIn(BaseModel):
    phone: str = Field(min_length=4, max_length=20)
    message: str | None = None


@portal_router.post("/integrations/{integration_id}/handoff/test")
async def test_handoff(integration_id: str, body: HandoffTestIn, user: TenantUser):
    """
    Dispara uma transferência de teste usando a config salva da integração.
    Útil pro usuário validar token / queue_id antes de jogar em produção.
    """
    user.assert_role("manager")
    integration = await _own_integration(integration_id, user.tenant_id)
    cfg = integration.get("handoff_config") or {}
    if not cfg.get("enabled"):
        raise HTTPException(400, "Transferência está desativada nesta integração. Ative e salve antes de testar.")

    from services.handoff import transfer_to_human
    phone_clean = "".join(c for c in body.phone if c.isdigit())
    result = await transfer_to_human(cfg, phone=phone_clean, custom_message=body.message)
    await log_event("broker.handoff.tested", actor_id=user.email,
                    tenant_id=user.tenant_id, target=str(integration_id),
                    meta={"ok": result.get("ok"), "status_code": result.get("status_code")})
    return result


@portal_router.post("/preview", response_model=PreviewOut)
async def preview(body: PreviewIn, _user: TenantUser):
    matched = broker.matches(body.match_rules, body.payload)
    result = broker.apply_mapping(body.field_map, body.payload) if matched else {}
    return PreviewOut(matched=matched, result=result)


@portal_router.post("/discover")
async def discover(body: DiscoverIn, _user: TenantUser):
    return {"paths": broker.discover_paths(body.payload)}


@portal_router.get("/integrations/{integration_id}/discover-fields")
async def discover_fields_from_history(
    integration_id: str, user: TenantUser, limit: int = 30,
):
    """Agrega paths dos últimos N eventos REAIS desta integração.

    Em vez de o tenant ter que decorar campos (`$.fromMe`, `$.to`, ...) e colar
    um payload de amostra, o portal busca os últimos eventos brutos já recebidos
    via `/hooks` (tabela `broker_raw_events`) e devolve a lista de paths
    encontrados com seus tipos, amostras de valor e em quantos eventos cada um
    apareceu. A UI alimenta os dropdowns da seção "Pausar a IA quando o
    atendente responder" a partir disso. Stateless do ponto de vista de
    metadados — usa só o que já está persistido.
    """
    await _own_integration(integration_id, user.tenant_id)
    limit = min(max(limit, 1), 100)

    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT payload, direction
              FROM public.broker_raw_events
             WHERE tenant_id = $1
               AND integration_id = $2
               AND payload IS NOT NULL
             ORDER BY created_at DESC
             LIMIT $3
            """,
            user.tenant_id, integration_id, limit,
        )

    # Agregação: {path -> {type, samples[], directions{}, event_count}}
    agg: dict[str, dict[str, Any]] = {}
    inbound_count = 0
    outbound_count = 0
    for r in rows:
        direction = (r["direction"] or "inbound").lower()
        if direction == "outbound":
            outbound_count += 1
        else:
            inbound_count += 1
        try:
            paths = broker.discover_paths(r["payload"])
        except Exception:
            continue
        for p in paths:
            key = p["path"]
            slot = agg.get(key)
            if slot is None:
                slot = {"path": key, "type": p["type"],
                        "samples": [], "directions": set(), "event_count": 0}
                agg[key] = slot
            slot["event_count"] += 1
            slot["directions"].add(direction)
            sample = p.get("sample")
            # Mantém amostras únicas (máx 5), priorizando primitivos legíveis.
            if sample is not None and sample != "" \
                    and sample not in slot["samples"] and len(slot["samples"]) < 5:
                slot["samples"].append(sample)

    paths_out = [
        {
            "path": v["path"],
            "type": v["type"],
            "samples": v["samples"],
            "directions": sorted(v["directions"]),
            "event_count": v["event_count"],
        }
        for v in agg.values()
    ]
    # Ordena por contagem desc → quem mais aparece sobe (mais útil pro dropdown).
    paths_out.sort(key=lambda x: (-x["event_count"], x["path"]))

    return {
        "paths": paths_out,
        "event_count": len(rows),
        "inbound_count": inbound_count,
        "outbound_count": outbound_count,
    }


@portal_router.get("/raw-events", response_model=list[RawEventOut])
async def list_events(user: TenantUser, limit: int = 50, status_filter: str | None = None):
    limit = min(max(limit, 1), 200)
    async with get_db_conn() as conn:
        if status_filter:
            rows = await conn.fetch(
                """
                SELECT id, integration_slug, direction, status, canonical_event,
                       error, attempts, created_at, idempotency_key, payload,
                       forward_status_code
                FROM public.broker_raw_events
                WHERE tenant_id = $1 AND status = $2
                ORDER BY created_at DESC LIMIT $3
                """,
                user.tenant_id, status_filter, limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, integration_slug, direction, status, canonical_event,
                       error, attempts, created_at, idempotency_key, payload,
                       forward_status_code
                FROM public.broker_raw_events
                WHERE tenant_id = $1
                ORDER BY created_at DESC LIMIT $2
                """,
                user.tenant_id, limit,
            )
    return [
        RawEventOut(
            id=str(r["id"]),
            integration_slug=r["integration_slug"],
            direction=r["direction"],
            status=r["status"],
            canonical_event=r["canonical_event"],
            error=r["error"],
            attempts=r["attempts"],
            created_at=r["created_at"].isoformat(),
            idempotency_key=r["idempotency_key"],
            payload_preview=(json.dumps(r["payload"], ensure_ascii=False)[:200]
                             if r["payload"] is not None else None),
            forward_status_code=r["forward_status_code"],
        )
        for r in rows
    ]


@portal_router.get("/raw-events/{event_id}")
async def get_event(event_id: str, user: TenantUser):
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.broker_raw_events WHERE id = $1 AND tenant_id = $2",
            event_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(404, "Evento não encontrado")
    return {
        **{k: v for k, v in dict(row).items() if k not in {"created_at", "processed_at"}},
        "id": str(row["id"]),
        "tenant_id": str(row["tenant_id"]),
        "integration_id": str(row["integration_id"]) if row["integration_id"] else None,
        "matched_mapping_id": str(row["matched_mapping_id"]) if row["matched_mapping_id"] else None,
        "created_at": row["created_at"].isoformat(),
        "processed_at": row["processed_at"].isoformat() if row["processed_at"] else None,
    }


@portal_router.post("/raw-events/{event_id}/replay")
async def replay_event(event_id: str, user: TenantUser):
    user.assert_role("manager")
    async with get_db_conn() as conn:
        ev = await conn.fetchrow(
            "SELECT * FROM public.broker_raw_events WHERE id = $1 AND tenant_id = $2",
            event_id, user.tenant_id,
        )
        if not ev:
            raise HTTPException(404, "Evento não encontrado")
        mappings = [dict(r) for r in await conn.fetch(
            """
            SELECT * FROM public.integration_mappings
            WHERE integration_id = $1 AND direction = 'inbound' AND enabled = TRUE
            ORDER BY version DESC, created_at ASC
            """,
            ev["integration_id"],
        )]
    matched = broker.pick_mapping(mappings, ev["payload"])
    if not matched:
        return {"matched": False}
    canonical = broker.apply_mapping(matched["field_map"], ev["payload"])
    async with get_db_conn() as conn:
        await conn.execute(
            """
            UPDATE public.broker_raw_events
            SET status = 'processed', canonical_event = $2, canonical_payload = $3,
                matched_mapping_id = $4, error = NULL, attempts = attempts + 1,
                processed_at = NOW()
            WHERE id = $1
            """,
            event_id, matched["canonical_event"], canonical, matched["id"],
        )
    return {"matched": True, "canonical_event": matched["canonical_event"], "result": canonical}
