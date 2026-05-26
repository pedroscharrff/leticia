"""
Offers service — leitura de ofertas vigentes do tenant.

Consumido pelo hook de pré-handoff em api/workers/celery_app.py: antes de
transferir a conversa para um humano, se a capability `sales.pre_handoff_offers`
estiver ativa, anexamos as ofertas vigentes à mensagem do cliente.

Função pura, sem efeitos colaterais. Retorna [] quando não há ofertas vigentes —
o caller trata isso como no-op.
"""
from __future__ import annotations

from typing import Any

import structlog

from db.postgres import get_db_conn

log = structlog.get_logger()


async def get_active_offers(tenant_id: str, limit: int = 3) -> list[dict[str, Any]]:
    """Retorna até `limit` ofertas vigentes do tenant, ordenadas por prioridade.

    Vigente = `active = TRUE` E `valid_from <= now()` (ou NULL) E
    `valid_until >= now()` (ou NULL).
    """
    if not tenant_id or limit <= 0:
        return []

    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT id, title, description, valid_until, priority
              FROM public.offers
             WHERE tenant_id = $1
               AND active   = TRUE
               AND (valid_from  IS NULL OR valid_from  <= NOW())
               AND (valid_until IS NULL OR valid_until >= NOW())
             ORDER BY priority DESC, created_at DESC
             LIMIT $2
            """,
            tenant_id, limit,
        )

    return [dict(r) for r in rows]
