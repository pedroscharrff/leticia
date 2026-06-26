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

import time

import structlog

from db.postgres import get_db_conn
from services.bulario_repo import MIN_SIMILARITY, _normalize

log = structlog.get_logger()

# ── Léxico cacheado (princípios ativos + marcas de referência) ────────────────
# Usado pelo andaime de grounding (services/grounding_guard.py) para saber se um
# termo citado pela LLM fraca é um nome de medicamento conhecido. A base é GLOBAL
# e praticamente estática (ingestão offline do guia), então cacheamos em memória
# com TTL longo — não consulta o banco a cada turno do agente.
_LEXICON_TTL_S = 3600.0
_lexicon_cache: set[str] | None = None
_lexicon_loaded_at: float = 0.0


async def load_reference_lexicon() -> set[str]:
    """Conjunto normalizado de princípios ativos + marcas de referência conhecidos.

    Cacheado em memória (TTL `_LEXICON_TTL_S`). Fail-open: qualquer erro devolve o
    cache anterior (se houver) ou um set vazio — o detector que consome isso não
    dispara com léxico vazio, então nunca quebra o turno.
    """
    global _lexicon_cache, _lexicon_loaded_at
    now = time.monotonic()
    if _lexicon_cache is not None and (now - _lexicon_loaded_at) < _LEXICON_TTL_S:
        return _lexicon_cache

    try:
        async with get_db_conn() as conn:
            rows = await conn.fetch(
                "SELECT principio_ativo, nome_referencia "
                "FROM public.medicamentos_referencia"
            )
        lex: set[str] = set()
        for r in rows:
            for raw in (r.get("principio_ativo"), r.get("nome_referencia")):
                n = _normalize(raw or "")
                if n:
                    lex.add(n)
        _lexicon_cache = lex
        _lexicon_loaded_at = now
        log.info("referencia.lexicon.loaded", size=len(lex))
        return lex
    except Exception as exc:  # noqa: BLE001
        log.warning("referencia.lexicon.load_failed", exc=str(exc))
        return _lexicon_cache if _lexicon_cache is not None else set()


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


async def log_consulta(
    *,
    termo: str,
    rows: list[dict],
    tenant_id: str | None = None,
    session_id: str | None = None,
    skill: str | None = None,
) -> None:
    """
    Grava UMA linha de telemetria por consulta à base de referência (tool
    `consultar_medicamento_referencia`). Alimenta o painel "Consultas".

    Defensivo por contrato: telemetria NUNCA pode quebrar o turno do agente —
    qualquer falha é logada e engolida. Recebe as `rows` já devolvidas por
    `search_referencia`, então deriva (medicamentos casados, seções ativas
    consumidas) sem re-consultar o banco.

    JSONB: passamos listas Python direto — o codec de db/postgres.py já encoda;
    NÃO usar json.dumps aqui (evita double-encoding, cf. jsonb-double-encoding).
    """
    try:
        meds = [
            {
                "principio_ativo": r.get("principio_ativo"),
                "nome_referencia": r.get("nome_referencia"),
            }
            for r in rows
        ]
        # Slugs únicos de seções ativas efetivamente devolvidas (consumidas).
        secoes: list[str] = []
        for r in rows:
            for s in (r.get("secoes") or []):
                slug = s.get("secao")
                if slug and slug not in secoes:
                    secoes.append(slug)

        async with get_db_conn() as conn:
            await conn.execute(
                """
                INSERT INTO public.medicamentos_referencia_consultas
                    (tenant_id, session_id, skill, termo, encontrado,
                     num_resultados, medicamentos, secoes)
                VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8)
                """,
                tenant_id,
                session_id,
                skill,
                termo,
                bool(rows),
                len(rows),
                meds,
                secoes,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("referencia.log_consulta.failed", termo=termo, exc=str(exc))
