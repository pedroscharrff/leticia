"""
Celery application and the main process_message task.

Each task:
1. Fetches the tenant's active skills from PostgreSQL
2. Builds the LangGraph for that tenant
3. Invokes the graph with the incoming message
4. POSTs the final response to the tenant's callback_url (with retry)
"""
import asyncio
import json
import os
import time

# ── Prometheus multiprocess setup ─────────────────────────────────────────────
# DEVE rodar antes de qualquer Counter()/Histogram() ser instanciado, porque
# prometheus_client decide o backend (single vs mmap) no momento da criação
# da métrica, lendo $PROMETHEUS_MULTIPROC_DIR.
#
# Default = 0 (desligado) porque este módulo é importado pelo processo API
# (via routers/webhook.py → process_message) e setar PROMETHEUS_MULTIPROC_DIR
# lá quebra as Gauges do metrics_collector (multiproc sem `multiprocess_mode`
# = série invisível no scrape). Container `worker:` do compose seta
# WORKER_METRICS_PORT=9100 explícito; container `api:` não seta e cai em 0.
_METRICS_PORT = int(os.environ.get("WORKER_METRICS_PORT", "0"))
_MULTIPROC_DIR = os.environ.get("PROMETHEUS_MULTIPROC_DIR", "/tmp/prom_multiproc_celery")

if _METRICS_PORT > 0:
    os.environ["PROMETHEUS_MULTIPROC_DIR"] = _MULTIPROC_DIR
    os.makedirs(_MULTIPROC_DIR, exist_ok=True)
    # Limpa restos de execução anterior (PIDs antigos com mmap stale)
    for _fname in os.listdir(_MULTIPROC_DIR):
        try:
            os.unlink(os.path.join(_MULTIPROC_DIR, _fname))
        except OSError:
            pass

import httpx
import structlog
from celery import Celery
from celery.signals import worker_init, worker_process_init, worker_process_shutdown
from prometheus_client import Counter, Histogram
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from llm.usage_tracking import begin_turn as _begin_token_turn

log = structlog.get_logger()

# ── Celery app ────────────────────────────────────────────────────────────────

celery_app = Celery(
    "saas_farmacia",
    broker=settings.rabbitmq_url,
    backend=f"redis://{settings.redis_url.split('redis://')[-1]}",
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    worker_concurrency=settings.celery_workers_concurrency,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # Beat schedule — jobs proativos (capability-gated por dentro)
    beat_schedule={
        "recover_abandoned_carts": {
            # 2 min: granularidade fina pra honrar `delay_minutes` (1–1440 min)
            # configurável pelo tenant. Schedule antigo (1h) só funcionava
            # quando o campo legado era `delay_hours` (4h default) — agora um
            # cart elegível na régua de 2 min esperaria até a próxima hora
            # cheia pro nudge sair. Job é leve (1 SELECT por tenant) e respeita
            # quiet_hours por dentro, então 2 min é barato e correto.
            "task":     "jobs.recover_abandoned_carts",
            "schedule": 60 * 2,
        },
        "expire_abandoned_carts": {
            # 2 min: granularidade fina pra honrar expire_minutes a partir de 1.
            # Job é leve (varre só carts com sent_recovery_at preenchido).
            "task":     "jobs.expire_abandoned_carts",
            "schedule": 60 * 2,
        },
        "nudge_continuous_refill": {
            # 24h: roda 1x por dia. O job interno respeita time_of_day da
            # config (futuro) — por ora, executa sempre quando o beat acorda.
            "task":     "jobs.nudge_continuous_refill",
            "schedule": 60 * 60 * 24,
        },
        "aggregate_llm_usage_daily": {
            # 24h: agrega tokens consumidos do dia anterior por tenant×modelo
            # em public.llm_usage_daily. Idempotente (ON CONFLICT). Não trava
            # se um tenant falhar — segue pros próximos e loga erro.
            "task":     "jobs.aggregate_llm_usage_daily",
            "schedule": 60 * 60 * 24,
        },
    },
)


# ── Beat tasks (capability-gated dentro dos jobs) ───────────────────────────

@celery_app.task(name="jobs.recover_abandoned_carts", bind=True, max_retries=0)
def recover_abandoned_carts_task(self) -> dict:
    """Task agendada — chama o job sync que lê a flag por tenant."""
    from workers.jobs.abandoned_cart import recover_abandoned_carts_sync
    try:
        return recover_abandoned_carts_sync()
    except Exception as exc:  # noqa: BLE001
        log.warning("celery.recover_failed", exc=str(exc))
        return {"error": str(exc)}


@celery_app.task(name="jobs.expire_abandoned_carts", bind=True, max_retries=0)
def expire_abandoned_carts_task(self) -> dict:
    """Encerra tickets cujo carrinho não teve retorno após a recuperação."""
    from workers.jobs.expire_carts import expire_abandoned_carts_sync
    try:
        return expire_abandoned_carts_sync()
    except Exception as exc:  # noqa: BLE001
        log.warning("celery.expire_failed", exc=str(exc))
        return {"error": str(exc)}


@celery_app.task(name="jobs.nudge_continuous_refill", bind=True, max_retries=0)
def nudge_continuous_refill_task(self) -> dict:
    from workers.jobs.refill_nudge import nudge_continuous_refill_sync
    try:
        return nudge_continuous_refill_sync()
    except Exception as exc:  # noqa: BLE001
        log.warning("celery.refill_failed", exc=str(exc))
        return {"error": str(exc)}


@celery_app.task(name="jobs.aggregate_llm_usage_daily", bind=True, max_retries=0)
def aggregate_llm_usage_daily_task(self, target_day_iso: str | None = None) -> dict:
    """Agrega conversation_logs do dia anterior em public.llm_usage_daily.

    Passa `target_day_iso='YYYY-MM-DD'` pra re-processar um dia específico
    (ex.: `celery -A workers.celery_app call jobs.aggregate_llm_usage_daily
    --args='["2026-06-04"]'`).
    """
    from workers.jobs.aggregate_usage import aggregate_llm_usage_daily_sync
    try:
        return aggregate_llm_usage_daily_sync(target_day_iso)
    except Exception as exc:  # noqa: BLE001
        log.warning("celery.aggregate_usage_failed", exc=str(exc))
        return {"error": str(exc)}


# Disparo manual de recuperação em lote (chamado pelo endpoint
# POST /portal/recovery/trigger). NÃO está no beat — é on-demand.
@celery_app.task(name="jobs.process_recovery_batch", bind=True, max_retries=0)
def process_recovery_batch_task(self, batch_id: str) -> dict:
    from workers.jobs.recovery_batch import process_recovery_batch_sync
    try:
        return process_recovery_batch_sync(batch_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("celery.recovery_batch_failed",
                    batch_id=batch_id, exc=str(exc))
        return {"error": str(exc)}


# ── Prometheus metrics ────────────────────────────────────────────────────────

CONV_TOTAL = Counter(
    "conversations_total",
    "Total conversations processed",
    ["tenant_id", "skill", "status"],
)
LATENCY = Histogram(
    "conversation_latency_seconds",
    "End-to-end conversation latency",
    ["tenant_id", "skill"],
)
LLM_ERRORS = Counter(
    "llm_errors_total",
    "LLM call failures",
    ["tenant_id", "skill", "llm_model"],
)


# ── Expor métricas via HTTP no worker (multiprocess mode) ────────────────────
#
# Celery prefork: N processos filhos. Cada um tem seu próprio in-memory
# REGISTRY → se cada fork servisse HTTP, (a) só o primeiro bindaria a porta
# e (b) o scrape veria só o slice de um fork.
#
# Modelo oficial: `prometheus_client.multiprocess`. Forks escrevem samples
# em arquivos mmap em $PROMETHEUS_MULTIPROC_DIR (setado no topo deste módulo);
# um único HTTP server no processo PAI lê os arquivos via MultiProcessCollector
# no momento do scrape e devolve a soma agregada.
#
# Réplicas de worker no compose recebem hostnames separados; Prometheus
# scrape `worker:9100` resolve via DNS-RR — pra Counter (monotônico) está OK
# porque PromQL agrega com sum().
#
# Desligar: WORKER_METRICS_PORT=0


@worker_init.connect
def _start_metrics_server(**_kw) -> None:
    """Sobe o HTTP no processo PAI do Celery (uma vez por container)."""
    if _METRICS_PORT <= 0:
        return
    try:
        from prometheus_client import CollectorRegistry, multiprocess
        from prometheus_client.exposition import start_http_server as _start

        registry = CollectorRegistry()
        multiprocess.MultiProcessCollector(registry)
        _start(_METRICS_PORT, registry=registry)
        log.info("worker.metrics.listening",
                 port=_METRICS_PORT, dir=_MULTIPROC_DIR)
    except OSError as exc:
        log.warning("worker.metrics.bind_failed",
                    port=_METRICS_PORT, exc=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.warning("worker.metrics.start_failed", exc=str(exc))


@worker_process_shutdown.connect
def _cleanup_child_metrics(pid=None, **_kw) -> None:
    """Remove o mmap do fork quando ele encerra (evita drift de Counters)."""
    if _METRICS_PORT <= 0:
        return
    try:
        from prometheus_client import multiprocess
        multiprocess.mark_process_dead(pid or os.getpid(), path=_MULTIPROC_DIR)
    except Exception:  # noqa: BLE001
        pass


# ── Callback delivery ─────────────────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    reraise=True,
)
async def _deliver_response(callback_url: str, payload: dict) -> None:
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(callback_url, json=payload)
        resp.raise_for_status()


# ── Pre-handoff offers ───────────────────────────────────────────────────────

_DEFAULT_OFFERS_HEADER = "Antes de transferir, veja nossas ofertas:"


def _format_offers_text_block(offers: list[dict], header: str) -> str:
    """Bullet único com ofertas só-texto (mantém formato v1)."""
    lines = [header.strip()]
    for o in offers:
        title = (o.get("title") or "").strip()
        desc  = (o.get("description") or "").strip()
        if not title:
            continue
        lines.append(f"• {title}: {desc}" if desc else f"• {title}")
    return "\n".join(lines)


def _offer_caption(o: dict) -> str:
    """Caption de uma oferta com mídia: '{title}: {description}' ou só title."""
    title = (o.get("title") or "").strip()
    desc  = (o.get("description") or "").strip()
    if title and desc:
        return f"{title}: {desc}"
    return title or desc


async def _resolve_sentiment_transfer_message(
    tenant_id: str,
    final_state: dict | None,
) -> str:
    """Retorna a mensagem de transferência específica de sentimento, ou "".

    Só retorna texto quando OS DOIS forem verdadeiros:
      1. A escalação foi marcada como vindo do sentiment_analyzer
         (`final_state["escalate_reason"] == "sentiment"`).
      2. O tenant configurou `transfer_message` na capability
         `intelligence.sentiment_analysis` (não-vazio).

    Qualquer outro caminho (skill emitiu [[ESCALATE]], keyword bateu,
    order_finalized, ou capability sem `transfer_message`) → retorna "" e o
    chamador segue o comportamento atual. Tolerante a falha: erro = "".
    """
    try:
        if not final_state or final_state.get("escalate_reason") != "sentiment":
            return ""
        from services import capabilities as cap_svc
        cfg = await cap_svc.get_config(tenant_id, "intelligence.sentiment_analysis") or {}
        return (cfg.get("transfer_message") or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.warning("handoff.sentiment_transfer_message.failed",
                    tenant=tenant_id, exc=str(exc))
        return ""


async def _send_pre_handoff_offers(
    tenant_id: str,
    *,
    phone: str,
    channel_cfg: dict | None,
    text_sender,        # callable async (text: str) -> None  — texto para o cliente
) -> int:
    """Envia ofertas vigentes como mensagens SEPARADAS (após a mensagem
    principal de handoff já ter saído).

    Comportamento:
      - Ofertas só-texto → uma única mensagem com bullets (v1 preservada).
      - Ofertas com mídia → uma mensagem por oferta via channel_media,
        usando o provider configurado em `channel_cfg`.
      - Provider sem suporte a mídia → caption viaja como texto puro
        (mantém a oferta visível, perde só o anexo).

    NUNCA deve levantar exceção — handoff já saiu, nada deve quebrar aqui.
    """
    try:
        from services import capabilities as cap_svc
        from services import offers as offers_svc
        from services import channel_media as cm

        if not await cap_svc.is_enabled(tenant_id, "sales.pre_handoff_offers"):
            # Skip explícito: capability desligada no tenant. Logar para o skip
            # ser diagnosticável — handoff dispara em código (true/false), então
            # "não saiu oferta" nunca deve ser um no-op silencioso.
            log.info("pre_handoff_offers.skipped",
                     tenant=tenant_id, reason="capability_disabled")
            return 0

        cfg = await cap_svc.get_config(tenant_id, "sales.pre_handoff_offers") or {}
        limit  = int(cfg.get("max_offers", 3) or 3)
        header = cfg.get("header_text") or _DEFAULT_OFFERS_HEADER

        offers = await offers_svc.get_active_offers(tenant_id, limit=limit)
        if not offers:
            # Skip explícito: capability ON mas nenhuma oferta vigente
            # (active=FALSE ou fora da janela valid_from/valid_until). É a causa
            # nº1 de "transferiu mas não mandou oferta" — antes era invisível.
            log.info("pre_handoff_offers.skipped",
                     tenant=tenant_id, reason="no_active_offers")
            return 0

        text_only = [o for o in offers if not (o.get("media_url") and o.get("media_type"))]
        with_media = [o for o in offers if (o.get("media_url") and o.get("media_type"))]

        # 1) Bloco textual (v1) para ofertas sem mídia
        if text_only:
            try:
                await text_sender(_format_offers_text_block(text_only, header))
            except Exception as exc:  # noqa: BLE001
                log.warning("pre_handoff_offers.text_send_failed",
                            tenant=tenant_id, exc=str(exc))

        # 2) Uma mensagem por oferta com mídia
        provider = (channel_cfg or {}).get("provider") if channel_cfg else None
        phone_clean = "".join(c for c in phone if c.isdigit())

        for o in with_media:
            caption = _offer_caption(o)
            sent_as_media = False
            if provider and channel_cfg:
                result = await cm.send_media(
                    provider, channel_cfg,
                    media_type=o["media_type"],
                    phone=phone_clean,
                    caption=caption,
                    media_url=o["media_url"],
                )
                if result.get("ok"):
                    sent_as_media = True
                else:
                    log.warning(
                        "pre_handoff_offers.media_failed_fallback_text",
                        tenant=tenant_id, provider=provider,
                        media_type=o.get("media_type"),
                        error=result.get("error"),
                    )
            if not sent_as_media:
                # Fallback: provider sem suporte ou erro → manda só caption
                try:
                    await text_sender(caption)
                except Exception as exc:  # noqa: BLE001
                    log.warning("pre_handoff_offers.media_text_fallback_failed",
                                tenant=tenant_id, exc=str(exc))

        log.info(
            "pre_handoff_offers.sent",
            tenant=tenant_id,
            text_count=len(text_only),
            media_count=len(with_media),
        )
        return len(with_media)
    except Exception as exc:  # noqa: BLE001
        log.warning("pre_handoff_offers.failed",
                    tenant=tenant_id, exc=str(exc))
        return 0


async def _send_post_handoff_messages(
    tenant_id: str,
    *,
    phone: str,
    cart,
    channel_cfg: dict | None,
    text_sender,
) -> None:
    """Envia resumo do pedido e ofertas pré-handoff na ordem configurada.

    Ordem controlada por `handoff_config.post_handoff_order`:
      - "summary_first"  (default) → resumo depois ofertas  [comportamento original]
      - "offers_first"             → ofertas depois resumo

    ⚠️ Ordem de ENVIO ≠ ordem de ENTREGA: ofertas com mídia saem direto pela API
    do canal (ex. ClickMassa), o resumo sai pelo `reply_url` (transportes
    distintos), e o WhatsApp entrega mídia mais devagar que texto. Por isso, no
    fluxo `offers_first`, quando a oferta tinha mídia, esperamos um intervalo
    antes do resumo pra dar tempo da imagem aparecer primeiro. Configurável em
    `handoff_config.post_handoff_media_delay_seconds` (default 2.5s, 0 desativa).

    Cada bloco é independente — falha num não cancela o outro.
    NUNCA levanta exceção.
    """
    from services.order_summary import send_order_summary

    cfg = channel_cfg or {}
    order = cfg.get("post_handoff_order") or "summary_first"

    async def _do_summary() -> None:
        try:
            await send_order_summary(
                tenant_id,
                phone=phone,
                cart=cart,
                text_sender=text_sender,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("post_handoff.order_summary_failed",
                        tenant=tenant_id, exc=str(exc))

    async def _do_offers() -> int:
        return await _send_pre_handoff_offers(
            tenant_id,
            phone=phone,
            channel_cfg=channel_cfg,
            text_sender=text_sender,
        )

    if order == "offers_first":
        media_count = await _do_offers()
        # Atraso só quando houve mídia: a imagem demora mais pra entregar no
        # WhatsApp; sem isso o resumo (texto) ultrapassa a oferta (imagem).
        if media_count and media_count > 0:
            try:
                delay = float(cfg.get("post_handoff_media_delay_seconds", 2.5))
            except (TypeError, ValueError):
                delay = 2.5
            if delay > 0:
                log.info("post_handoff.media_delay_before_summary",
                         tenant=tenant_id, seconds=delay, media_count=media_count)
                await asyncio.sleep(delay)
        await _do_summary()
    else:
        await _do_summary()
        await _do_offers()


# ── Session close keyword + auto-reset ──────────────────────────────────────

async def _maybe_close_or_reset_session(
    tenant_id: str,
    phone: str,
    current_message: str,
    session_cfg: dict,
    *,
    text_sender,   # async (text: str) -> None — entrega a confirmação ao cliente
) -> bool:
    """Aplica o ciclo de vida da sessão antes de rodar o agente.

    1) Se a conversa está com `closed_at` marcado E NÃO está pausada (janela
       de handoff já expirou), reseta — próxima mensagem inicia atendimento
       do zero.
    2) Se a mensagem casa com uma `close_keywords` configurada, encerra a
       sessão, manda a `close_message` e retorna True (sinaliza ao caller
       para PULAR o agente).

    Retorna True quando a mensagem deve ser tratada como "encerramento" e
    o agente NÃO deve rodar; False para seguir o fluxo normal.
    """
    from services import conversation_state as cs
    from services.session_close import (
        coerce_session_config, matches_close_keyword, DEFAULT_CLOSE_MESSAGE,
    )

    cfg = coerce_session_config(session_cfg)
    keywords = cfg.get("close_keywords") or []
    log.info(
        "session.lifecycle_check",
        tenant=tenant_id, phone_prefix=phone[:4],
        msg_preview=(current_message or "")[:60],
        close_keywords_count=len(keywords),
        has_session_cfg=bool(cfg),
    )

    # 1) Reset automático: cliente voltou após handoff (closed_at marcado e
    #    janela de pausa expirada — paused_until <= NOW ou ai_paused=FALSE).
    try:
        state = await cs.get_state(tenant_id, phone)
        if state.get("closed_at"):
            from datetime import datetime, timezone
            paused_until = state.get("paused_until")
            still_paused = False
            if state.get("ai_paused"):
                if not paused_until:
                    still_paused = True
                else:
                    pu = datetime.fromisoformat(paused_until)
                    if pu > datetime.now(timezone.utc):
                        still_paused = True
            if not still_paused:
                await cs.reset_session(
                    tenant_id, phone,
                    by="auto:new_contact",
                    reason="post_close_new_contact",
                )
                log.info("session.reset_on_new_contact",
                         tenant=tenant_id, phone=phone[:4])
    except Exception as exc:  # noqa: BLE001
        log.warning("session.reset_check_failed",
                    tenant=tenant_id, phone=phone[:4], exc=str(exc))

    # 2) Palavra-chave de encerramento enviada pelo cliente.
    matched = matches_close_keyword(current_message, keywords)
    if not matched:
        return False

    close_msg = (cfg.get("close_message") or DEFAULT_CLOSE_MESSAGE).strip()
    try:
        await cs.end_session(
            tenant_id, phone,
            by="customer:keyword",
            reason=f"close_keyword:{matched}",
            clear_history=True,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("session.close_via_keyword_failed",
                    tenant=tenant_id, phone=phone[:4], exc=str(exc))

    try:
        await text_sender(close_msg)
    except Exception as exc:  # noqa: BLE001
        log.warning("session.close_message_delivery_failed",
                    tenant=tenant_id, phone=phone[:4], exc=str(exc))

    log.info("session.closed_by_keyword",
             tenant=tenant_id, phone=phone[:4], keyword=matched)
    return True


# ── Triggers determinísticos pós-agente ─────────────────────────────────────

def _extract_order_close_signal(final_state: dict | None) -> dict | None:
    """Retorna o snapshot do pedido fechado nesta task, ou None.

    Determinístico — não depende do LLM emitir nada. Lê o marker que a tool
    `finalizar_pedido` deixa em `cart.last_order` quando o pedido é
    efetivamente criado no banco. Funciona pra modo normal (inventory.py).
    Pré-atendimento (balcao.py) continua disparando via escalate=True como
    sempre fez.
    """
    if not final_state:
        return None
    cart = final_state.get("cart") or {}
    if not cart.get("just_finalized"):
        return None
    last_order = cart.get("last_order")
    if not isinstance(last_order, dict):
        return None
    return last_order


def _normalize_cart_items_pt(items: list) -> list[dict]:
    """Converte itens do cart (chaves EN do agente) para PT (que o resumo espera).

    O cart interno do agente usa `name`/`qty`/`price` (consistente com tools de
    inventory/balcao). O `order_summary.build_summary_text` espera `nome`/
    `quantidade`/`preco`. Aceita também chaves PT, pra ser idempotente caso
    algum caller no futuro já mande no formato certo.
    """
    out: list[dict] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        nome = it.get("nome") or it.get("name") or it.get("produto") or ""
        qtd  = it.get("quantidade") or it.get("qty") or 1
        preco = it.get("preco") or it.get("price") or 0
        if not str(nome).strip():
            continue
        out.append({
            "nome":       str(nome).strip(),
            "quantidade": qtd,
            "preco":      preco,
        })
    return out


def _cart_for_summary(final_state: dict | None) -> dict | None:
    """Retorna o cart certo pro resumo do pedido, já normalizado em PT.

    Se `cart.last_order` existe (pedido acabou de fechar e o cart já está
    esvaziado), monta um dict equivalente com os itens originais — só assim
    o resumo mostra o que o cliente realmente pediu. Caso contrário usa o
    cart atual (caso pré-atendimento, sem finalizar_pedido).
    """
    if not final_state:
        return None
    cart = final_state.get("cart") or {}
    last = cart.get("last_order")
    if isinstance(last, dict) and last.get("items"):
        return {
            "items":    _normalize_cart_items_pt(last.get("items") or []),
            "subtotal": last.get("subtotal") or 0,
            # Campos extras que o template do resumo pode usar no futuro
            "discount": last.get("discount") or 0,
            "total":    last.get("total")    or 0,
            "payment":  last.get("payment")  or "",
            "order_id": last.get("id")       or "",
        }
    # Pré-atendimento: cart bruto, normaliza items pra PT
    return {
        "items":    _normalize_cart_items_pt(cart.get("items") or []),
        "subtotal": cart.get("subtotal") or 0,
    }


# ── Main task ─────────────────────────────────────────────────────────────────

@celery_app.task(name="process_message", bind=True, max_retries=0)
def process_message(
    self,
    tenant_id: str,
    schema_name: str,
    callback_url: str,
    phone: str,
    session_id: str,
    current_message: str,
    media: dict | None = None,
) -> None:
    asyncio.run(
        _run_graph(
            tenant_id=tenant_id,
            schema_name=schema_name,
            callback_url=callback_url,
            phone=phone,
            session_id=session_id,
            current_message=current_message,
            media=media,
        )
    )


async def _run_graph(
    tenant_id: str,
    schema_name: str,
    callback_url: str,
    phone: str,
    session_id: str,
    current_message: str,
    media: dict | None = None,
) -> None:
    from db.postgres import get_db_conn, init_pool
    from db.redis_client import get_redis, init_redis
    from agents.graph_builder import build_graph_for_tenant, TenantConfig
    from services.llm_config import load_tenant_llm_config

    # Lazy-init connections inside the async loop (worker process)
    await init_pool()
    await init_redis()

    redis = get_redis()

    # Fetch active skills and LLM config for this tenant
    async with get_db_conn() as conn:
        await conn.execute(f"SET search_path = {schema_name}, public")
        rows = await conn.fetch(
            "SELECT skill_name FROM skills_config WHERE ativo = TRUE"
        )
        active_skills = [r["skill_name"] for r in rows]

    llm_cfg = await load_tenant_llm_config(tenant_id)

    # ── Session lifecycle (close keyword + auto-reset pós-handoff) ───────────
    # Lê session_config do primeiro canal ativo com config preenchida.
    try:
        async with get_db_conn() as conn:
            ch_row = await conn.fetchrow(
                """
                SELECT session_config
                  FROM public.tenant_channels
                 WHERE tenant_id = $1 AND active = TRUE
                 ORDER BY created_at
                 LIMIT 1
                """,
                tenant_id,
            )
        session_cfg = (ch_row and ch_row["session_config"]) or {}
        if isinstance(session_cfg, str):
            session_cfg = json.loads(session_cfg) if session_cfg else {}

        async def _send_via_callback(text: str) -> None:
            await _deliver_response(callback_url, {
                "phone": phone,
                "session_id": session_id,
                "message": text,
                "tenant_id": tenant_id,
                "kind": "session_closed",
            })

        ended = await _maybe_close_or_reset_session(
            tenant_id, phone, current_message,
            session_cfg,
            text_sender=_send_via_callback,
        )
        if ended:
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook.flow.session_lifecycle_failed",
                    tenant=tenant_id, exc=str(exc))

    tenant_cfg = TenantConfig(
        tenant_id=tenant_id,
        schema_name=schema_name,
        callback_url=callback_url,
        skills_active=active_skills,
        **llm_cfg,
    )

    graph = build_graph_for_tenant(tenant_cfg, redis)

    initial_state = {
        "tenant_id": tenant_id,
        "session_id": session_id,
        "phone": phone,
        "schema_name": schema_name,
        "current_message": current_message,
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
        "callback_url": callback_url,
        "trace_steps": [],
        "persona": {},
        "skill_prompts": {},
    }

    if media:
        initial_state.update({
            "media_type": media.get("media_type"),
            "media_mime": media.get("media_mime"),
            "media_url":  media.get("media_url"),
            "media_id":   media.get("media_id"),
            "media_b64":  media.get("media_b64"),
        })

    config = {"configurable": {"thread_id": session_id}}

    t0 = time.monotonic()
    skill_used = "unknown"
    final_state: dict | None = None
    trace_error: str | None = None

    # Abre buffer de tokens do turno (callback em llm/usage_tracking acumula
    # nele E incrementa Counter Prometheus). tenant_name fica vazio aqui —
    # Grafana faz join com saas_tenant_* via tenant_id.
    _begin_token_turn(tenant_id, "")

    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        skill_used = final_state.get("selected_skill", "unknown")
        response_text = final_state.get("final_response", "")

        elapsed = time.monotonic() - t0
        LATENCY.labels(tenant_id=tenant_id, skill=skill_used).observe(elapsed)
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="ok").inc()

        # ── Handoff p/ atendente humano (balcão) ─────────────────────────────
        # Para webhooks nativos (POST /webhook/{token}) lemos o handoff_config
        # do PRIMEIRO canal ativo do tenant que tenha handoff habilitado.
        # (Tenants com 1 canal — a maioria — sempre cai no canal certo.)
        agent_escalate = bool(final_state.get("escalate", False))
        # Trigger determinístico: pedido fechado nesta task força handoff
        # mesmo sem o LLM emitir [[ESCALATE]]. Independe do prompt.
        order_close = _extract_order_close_signal(final_state)
        order_just_finalized = order_close is not None
        if order_just_finalized:
            log.info("webhook.order_finalized.trigger",
                     tenant=tenant_id, order_id=str(order_close.get("id"))[:8])
        handoff_cfg: dict = {}
        channel_pause_minutes: int = 240  # default 4h
        try:
            async with get_db_conn() as conn:
                ch_row = await conn.fetchrow(
                    """
                    SELECT handoff_config, handoff_pause_minutes
                      FROM public.tenant_channels
                     WHERE tenant_id = $1
                       AND active = TRUE
                       AND handoff_config IS NOT NULL
                       AND handoff_config != '{}'::jsonb
                     ORDER BY created_at
                     LIMIT 1
                    """,
                    tenant_id,
                )
            if ch_row and ch_row["handoff_config"]:
                cfg_raw = ch_row["handoff_config"]
                if isinstance(cfg_raw, str):
                    handoff_cfg = json.loads(cfg_raw) or {}
                else:
                    handoff_cfg = dict(cfg_raw)
                channel_pause_minutes = int(ch_row["handoff_pause_minutes"] or 240)
        except Exception as exc:  # noqa: BLE001
            log.warning("webhook.handoff.lookup_failed", tenant=tenant_id, exc=str(exc))

        handoff_was_executed = False
        try:
            from services.handoff import should_handoff, transfer_to_human
            do_handoff, reason = should_handoff(
                handoff_cfg,
                # OU escalate do agente OU pedido determinísticamente fechado
                agent_escalate=(agent_escalate or order_just_finalized),
                user_message=current_message,
            )
            if order_just_finalized and not agent_escalate:
                reason = "order_finalized"
            if do_handoff:
                handoff_was_executed = True
                log.info(
                    "webhook.handoff.triggered",
                    tenant=tenant_id, reason=reason,
                    phone_prefix=phone[:4], agent_escalate=agent_escalate,
                    order_finalized=order_just_finalized,
                )
                phone_clean = "".join(c for c in phone if c.isdigit())
                # Usa a resposta do agente como mensagem de transferência
                # quando: (a) o agente escalou explicitamente (texto bem
                # construído de despedida) OU (b) a tool de fechar pedido
                # rodou (texto contém o recibo "✅ Pedido confirmado #...").
                use_agent_reply = bool(response_text) and (agent_escalate or order_just_finalized)
                # Mensagem específica por gatilho de SENTIMENTO (capability
                # intelligence.sentiment_analysis → config `transfer_message`).
                # Só aplica quando o sentiment_analyzer marcou a escalação
                # como sua (escalate_reason == "sentiment") E o tenant
                # configurou um texto. Caso contrário, comportamento atual
                # intacto — não afeta [[ESCALATE]], keyword, order_finalized.
                sentiment_msg = await _resolve_sentiment_transfer_message(
                    tenant_id, final_state,
                )
                if sentiment_msg:
                    response_text = sentiment_msg
                    use_agent_reply = False
                hresult = await transfer_to_human(
                    handoff_cfg, phone=phone_clean,
                    custom_message=(response_text if (use_agent_reply or sentiment_msg) else None),
                )
                if not use_agent_reply and not sentiment_msg:
                    response_text = (
                        handoff_cfg.get("transfer_message")
                        or "Estou te transferindo para um atendente agora. Um momento, por favor."
                    )
                skill_used = "handoff"
                log.info("webhook.handoff.result", ok=hresult.get("ok"),
                         status_code=hresult.get("status_code"))
                if not hresult.get("ok"):
                    # Transferência externa falhou — finalizamos mesmo assim
                    # (decisão de produto). O atendimento é dado por encerrado
                    # e a IA é pausada; o operador acompanha pela inbox.
                    log.warning("webhook.handoff.external_failed_closing_anyway",
                                tenant=tenant_id, error=hresult.get("error"),
                                status_code=hresult.get("status_code"))
                # Finaliza o atendimento (closed_at) e pausa a IA — SEMPRE que
                # houve handoff, independente do sucesso da API externa.
                try:
                    from services.conversation_state import auto_pause_after_handoff
                    await auto_pause_after_handoff(
                        tenant_id, phone,
                        pause_minutes=channel_pause_minutes,
                    )
                except Exception as exc:  # noqa: BLE001
                    log.warning("webhook.handoff.autopause_failed",
                                tenant=tenant_id, exc=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.error("webhook.handoff.dispatch_failed", tenant=tenant_id, exc=str(exc))

        # ── Fim de atendimento sinalizado pelo agente ([[END]]) ──────────────
        # Cliente se despediu sem pedido pendente. Encerra a sessão de forma
        # determinística (closed_at, sem pausar). Só quando NÃO houve handoff —
        # o handoff já finaliza via auto_pause_after_handoff.
        if not handoff_was_executed and final_state.get("end_conversation"):
            try:
                from services.conversation_state import end_session
                await end_session(
                    tenant_id, phone,
                    by="agent:end_marker",
                    reason="agent_end_conversation",
                    clear_history=True,
                )
                log.info("webhook.session.ended_by_agent", tenant=tenant_id,
                         phone_prefix=phone[:4])
            except Exception as exc:  # noqa: BLE001
                log.warning("webhook.session.end_failed",
                            tenant=tenant_id, exc=str(exc))

        await _deliver_response(
            callback_url,
            {
                "phone": phone,
                "session_id": session_id,
                "message": response_text,
                "tenant_id": tenant_id,
            },
        )

        # Ofertas pré-handoff: enviadas COMO MENSAGENS SEPARADAS,
        # depois da mensagem de transferência principal.
        if handoff_was_executed:
            async def _send_text(text: str) -> None:
                await _deliver_response(
                    callback_url,
                    {
                        "phone": phone,
                        "session_id": session_id,
                        "message": text,
                        "tenant_id": tenant_id,
                        "kind": "pre_handoff_offer",
                    },
                )
            await _send_post_handoff_messages(
                tenant_id,
                phone=phone,
                cart=_cart_for_summary(final_state),
                channel_cfg=handoff_cfg,
                text_sender=_send_text,
            )

        log.info(
            "task.done",
            tenant=tenant_id,
            session=session_id,
            skill=skill_used,
            elapsed_s=round(elapsed, 2),
        )

    except Exception as exc:  # noqa: BLE001
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="error").inc()
        log.error("task.failed", tenant=tenant_id, session=session_id, exc=str(exc))
        trace_error = str(exc)
        # Best-effort: notify the callback with an error payload
        try:
            await _deliver_response(
                callback_url,
                {
                    "phone": phone,
                    "session_id": session_id,
                    "message": "Ocorreu um erro no atendimento. Por favor, tente novamente.",
                    "tenant_id": tenant_id,
                    "error": True,
                },
            )
        except Exception:
            pass
        raise
    finally:
        from services.agent_traces import persist_trace
        await persist_trace(
            schema_name=schema_name,
            session_key=session_id,
            phone=phone,
            message_in=current_message,
            final_state=final_state,
            latency_ms=int((time.monotonic() - t0) * 1000),
            error=trace_error,
        )


# ── Broker bundled task (debounce — agrupa mensagens picadas) ───────────────

@celery_app.task(name="process_bundled_message", bind=True, max_retries=0)
def process_bundled_message(
    self,
    tenant_id: str,
    integration_id: str,
    bundle_key: str,
    scheduled_for_ts: float,
) -> None:
    """
    Debounce processor.

    Cada mensagem que chega agenda esta task com countdown=window. Quando
    rodamos:
      - lemos `last_seen` do Redis. Se for > nosso `scheduled_for_ts`,
        significa que chegou mensagem nova depois de nós — então DESISTIMOS
        (uma task mais recente vai processar o bundle completo).
      - caso contrário, pegamos todas as mensagens do buffer, concatenamos
        com quebra de linha, e disparamos o fluxo do agente com o texto
        combinado.
    """
    asyncio.run(_run_bundle(
        tenant_id=tenant_id,
        integration_id=integration_id,
        bundle_key=bundle_key,
        scheduled_for_ts=scheduled_for_ts,
    ))


async def _run_bundle(
    tenant_id: str,
    integration_id: str,
    bundle_key: str,
    scheduled_for_ts: float,
) -> None:
    from db.postgres import init_pool
    from db.redis_client import get_redis, init_redis
    import json as _json

    await init_pool()
    await init_redis()
    redis = get_redis()

    last_seen_raw = await redis.get(f"{bundle_key}:last_seen")
    try:
        last_seen = float(last_seen_raw) if last_seen_raw else 0.0
    except (ValueError, TypeError):
        last_seen = 0.0

    # Outra mensagem chegou depois desta task ser agendada → desiste.
    # A task agendada por aquela mensagem mais recente vai processar tudo.
    if last_seen > scheduled_for_ts:
        log.info("bundle.skipped_newer_arrived",
                 bundle_key=bundle_key,
                 scheduled=scheduled_for_ts, last_seen=last_seen)
        return

    # Pega tudo do buffer e limpa
    items_raw = await redis.lrange(bundle_key, 0, -1)
    await redis.delete(bundle_key, f"{bundle_key}:last_seen")

    if not items_raw:
        return

    items = [_json.loads(i) for i in items_raw]
    combined_message = "\n".join(it["msg"].strip()
                                 for it in items if (it.get("msg") or "").strip())

    # Usa o canonical_input da última mensagem como base e sobrescreve message
    base_input = items[-1].get("input") or {}
    last_event_id = items[-1].get("event_id") or ""

    # Se o último item carrega mídia (áudio/imagem), preservamos a mídia
    # mesmo sem texto — o ingest_media node vai transcrever/descrever.
    has_media = bool(base_input.get("media_type"))
    if not combined_message and not has_media:
        # Nada útil para processar
        return

    canonical_input = {**base_input, "message": combined_message}

    log.info("bundle.processing",
             bundle_key=bundle_key,
             count=len(items),
             combined_len=len(combined_message))

    await _run_broker_flow(
        tenant_id=tenant_id,
        integration_id=integration_id,
        raw_event_id=last_event_id,
        canonical_input=canonical_input,
    )


# ── Broker task (universal webhook flow) ─────────────────────────────────────

@celery_app.task(name="process_broker_message", bind=True, max_retries=0)
def process_broker_message(
    self,
    tenant_id: str,
    integration_id: str,
    raw_event_id: str,
    canonical_input: dict,
) -> None:
    """
    Runs the agent for a webhook event ingested via /hooks/*.

    After the agent finishes:
      - Applies the integration's reply_body_template against
        {input, reply, phone, message, name, session_id, event_id}
      - If reply_mode == 'forward', POSTs the body to reply_url.
      - Updates the broker_raw_events row with the final canonical payload.
    """
    asyncio.run(_run_broker_flow(
        tenant_id=tenant_id,
        integration_id=integration_id,
        raw_event_id=raw_event_id,
        canonical_input=canonical_input,
    ))


async def _run_broker_flow(
    tenant_id: str,
    integration_id: str,
    raw_event_id: str,
    canonical_input: dict,
) -> None:
    from db.postgres import get_db_conn, init_pool
    from db.redis_client import get_redis, init_redis
    from agents.graph_builder import build_graph_for_tenant, TenantConfig
    from services.llm_config import load_tenant_llm_config
    from services import broker as broker_svc

    await init_pool()
    await init_redis()
    redis = get_redis()

    async with get_db_conn() as conn:
        tenant = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1", tenant_id,
        )
        integration = await conn.fetchrow(
            "SELECT * FROM public.tenant_integrations WHERE id = $1", integration_id,
        )

    if not tenant or not integration:
        log.error("broker.flow.missing_records", tenant=tenant_id, integration=integration_id)
        return

    schema_name = tenant["schema_name"]
    phone = canonical_input.get("phone") or ""
    # Sanitize phone: keep only digits (Z-API/WhatsApp formats add ":21@s.whatsapp.net")
    phone_clean = "".join(c for c in phone if c.isdigit())[:20] or "unknown"
    message = canonical_input.get("message") or ""
    session_id = canonical_input.get("session_id") or phone_clean

    # ── Ciclo de vida da sessão ─────────────────────────────────────────────
    # 1) Reseta a sessão se o cliente voltou após handoff (closed_at marcado
    #    e janela de pausa expirada).
    # 2) Encerra agora se a msg casa com palavra-chave configurada no canal.
    async def _send_via_broker(text: str) -> None:
        """Envia texto pelo reply_url do gateway (mesmo caminho do reply normal)."""
        from services import broker as broker_svc, bot_echo
        # Fingerprint do que o BOT mandou: o eco de saída do gateway será
        # reconhecido como "próprio bot" (não pausa a IA). Ver bot_echo.py.
        await bot_echo.remember(tenant_id, phone_clean, text)
        ctx = {
            "input": canonical_input, "reply": text,
            "phone": phone, "message": message,
            "name": canonical_input.get("name"),
            "session_id": session_id, "event_id": raw_event_id,
        }
        template = integration["reply_body_template"] or {}
        body = (broker_svc.apply_mapping(template, ctx) if template
                else {"reply": text})
        if integration["reply_mode"] == "forward" and integration["reply_url"]:
            method = (integration.get("reply_method") or "POST").upper()
            headers = {str(k): str(v) for k, v in
                       (integration.get("reply_headers") or {}).items() if k and v}
            async with httpx.AsyncClient(timeout=15) as client:
                await client.request(method, integration["reply_url"],
                                     json=body, headers=headers)

    # 0) Pausa da IA: se um humano assumiu a conversa (ai_paused + janela ativa),
    #    o bot ignora 100% das mensagens — inclusive as picadas (bundled). Espelha
    #    o curto-circuito que o webhook nativo já faz (routers/webhook.py:58).
    #    SPEC 05 §"Não bypassar is_ai_paused". Sem este gate, o broker respondia
    #    durante a janela de handoff e re-finalizava pedidos antigos (o histórico
    #    Redis vazava de volta pro LLM porque o reset não roda enquanto pausado).
    try:
        from services.conversation_state import is_ai_paused
        paused, pause_reason = await is_ai_paused(tenant_id, phone_clean)
        if paused:
            log.info("broker.flow.skipped.ai_paused",
                     tenant=tenant_id, phone=phone_clean[:4], reason=pause_reason)
            try:
                async with get_db_conn() as conn:
                    await conn.execute(
                        "UPDATE public.broker_raw_events "
                        "SET status='processed', canonical_event='skipped.ai_paused', "
                        "    attempts=attempts+1, processed_at=NOW() "
                        "WHERE id=$1",
                        raw_event_id,
                    )
            except Exception:
                pass
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("broker.flow.pause_check_failed", tenant=tenant_id, exc=str(exc))

    try:
        ended = await _maybe_close_or_reset_session(
            tenant_id, phone_clean, message,
            integration.get("session_config") or {},
            text_sender=_send_via_broker,
        )
        if ended:
            # Persist como evento processado (skip do agente)
            try:
                async with get_db_conn() as conn:
                    await conn.execute(
                        "UPDATE public.broker_raw_events "
                        "SET status='processed', canonical_event='session.closed_by_keyword', "
                        "    attempts=attempts+1, processed_at=NOW() "
                        "WHERE id=$1",
                        raw_event_id,
                    )
            except Exception:
                pass
            return
    except Exception as exc:  # noqa: BLE001
        log.warning("broker.flow.session_lifecycle_failed",
                    tenant=tenant_id, exc=str(exc))

    # Safety net: se canonical_input não trouxer mídia, tenta auto-detectar
    # no payload bruto do evento (cobre o caso de canonical antigo ou
    # bundling com versão anterior do código).
    if not canonical_input.get("media_type") and raw_event_id:
        try:
            from services.media_detect import detect_media
            async with get_db_conn() as conn:
                raw_row = await conn.fetchrow(
                    "SELECT payload FROM public.broker_raw_events WHERE id = $1",
                    raw_event_id,
                )
            if raw_row and raw_row["payload"]:
                detected = detect_media(raw_row["payload"])
                if detected:
                    canonical_input.update(detected)
                    log.info("broker.flow.media_recovered_from_raw",
                             media_type=detected["media_type"],
                             event_id=raw_event_id)
        except Exception as exc:
            log.warning("broker.flow.media_recovery_failed", exc=str(exc))

    # Load active skills + LLM config
    async with get_db_conn() as conn:
        await conn.execute(f"SET search_path = {schema_name}, public")
        rows = await conn.fetch(
            "SELECT skill_name FROM skills_config WHERE ativo = TRUE"
        )
        active_skills = [r["skill_name"] for r in rows]

    llm_cfg = await load_tenant_llm_config(tenant_id)

    tenant_cfg = TenantConfig(
        tenant_id=tenant_id,
        schema_name=schema_name,
        callback_url="",   # not used; we control the reply ourselves
        skills_active=active_skills,
        **llm_cfg,
    )

    graph = build_graph_for_tenant(tenant_cfg, redis)

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

    # Pass-through de mídia mapeada pelo broker (Z-API/WA Cloud → canonical)
    if canonical_input.get("media_type"):
        initial_state.update({
            "media_type": canonical_input.get("media_type"),
            "media_mime": canonical_input.get("media_mime"),
            "media_url":  canonical_input.get("media_url"),
            "media_id":   canonical_input.get("media_id"),
            "media_b64":  canonical_input.get("media_b64"),
        })

    config = {"configurable": {"thread_id": session_id}}
    t0 = time.monotonic()
    skill_used = "broker"
    reply_text = ""
    error: str | None = None
    final_state: dict | None = None

    _begin_token_turn(tenant_id, "")

    try:
        final_state = await graph.ainvoke(initial_state, config=config)
        skill_used = final_state.get("selected_skill", "unknown")
        reply_text = final_state.get("final_response", "")
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="ok").inc()
        LATENCY.labels(tenant_id=tenant_id, skill=skill_used).observe(time.monotonic() - t0)
    except Exception as exc:
        error = str(exc)
        reply_text = "Ocorreu um erro no atendimento. Por favor, tente novamente."
        CONV_TOTAL.labels(tenant_id=tenant_id, skill=skill_used, status="error").inc()
        log.error("broker.flow.agent_failed", tenant=tenant_id, exc=error)

    # ── Handoff p/ atendente humano (balcão) ─────────────────────────────────
    # Roda DEPOIS do agente. Decide se transfere com base em:
    #   - escalate=True sinalizado pelo agente (guardrails, analyst, etc.)
    #   - palavra-chave configurada batendo na mensagem do cliente
    # Se transferir, sobrescreve reply_text pela mensagem de transferência
    # e dispara o POST para a API externa (ClickMassa / TalkFarma / ...).
    # handoff_config pode vir como dict (asyncpg JSONB) ou string (legacy)
    handoff_cfg = integration.get("handoff_config") or {}
    if isinstance(handoff_cfg, str):
        try:
            import json as _json
            handoff_cfg = _json.loads(handoff_cfg) or {}
        except Exception:
            handoff_cfg = {}
    agent_escalate = bool(final_state.get("escalate")) if final_state else False
    # Trigger determinístico: pedido fechado nesta task força handoff
    # mesmo sem o LLM emitir [[ESCALATE]]. Independe do prompt.
    order_close = _extract_order_close_signal(final_state)
    order_just_finalized = order_close is not None
    if order_just_finalized:
        log.info("broker.flow.order_finalized.trigger",
                 tenant=tenant_id, order_id=str(order_close.get("id"))[:8])
    handoff_result: dict | None = None
    handoff_was_executed = False
    try:
        from services.handoff import should_handoff, transfer_to_human
        do_handoff, reason = should_handoff(
            handoff_cfg,
            agent_escalate=(agent_escalate or order_just_finalized),
            user_message=message,
        )
        if order_just_finalized and not agent_escalate:
            reason = "order_finalized"
        if do_handoff:
            handoff_was_executed = True
            log.info("broker.flow.handoff_triggered",
                     tenant=tenant_id, reason=reason, phone_prefix=phone_clean[:4],
                     agent_escalate=agent_escalate,
                     order_finalized=order_just_finalized)
            # Usa a resposta do agente como mensagem de transferência quando:
            # (a) o agente escalou explicitamente OU
            # (b) a tool finalizar_pedido rodou (resposta = recibo do pedido).
            use_agent_reply = bool(reply_text) and (agent_escalate or order_just_finalized)
            # Idem ao webhook flow: override apenas quando reason=sentiment
            # E o tenant configurou `transfer_message` na capability.
            sentiment_msg = await _resolve_sentiment_transfer_message(
                tenant_id, final_state,
            )
            if sentiment_msg:
                reply_text = sentiment_msg
                use_agent_reply = False
            handoff_result = await transfer_to_human(
                handoff_cfg, phone=phone_clean,
                custom_message=(reply_text if (use_agent_reply or sentiment_msg) else None),
            )
            if not use_agent_reply and not sentiment_msg:
                reply_text = (handoff_cfg.get("transfer_message")
                              or "Estou te transferindo para um atendente agora. Um momento, por favor.")
            skill_used = "handoff"
            # `transfer_to_human` já entregou `reply_text` ao cliente (POST direto
            # no gateway). Fingerprint para que o eco dessa msg não seja lido como
            # resposta humana e pause a IA indevidamente.
            try:
                from services import bot_echo
                await bot_echo.remember(tenant_id, phone_clean, reply_text)
            except Exception:
                pass
            if not (handoff_result and handoff_result.get("ok")):
                # Transferência externa falhou — finalizamos mesmo assim
                # (decisão de produto): o atendimento é encerrado e a IA pausada.
                log.warning("broker.handoff.external_failed_closing_anyway",
                            tenant=tenant_id,
                            error=(handoff_result or {}).get("error"),
                            status_code=(handoff_result or {}).get("status_code"))
            # Finaliza o atendimento (closed_at) e pausa a IA — SEMPRE que houve
            # handoff, independente do sucesso da API externa.
            try:
                from services.conversation_state import auto_pause_after_handoff
                pause_min = int(integration.get("handoff_pause_minutes") or 240)
                await auto_pause_after_handoff(
                    tenant_id, phone_clean,
                    pause_minutes=pause_min,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("broker.handoff.autopause_failed", exc=str(exc))
    except Exception as exc:
        log.error("broker.flow.handoff_dispatch_failed", error=str(exc))
        handoff_result = {"ok": False, "error": f"Erro no dispatcher de handoff: {exc}",
                          "status_code": None, "response": None}

    # ── Fim de atendimento sinalizado pelo agente ([[END]]) ──────────────────
    # Cliente se despediu sem pedido pendente. Encerra a sessão de forma
    # determinística (closed_at, sem pausar). Só quando NÃO houve handoff — o
    # handoff já finaliza via auto_pause_after_handoff.
    if not handoff_was_executed and final_state and final_state.get("end_conversation"):
        try:
            from services.conversation_state import end_session
            await end_session(
                tenant_id, phone_clean,
                by="agent:end_marker",
                reason="agent_end_conversation",
                clear_history=True,
            )
            log.info("broker.session.ended_by_agent", tenant=tenant_id,
                     phone_prefix=phone_clean[:4])
        except Exception as exc:  # noqa: BLE001
            log.warning("broker.session.end_failed", tenant=tenant_id, exc=str(exc))

    from services.agent_traces import persist_trace
    await persist_trace(
        schema_name=schema_name,
        session_key=session_id,
        phone=phone_clean,
        message_in=message,
        final_state=final_state,
        latency_ms=int((time.monotonic() - t0) * 1000),
        error=error,
    )

    # Build reply body from template
    reply_context = {
        "input": canonical_input,
        "reply": reply_text,
        "phone": phone,
        "message": message,
        "name": canonical_input.get("name"),
        "session_id": session_id,
        "event_id": raw_event_id,
    }
    template = integration["reply_body_template"] or {}
    reply_body = (
        broker_svc.apply_mapping(template, reply_context)
        if template else {"reply": reply_text}
    )

    # Forward to external URL if configured — captura status + body
    forward_status: int | None = None
    forward_response: dict | None = None
    forward_error: str | None = None

    # IMPORTANTE: quando o handoff foi executado, `transfer_to_human` JÁ entregou
    # a mensagem de transferência ao cliente (POST no endpoint ClickMassa com o
    # `body`). Forwardear `reply_text` de novo aqui pelo reply_url duplicaria a
    # mensagem na conversa do cliente. Então pulamos o forward principal nesse
    # caso — as ofertas pré-handoff abaixo continuam saindo normalmente.
    if handoff_was_executed:
        log.info("broker.flow.skip_reply_forward_after_handoff", tenant=tenant_id)
    elif integration["reply_mode"] == "forward" and integration["reply_url"]:
        method = (integration.get("reply_method") or "POST").upper()
        headers = {str(k): str(v) for k, v in
                   (integration.get("reply_headers") or {}).items() if k and v}
        try:
            from services import bot_echo
            await bot_echo.remember(tenant_id, phone_clean, reply_text)
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.request(
                    method, integration["reply_url"],
                    json=reply_body, headers=headers,
                )
            forward_status = resp.status_code
            try:
                forward_response = resp.json()
            except Exception:
                forward_response = {"_text": resp.text[:2000]}

            if 200 <= resp.status_code < 300:
                log.info("broker.flow.forwarded",
                         tenant=tenant_id, url=integration["reply_url"],
                         status=resp.status_code)
            else:
                forward_error = f"Gateway externo retornou {resp.status_code}"
                log.warning("broker.flow.forward_bad_status",
                            tenant=tenant_id, url=integration["reply_url"],
                            status=resp.status_code,
                            response_preview=str(forward_response)[:300])
        except Exception as exc:
            forward_error = f"Falha ao conectar no destino: {exc}"
            log.warning("broker.flow.forward_failed",
                        tenant=tenant_id, url=integration["reply_url"], exc=str(exc))

    # Ofertas pré-handoff: enviadas COMO MENSAGENS SEPARADAS,
    # depois do reply principal já ter sido forward-eado.
    if handoff_was_executed and integration["reply_mode"] == "forward" and integration["reply_url"]:
        async def _send_text(text: str) -> None:
            from services import bot_echo
            await bot_echo.remember(tenant_id, phone_clean, text)
            ctx = {**reply_context, "reply": text, "_kind": "pre_handoff_offer"}
            body = (
                broker_svc.apply_mapping(template, ctx) if template else {"reply": text}
            )
            method = (integration.get("reply_method") or "POST").upper()
            headers = {str(k): str(v) for k, v in
                       (integration.get("reply_headers") or {}).items() if k and v}
            async with httpx.AsyncClient(timeout=15) as client:
                await client.request(method, integration["reply_url"],
                                     json=body, headers=headers)

        await _send_post_handoff_messages(
            tenant_id,
            phone=phone_clean,
            cart=_cart_for_summary(final_state),
            channel_cfg=handoff_cfg,
            text_sender=_send_text,
        )

    # Persist final state (com info do forward, se houve)
    canonical_combined = {**reply_context, "_reply_body": reply_body, "_error": error}
    if handoff_result is not None:
        canonical_combined["_handoff"] = handoff_result
    final_status = (
        "failed" if (error or forward_error) else "processed"
    )
    final_error = error or forward_error

    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.broker_raw_events "
            "SET status=$2, canonical_event='agent.message', canonical_payload=$3, "
            "    attempts=attempts+1, processed_at=NOW(), error=$4, "
            "    forward_url=$5, forward_status_code=$6, forward_response=$7 "
            "WHERE id=$1",
            raw_event_id,
            final_status,
            canonical_combined,
            final_error,
            integration["reply_url"] if integration["reply_mode"] == "forward" else None,
            forward_status,
            forward_response,
        )
