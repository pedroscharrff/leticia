"""
Repositório das seções de bula com busca full-text em português.

Camada entre a tool `consultar_bula_secao` e a tabela public.bula_secoes.

  • upsert_secoes(num_processo, secoes)   — grava seções extraídas
  • search_bula(termo_medicamento, pergunta, ...) — busca FTS por trechos
"""
from __future__ import annotations

from typing import Any

import structlog

from db.postgres import get_db_conn
from services.bula_extractor import BulaSecao

log = structlog.get_logger()


async def has_bula(num_processo: str) -> bool:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM public.bula_secoes WHERE num_processo = $1 LIMIT 1",
            num_processo,
        )
    return row is not None


async def upsert_secoes(num_processo: str, secoes: list[BulaSecao]) -> int:
    """Grava as seções extraídas. Retorna quantas linhas foram afetadas."""
    if not secoes:
        return 0
    n = 0
    async with get_db_conn() as conn:
        for sec in secoes:
            conteudo = (sec.conteudo or "").strip()
            if not conteudo:
                continue
            await conn.execute(
                """
                INSERT INTO public.bula_secoes
                    (num_processo, secao, secao_titulo, conteudo, char_count)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (num_processo, secao) DO UPDATE
                   SET secao_titulo = EXCLUDED.secao_titulo,
                       conteudo     = EXCLUDED.conteudo,
                       char_count   = EXCLUDED.char_count,
                       extracted_at = NOW()
                """,
                num_processo,
                sec.slug,
                sec.titulo,
                conteudo,
                len(conteudo),
            )
            n += 1
    return n


def _build_tsquery_expr(mode: str) -> str:
    """
    Retorna a expressão SQL que produz o tsquery de busca conforme o modo.

    - 'and': websearch_to_tsquery — precisão alta, exige todos os termos.
    - 'or':  to_tsquery construído manualmente com `|` entre tokens —
             recall alto, qualquer termo basta.
    """
    if mode == "and":
        return "websearch_to_tsquery('portuguese', public.f_unaccent($2))"
    # OR: split na pergunta, descarta tokens < 3 chars (stopwords sobrariam),
    # une com ` | `. Plpgsql não tem split fácil, então fazemos isso no app.
    # Aqui só consumimos $2 já transformado em "termo1 | termo2 | ...".
    return "to_tsquery('portuguese', public.f_unaccent($2))"


def _to_or_query(pergunta: str) -> str:
    """Transforma 'dose maxima criança' em 'dose | maxima | criança'."""
    tokens = [t for t in pergunta.split() if len(t) >= 3]
    return " | ".join(tokens) if tokens else pergunta


async def _run_search(
    termo: str, pergunta_expr: str, tsquery_sql: str, limit: int, secao: str | None,
) -> list[dict[str, Any]]:
    params: list[Any] = [termo, pergunta_expr, limit]
    where_secao = ""
    if secao:
        params.append(secao)
        where_secao = f"AND bs.secao = ${len(params)}"

    sql = f"""
    WITH meds AS (
        SELECT num_processo, nome_produto,
               similarity(nome_produto_norm, $1) AS sim
          FROM public.medicamentos_anvisa
         WHERE nome_produto_norm % $1
            OR principio_ativo ILIKE '%' || $1 || '%'
         ORDER BY sim DESC NULLS LAST
         LIMIT 5
    ),
    q AS (
        SELECT {tsquery_sql} AS tsq
    )
    SELECT bs.num_processo,
           m.nome_produto,
           bs.secao,
           bs.secao_titulo,
           ts_rank_cd(bs.conteudo_tsv, (SELECT tsq FROM q)) AS rank,
           ts_headline(
               'portuguese',
               bs.conteudo,
               (SELECT tsq FROM q),
               'MaxFragments=2, MaxWords=40, MinWords=15, StartSel=«, StopSel=»'
           ) AS trecho
      FROM public.bula_secoes bs
      JOIN meds m ON m.num_processo = bs.num_processo
     WHERE bs.conteudo_tsv @@ (SELECT tsq FROM q)
       {where_secao}
     ORDER BY rank DESC
     LIMIT $3
    """
    async with get_db_conn() as conn:
        rows = await conn.fetch(sql, *params)
    return [dict(r) for r in rows]


async def search_bula(
    termo_medicamento: str,
    pergunta: str,
    *,
    limit: int = 3,
    secao: str | None = None,
) -> list[dict[str, Any]]:
    """
    Busca trechos relevantes da bula com fallback AND → OR.

    Estratégia:
      1. AND (websearch_to_tsquery) — todos os termos. Precisão alta.
      2. Se vazio, OR (`termo1 | termo2 | ...` via to_tsquery) — recall alto.

    Filtra `bula_secoes` por num_processo dos medicamentos que batem com
    `termo_medicamento` (via trigram em medicamentos_anvisa). Ranqueia por
    ts_rank_cd; retorna trecho com ts_headline (palavras em «…»).
    """
    norm_termo = " ".join((termo_medicamento or "").lower().split())
    norm_pergunta = (pergunta or "").strip()
    if not norm_termo or not norm_pergunta:
        return []

    # 1) AND
    rows = await _run_search(
        norm_termo, norm_pergunta,
        _build_tsquery_expr("and"), limit, secao,
    )
    if rows:
        return rows

    # 2) Fallback OR — só palavras significativas
    or_expr = _to_or_query(norm_pergunta)
    if not or_expr.strip():
        return []
    log.info("bula.search.fallback_or", termo=norm_termo, q=or_expr)
    return await _run_search(
        norm_termo, or_expr,
        _build_tsquery_expr("or"), limit, secao,
    )
