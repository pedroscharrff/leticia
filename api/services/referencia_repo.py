"""
Repositório da base curada de medicamentos de referência
(public.medicamentos_referencia + _secoes).

Diferente do bulario_repo (ANVISA, dinâmico): aqui a fonte é o "Guia de
Medicamentos Genéricos", ingerido offline. Responde o vínculo princípio
ativo ↔ marca de referência (original) — buraco que a ANVISA não cobre bem.

⚠️  Gate de curadoria: a info clínica da fonte é de 2001. Só seções com
`status='active'` (revisadas no painel superadmin) são devolvidas. O filtro é
determinístico AQUI — a tool/agente nunca veem seções `pending`/`disabled`.

Compartilha o MIN_SIMILARITY do bulario_repo para o mesmo rigor de match
(evita 'buspirona'→'espironolactona').
"""
from __future__ import annotations

import structlog

from db.postgres import get_db_conn
from services.bulario_repo import MIN_SIMILARITY, _normalize

log = structlog.get_logger()


async def search_referencia(
    termo: str, limit: int = 5, threshold: float = MIN_SIMILARITY
) -> list[dict]:
    """
    Busca por princípio ativo OU marca de referência (o cliente pode dar
    qualquer um dos dois), com corte de similaridade. Retorna o mapeamento +
    apenas as seções clínicas `active`.

    Cada item: {
        principio_ativo, nome_referencia, forma_farmaceutica, categoria,
        secoes: [{secao, conteudo}, ...]   # só as ativas, pode ser []
    }
    """
    norm = _normalize(termo)
    if not norm:
        return []

    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT mr.principio_ativo,
                   mr.nome_referencia,
                   mr.forma_farmaceutica,
                   mr.categoria,
                   COALESCE(
                       jsonb_agg(
                           jsonb_build_object('secao', s.secao, 'conteudo', s.conteudo)
                           ORDER BY s.secao
                       ) FILTER (WHERE s.id IS NOT NULL),
                       '[]'::jsonb
                   ) AS secoes
              FROM public.medicamentos_referencia mr
              LEFT JOIN public.medicamentos_referencia_secoes s
                     ON s.referencia_id = mr.id AND s.status = 'active'
             WHERE (mr.principio_ativo_norm % $1
                    AND similarity(mr.principio_ativo_norm, $1) >= $2)
                OR (mr.nome_referencia_norm % $1
                    AND similarity(mr.nome_referencia_norm, $1) >= $2)
                OR mr.principio_ativo_norm ILIKE '%' || $1 || '%'
                OR mr.nome_referencia_norm ILIKE '%' || $1 || '%'
             GROUP BY mr.id, mr.principio_ativo, mr.nome_referencia,
                      mr.forma_farmaceutica, mr.categoria, mr.principio_ativo_norm,
                      mr.nome_referencia_norm
             ORDER BY GREATEST(
                          similarity(mr.principio_ativo_norm, $1),
                          similarity(COALESCE(mr.nome_referencia_norm, ''), $1)
                      ) DESC
             LIMIT $3
            """,
            norm,
            threshold,
            limit,
        )

    out: list[dict] = []
    for r in rows:
        d = dict(r)
        secoes = d.get("secoes") or []
        # asyncpg pode devolver jsonb como str dependendo do codec; normaliza.
        if isinstance(secoes, str):
            import json
            try:
                secoes = json.loads(secoes)
            except ValueError:
                secoes = []
        d["secoes"] = secoes
        out.append(d)
    return out
