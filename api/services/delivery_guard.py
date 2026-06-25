"""
services/delivery_guard.py

Guard determinístico de frete. Dois níveis de defesa:

1. **Cruzamento com o orçamento do turno** (`quote`): quando o tool `calcular_frete`
   roda, ele grava em `cart["_shipping_quote_this_turn"]` o que REALMENTE calculou
   (valor, se é grátis, ou "fora de área"). Aqui cruzamos a resposta do agente
   contra essa verdade — pega o agente prometendo "frete grátis" quando o subtotal
   não atingiu o mínimo, ou prometendo entrega/valor para um CEP fora da área.

2. **Fallback MVP** (sem `quote`): se o tool não rodou e a resposta menciona
   "frete grátis"/"entrega grátis" e o tenant NÃO tem nenhuma regra de frete
   grátis cadastrada, flagga.

Async porque o fallback consulta `public.tenant_shipping_rules` (tabela
compartilhada). Cache de 60s por tenant.
"""
from __future__ import annotations

import re
import time
import unicodedata


_FREE_DELIVERY_PATTERNS = [
    r"\bfrete\s+gr[áa]tis\b",
    r"\bentrega\s+gr[áa]tis\b",
    r"\bgratuit[ao]\s+(o\s+)?frete\b",
    r"\bsem\s+custo\s+de\s+entrega\b",
    r"\bsem\s+frete\b",
]

# "frete/entrega ... R$ N" ou "R$ N ... de frete/entrega" — usado para detectar
# o agente cotando um valor de entrega quando o tool não conseguiu cotar.
_FRETE_PRICE_PATTERNS = [
    r"\b(frete|entrega)\b[^.\n]{0,40}r\$\s*\d",
    r"r\$\s*\d[^.\n]{0,40}\b(de\s+)?(frete|entrega)\b",
]

# Cache por tenant (fallback MVP)
_CACHE: dict[str, tuple[float, bool]] = {}
_CACHE_TTL_SECONDS = 60.0


def _normalize(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def has_free_delivery_claim(response_text: str) -> bool:
    if not response_text:
        return False
    norm = _normalize(response_text)
    return any(re.search(p, norm) for p in _FREE_DELIVERY_PATTERNS)


def _mentions_frete_price(response_text: str) -> bool:
    if not response_text:
        return False
    norm = _normalize(response_text)
    return any(re.search(p, norm) for p in _FRETE_PRICE_PATTERNS)


async def tenant_allows_free_delivery(tenant_id: str | None) -> bool:
    """True se o tenant tem AO MENOS UMA regra de frete grátis cadastrada
    (gratis_acima > 0), em qualquer dos dois modelos. Cacheado 60s."""
    if not tenant_id:
        return False
    now = time.time()
    cached = _CACHE.get(tenant_id)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        from db.postgres import get_db_conn
        async with get_db_conn() as conn:
            row = await conn.fetchrow(
                """
                SELECT 1 WHERE EXISTS (
                    SELECT 1 FROM public.tenant_shipping_rules
                     WHERE tenant_id = $1 AND active = TRUE
                       AND gratis_acima IS NOT NULL AND gratis_acima > 0
                ) OR EXISTS (
                    SELECT 1 FROM public.tenant_shipping_distance_tiers
                     WHERE tenant_id = $1 AND active = TRUE
                       AND gratis_acima IS NOT NULL AND gratis_acima > 0
                )
                """,
                tenant_id,
            )
        allowed = bool(row)
    except Exception:
        # Falha aberta — não flagga se não conseguimos verificar
        allowed = True
    _CACHE[tenant_id] = (now, allowed)
    return allowed


async def detect_delivery_issues(
    response_text: str,
    *,
    tenant_id: str | None,
    quote: dict | None = None,
) -> list[dict]:
    """Retorna lista de issues (vazia = ok).

    Reasons possíveis:
      - "free_claimed_but_not_free"  → bot prometeu grátis, mas o orçamento do
                                        turno diz que NÃO é grátis (subtotal abaixo
                                        do mínimo, ou sem regra de grátis).
      - "delivery_unconfirmed"       → o tool não conseguiu cotar (fora de área /
                                        sem regra / CEP inválido), mas o bot afirmou
                                        frete/valor mesmo assim.
      - "free_delivery_not_configured" → fallback MVP (sem quote): "grátis" sem
                                        nenhuma regra de grátis cadastrada.
    """
    if not response_text:
        return []

    claim = has_free_delivery_claim(response_text)

    # ── Nível 1: cruza com o orçamento calculado neste turno ──────────────────
    if quote:
        kind = quote.get("kind")
        if kind in ("distance", "cep"):
            if claim and not quote.get("free"):
                return [{"reason": "free_claimed_but_not_free",
                         "threshold": quote.get("free_threshold")}]
            return []
        if kind in ("out_of_area", "no_rule", "invalid", "error"):
            # Tool não cotou → qualquer afirmação de frete/grátis é fabricação.
            if claim or _mentions_frete_price(response_text):
                return [{"reason": "delivery_unconfirmed", "kind": kind}]
            return []
        return []

    # ── Nível 2 (fallback MVP, sem quote): "grátis" sem regra ─────────────────
    if not claim:
        return []
    if await tenant_allows_free_delivery(tenant_id):
        return []
    return [{"reason": "free_delivery_not_configured"}]


def build_correction_message(issues: list[dict]) -> str:
    reasons = {i.get("reason") for i in issues}

    if "delivery_unconfirmed" in reasons:
        return (
            "Preciso confirmar o frete e a área de entrega para esse endereço "
            "com o atendente antes de bater o valor. Um momento!"
        )
    if "free_claimed_but_not_free" in reasons:
        thr = next((i.get("threshold") for i in issues
                    if i.get("reason") == "free_claimed_but_not_free"), None)
        if thr:
            return (
                f"Corrigindo: o frete grátis vale para compras acima de "
                f"R$ {float(thr):.2f}. Abaixo disso o frete é cobrado normalmente — "
                f"já te confirmo o valor certinho."
            )
        return (
            "Deixa eu confirmar a política de frete com o atendente antes de "
            "prometer frete grátis. Um momento!"
        )
    # free_delivery_not_configured (MVP)
    return (
        "Vou confirmar a política de frete com o atendente antes de bater o "
        "martelo no valor. Um momento!"
    )
