"""Fingerprint das mensagens que o PRÓPRIO bot envia ao cliente.

Problema (o "dilema"): no WhatsApp via gateways que ecoam mensagens de saída
(TalkFarma/ClickMassa/WAHA/Evolution), tanto o bot quanto o atendente humano
enviam pelo mesmo número. O gateway devolve AMBAS ao nosso webhook marcadas
como "saída" (ex.: `fromMe=true`). Se pausássemos a IA em todo eco de saída,
o bot se auto-pausaria a cada resposta que ele mesmo dá.

Solução: antes de o bot mandar um texto, registramos um fingerprint efêmero
(`remember`). Quando um eco de saída chega no ingest do broker, consultamos
`is_echo`: se casar com algo que o bot acabou de mandar, é o próprio bot
(ignora); senão, é o atendente humano (pausa a IA). O fingerprint é
consumido na primeira consulta (one-shot) e expira sozinho via TTL.

Ver SPEC 05 §"Detecção de resposta humana" e
[[transferencia-handoff-escalate-ofertas-pre-handoff-fluxo-completo]].
"""

from __future__ import annotations

import hashlib
import re

import structlog

log = structlog.get_logger()

# Janela durante a qual um texto enviado pelo bot ainda é reconhecido como eco
# próprio. 5 min cobre folgadamente a latência de entrega/eco do gateway sem
# manter fingerprints velhos que poderiam mascarar uma resposta humana.
_TTL_SECONDS = 300

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Normaliza o texto para casar bot-send com o eco do gateway.

    Lowercase + colapsa espaços + strip. Conservador de propósito: gateways
    costumam preservar o corpo do texto, mas podem variar espaçamento/typo de
    quebra de linha.
    """
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _key(tenant_id: str, phone: str, text: str) -> str:
    digest = hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()
    phone_clean = "".join(c for c in (phone or "") if c.isdigit())[:20] or "unknown"
    return f"bot_echo:{tenant_id}:{phone_clean}:{digest}"


async def remember(tenant_id: str, phone: str, text: str) -> None:
    """Marca que o bot acabou de enviar `text` para `phone`. NUNCA levanta."""
    if not text or not text.strip():
        return
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        await redis.setex(_key(tenant_id, phone, text), _TTL_SECONDS, "1")
    except Exception as exc:  # noqa: BLE001
        log.warning("bot_echo.remember_failed",
                    tenant=tenant_id, phone=(phone or "")[:4], exc=str(exc))


async def is_echo(tenant_id: str, phone: str, text: str) -> bool:
    """True se `text` casa com uma mensagem que o bot mandou há pouco.

    Consome o fingerprint (one-shot) para que um humano que reenvie EXATAMENTE
    o mesmo texto logo em seguida ainda seja detectado. Em erro de Redis,
    retorna False (conservador: na dúvida trata como humano e pausa — coerente
    com "atendente humano > bot").
    """
    if not text or not text.strip():
        return False
    key = _key(tenant_id, phone, text)
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        try:
            val = await redis.getdel(key)
        except AttributeError:
            # redis-py < 4.2 sem getdel: fallback get + delete
            val = await redis.get(key)
            if val is not None:
                await redis.delete(key)
        return val is not None
    except Exception as exc:  # noqa: BLE001
        log.warning("bot_echo.is_echo_failed",
                    tenant=tenant_id, phone=(phone or "")[:4], exc=str(exc))
        return False
