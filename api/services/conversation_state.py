"""
services/conversation_state.py

Estado por conversa (tenant × telefone). Permite:
  • Pausar a IA temporariamente ou indefinidamente
  • Encerrar atendimento manualmente
  • Pausa automática após handoff humano (default 4h por canal)

A leitura de "está pausado?" é cacheada em Redis por 30s para não bater no
DB a cada mensagem do cliente. A escrita invalida o cache.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import structlog

from db.postgres import get_db_conn
from db.redis_client import get_redis

log = structlog.get_logger()

# Cache em Redis: chave por (tenant, phone). TTL curto para refletir mudanças do portal rápido.
_CACHE_TTL = 30  # segundos


def _cache_key(tenant_id: str, phone: str) -> str:
    # phone sem caracteres especiais para evitar problemas no Redis
    phone_clean = "".join(c for c in phone if c.isalnum())
    return f"convstate:{tenant_id}:{phone_clean}"


# ── Public API ───────────────────────────────────────────────────────────────

async def get_state(tenant_id: str, phone: str) -> dict:
    """Retorna o estado atual da conversa (ou um dict default se não existe).

    Não usa cache — o cache é só para is_ai_paused() que roda a cada mensagem.
    """
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT tenant_id, phone, ai_paused, paused_until,
                   paused_by, paused_reason, closed_at, updated_at
              FROM public.conversation_state
             WHERE tenant_id = $1 AND phone = $2
            """,
            tenant_id, phone,
        )
    if not row:
        return {
            "tenant_id": tenant_id,
            "phone": phone,
            "ai_paused": False,
            "paused_until": None,
            "paused_by": None,
            "paused_reason": None,
            "closed_at": None,
            "updated_at": None,
        }
    return {
        "tenant_id":     str(row["tenant_id"]),
        "phone":         row["phone"],
        "ai_paused":     row["ai_paused"],
        "paused_until":  row["paused_until"].isoformat() if row["paused_until"] else None,
        "paused_by":     row["paused_by"],
        "paused_reason": row["paused_reason"],
        "closed_at":     row["closed_at"].isoformat() if row["closed_at"] else None,
        "updated_at":    row["updated_at"].isoformat() if row["updated_at"] else None,
    }


async def is_ai_paused(tenant_id: str, phone: str) -> tuple[bool, str | None]:
    """Resposta rápida e tolerante a falhas para o webhook.

    Retorna (paused, reason). Em caso de erro no Redis ou DB, retorna False —
    melhor responder que ficar travado.

    Uma conversa está "pausada" quando:
      • ai_paused=TRUE E (paused_until IS NULL OR paused_until > NOW())

    Nota: `closed_at` sozinho NÃO bloqueia mais (vira marcador de "sessão
    encerrada — resetar no próximo contato"). O bloqueio efetivo durante
    o handoff vem do `ai_paused` (auto_pause_after_handoff define ambos).
    """
    cache_hit = None
    try:
        redis = get_redis()
        cached = await redis.get(_cache_key(tenant_id, phone))
        if cached:
            cache_hit = json.loads(cached)
    except Exception as exc:
        log.warning("convstate.cache_read_failed", exc=str(exc))

    if cache_hit is not None:
        return bool(cache_hit.get("paused")), cache_hit.get("reason")

    try:
        state = await get_state(tenant_id, phone)
    except Exception as exc:
        log.warning("convstate.read_failed", tenant=tenant_id, exc=str(exc))
        return False, None

    paused = False
    reason: str | None = None

    if state["ai_paused"]:
        until = state["paused_until"]
        if until is None:
            paused = True
            reason = state.get("paused_reason") or "pausado_indefinidamente"
        else:
            until_dt = datetime.fromisoformat(until)
            if until_dt > datetime.now(timezone.utc):
                paused = True
                reason = state.get("paused_reason") or "pausado_temporario"

    # Best-effort cache write
    try:
        redis = get_redis()
        await redis.setex(
            _cache_key(tenant_id, phone),
            _CACHE_TTL,
            json.dumps({"paused": paused, "reason": reason}),
        )
    except Exception:
        pass

    return paused, reason


async def _invalidate_cache(tenant_id: str, phone: str) -> None:
    try:
        redis = get_redis()
        await redis.delete(_cache_key(tenant_id, phone))
    except Exception:
        pass


async def pause(
    tenant_id: str,
    phone: str,
    *,
    until_minutes: int | None,
    by: str,
    reason: str | None = None,
) -> dict:
    """Pausa a IA para esta conversa.

    Args:
        until_minutes: None = pausa indefinida; N = pausa por N minutos.
        by: identificador de quem pausou (email do operador ou 'auto:handoff').
        reason: motivo (livre, para auditoria).
    """
    paused_until = None
    if until_minutes and until_minutes > 0:
        from datetime import timedelta
        paused_until = datetime.now(timezone.utc) + timedelta(minutes=until_minutes)

    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.conversation_state
                (tenant_id, phone, ai_paused, paused_until, paused_by, paused_reason, updated_at)
            VALUES ($1, $2, TRUE, $3, $4, $5, NOW())
            ON CONFLICT (tenant_id, phone) DO UPDATE SET
                ai_paused     = TRUE,
                paused_until  = EXCLUDED.paused_until,
                paused_by     = EXCLUDED.paused_by,
                paused_reason = EXCLUDED.paused_reason,
                closed_at     = NULL,  -- pausar reabre se estava fechado
                updated_at    = NOW()
            """,
            tenant_id, phone, paused_until, by, reason,
        )
    await _invalidate_cache(tenant_id, phone)
    log.info("convstate.paused", tenant=tenant_id, phone=phone[:4],
             until=paused_until.isoformat() if paused_until else "indef",
             by=by, reason=reason)
    return await get_state(tenant_id, phone)


async def resume(tenant_id: str, phone: str, *, by: str) -> dict:
    """Reativa a IA para esta conversa (limpa ai_paused e closed_at)."""
    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.conversation_state
                (tenant_id, phone, ai_paused, paused_until, paused_by, paused_reason, closed_at, updated_at)
            VALUES ($1, $2, FALSE, NULL, $3, 'resumed', NULL, NOW())
            ON CONFLICT (tenant_id, phone) DO UPDATE SET
                ai_paused     = FALSE,
                paused_until  = NULL,
                paused_by     = EXCLUDED.paused_by,
                paused_reason = 'resumed',
                closed_at     = NULL,
                updated_at    = NOW()
            """,
            tenant_id, phone, by,
        )
    await _invalidate_cache(tenant_id, phone)
    log.info("convstate.resumed", tenant=tenant_id, phone=phone[:4], by=by)
    return await get_state(tenant_id, phone)


async def close(
    tenant_id: str,
    phone: str,
    *,
    by: str,
    keep_history: bool = True,
) -> dict:
    """Encerra o atendimento. A IA não responde mais até alguém dar resume.

    Se keep_history=False, também apaga o histórico Redis dessa sessão.
    """
    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.conversation_state
                (tenant_id, phone, ai_paused, paused_until, paused_by,
                 paused_reason, closed_at, updated_at)
            VALUES ($1, $2, TRUE, NULL, $3, 'encerrado_manual', NOW(), NOW())
            ON CONFLICT (tenant_id, phone) DO UPDATE SET
                ai_paused     = TRUE,
                paused_until  = NULL,
                paused_by     = EXCLUDED.paused_by,
                paused_reason = 'encerrado_manual',
                closed_at     = NOW(),
                updated_at    = NOW()
            """,
            tenant_id, phone, by,
        )
    await _invalidate_cache(tenant_id, phone)

    if not keep_history:
        try:
            redis = get_redis()
            # session_id padrão usado em todo o sistema é "{tenant_id}:{phone}"
            await redis.delete(f"hist:{tenant_id}:{phone}")
        except Exception:
            pass

    log.info("convstate.closed", tenant=tenant_id, phone=phone[:4], by=by,
             keep_history=keep_history)
    return await get_state(tenant_id, phone)


async def end_session(
    tenant_id: str,
    phone: str,
    *,
    by: str,
    reason: str | None = None,
    clear_history: bool = True,
) -> None:
    """Encerra a sessão SEM pausar a IA — o próximo contato vai detectar o
    marker `closed_at` e abrir um novo atendimento do zero.

    Diferença vs. `close()`: este NÃO seta ai_paused, então a próxima
    mensagem do cliente é processada (e dispara o reset automático).

    Usado quando o cliente envia a palavra-chave configurada no canal.
    """
    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.conversation_state
                (tenant_id, phone, ai_paused, paused_until, paused_by,
                 paused_reason, closed_at, updated_at)
            VALUES ($1, $2, FALSE, NULL, $3, $4, NOW(), NOW())
            ON CONFLICT (tenant_id, phone) DO UPDATE SET
                ai_paused     = FALSE,
                paused_until  = NULL,
                paused_by     = EXCLUDED.paused_by,
                paused_reason = EXCLUDED.paused_reason,
                closed_at     = NOW(),
                updated_at    = NOW()
            """,
            tenant_id, phone, by, reason or "ended",
        )
    await _invalidate_cache(tenant_id, phone)

    if clear_history:
        try:
            redis = get_redis()
            await redis.delete(f"hist:{tenant_id}:{phone}")
        except Exception:
            pass

    log.info("convstate.ended", tenant=tenant_id, phone=phone[:4],
             by=by, reason=reason)


# ── Helpers para o handoff automático ────────────────────────────────────────

async def auto_pause_after_handoff(
    tenant_id: str,
    phone: str,
    *,
    pause_minutes: int,
) -> None:
    """Chamada do worker quando transfer_to_human roda com sucesso.

    Pausa a IA pelo tempo configurado para que o atendente humano possa
    responder sem competir com o bot.
    """
    if pause_minutes <= 0:
        return
    try:
        # Marca também closed_at: quando o cliente voltar a falar depois do
        # fim da janela de pausa, o webhook/worker detecta o marker e abre
        # um novo atendimento do zero (reset_session) em vez de continuar
        # o histórico anterior.
        from datetime import timedelta
        paused_until = datetime.now(timezone.utc) + timedelta(minutes=pause_minutes)
        async with get_db_conn() as conn:
            await conn.execute(
                """
                INSERT INTO public.conversation_state
                    (tenant_id, phone, ai_paused, paused_until,
                     paused_by, paused_reason, closed_at, updated_at)
                VALUES ($1, $2, TRUE, $3, 'auto:handoff',
                        'atendente_humano_assumiu', NOW(), NOW())
                ON CONFLICT (tenant_id, phone) DO UPDATE SET
                    ai_paused     = TRUE,
                    paused_until  = EXCLUDED.paused_until,
                    paused_by     = 'auto:handoff',
                    paused_reason = 'atendente_humano_assumiu',
                    closed_at     = NOW(),
                    updated_at    = NOW()
                """,
                tenant_id, phone, paused_until,
            )
        await _invalidate_cache(tenant_id, phone)
        log.info("convstate.auto_pause_after_handoff",
                 tenant=tenant_id, phone=phone[:4],
                 until=paused_until.isoformat())
    except Exception as exc:
        log.warning("convstate.auto_pause_failed",
                    tenant=tenant_id, phone=phone[:4], exc=str(exc))


async def reset_session(
    tenant_id: str,
    phone: str,
    *,
    by: str,
    reason: str | None = None,
) -> None:
    """Zera a sessão da conversa: limpa pausa, closed_at e o histórico Redis.

    Usado quando o cliente envia palavra-chave de encerramento OU quando volta
    a falar após um handoff (closed_at marcado e janela de pausa expirada) —
    o próximo atendimento começa do zero.
    """
    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.conversation_state
                (tenant_id, phone, ai_paused, paused_until, paused_by,
                 paused_reason, closed_at, updated_at)
            VALUES ($1, $2, FALSE, NULL, $3, $4, NULL, NOW())
            ON CONFLICT (tenant_id, phone) DO UPDATE SET
                ai_paused     = FALSE,
                paused_until  = NULL,
                paused_by     = EXCLUDED.paused_by,
                paused_reason = EXCLUDED.paused_reason,
                closed_at     = NULL,
                updated_at    = NOW()
            """,
            tenant_id, phone, by, reason or "session_reset",
        )
    await _invalidate_cache(tenant_id, phone)

    try:
        redis = get_redis()
        # session_id padrão é "{tenant_id}:{phone}" (LangGraph thread_id)
        await redis.delete(f"hist:{tenant_id}:{phone}")
    except Exception:
        pass

    log.info("convstate.reset", tenant=tenant_id, phone=phone[:4],
             by=by, reason=reason)


async def list_recent(
    tenant_id: str,
    *,
    limit: int = 100,
    only_paused: bool = False,
) -> list[dict]:
    """Lista os estados de conversa mais recentes do tenant (para o portal)."""
    where = "WHERE tenant_id = $1"
    if only_paused:
        where += " AND ai_paused = TRUE"
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT tenant_id, phone, ai_paused, paused_until, paused_by,
                   paused_reason, closed_at, updated_at
              FROM public.conversation_state
              {where}
             ORDER BY updated_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )
    return [
        {
            "phone":         r["phone"],
            "ai_paused":     r["ai_paused"],
            "paused_until":  r["paused_until"].isoformat() if r["paused_until"] else None,
            "paused_by":     r["paused_by"],
            "paused_reason": r["paused_reason"],
            "closed_at":     r["closed_at"].isoformat() if r["closed_at"] else None,
            "updated_at":    r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]
