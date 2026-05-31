"""
services/delivery_guard.py

MVP: detecta quando o agente promete "frete grátis" sem que o tenant tenha
NENHUMA regra de frete grátis cadastrada. Versão futura pode validar contra
CEP/subtotal específicos.

Async porque consulta `public.tenant_shipping_rules` (tabela compartilhada,
não no schema do tenant). Cache de 60s por tenant pra não bater no DB toda
resposta.
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

# Negação: se tiver "frete grátis acima de R$ X" e a oferta existe na regra,
# essa é uma afirmação válida — exigiria validação de subtotal.
# Pra MVP: se tenant tem alguma regra com gratis_acima, presumimos legítimo
# (o agente DEVE estar respeitando a condição cadastrada).

# Cache por tenant
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


async def tenant_allows_free_delivery(tenant_id: str | None) -> bool:
    """True se o tenant tem AO MENOS UMA regra de frete grátis cadastrada
    (gratis_acima IS NOT NULL e > 0). Cacheado 60s.
    """
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
                SELECT 1 FROM public.tenant_shipping_rules
                 WHERE tenant_id = $1
                   AND active = TRUE
                   AND gratis_acima IS NOT NULL
                   AND gratis_acima > 0
                 LIMIT 1
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
) -> list[dict]:
    """Retorna [{"reason": "free_delivery_not_configured"}] ou []."""
    if not has_free_delivery_claim(response_text):
        return []
    if await tenant_allows_free_delivery(tenant_id):
        return []
    return [{"reason": "free_delivery_not_configured"}]


def build_correction_message(issues: list[dict]) -> str:
    return (
        "Vou confirmar a política de frete com o atendente antes de bater o "
        "martelo no valor. Um momento!"
    )
