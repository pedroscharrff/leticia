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


async def search_bula(
    termo_medicamento: str,
    pergunta: str,
    *,
    limit: int = 3,
    secao: str | None = None,
) -> list[dict[str, Any]]:
    """
    Busca trechos relevantes da bula.

    Estratégia:
      1. Filtra `bula_secoes` por num_processo dos medicamentos que batem
         com `termo_medicamento` (via trigram em medicamentos_anvisa).
      2. Aplica websearch_to_tsquery na `pergunta` contra `conteudo_tsv`.
      3. Ranqueia por ts_rank_cd; retorna trecho com ts_headline.

    Retorna: [{num_processo, nome_produto, secao, trecho, rank}, ...]
    """
    norm_termo = " ".join((termo_medicamento or "").lower().split())
    norm_pergunta = (pergunta or "").strip()
    if not norm_termo or not norm_pergunta:
        return []

    params: list[Any] = [norm_termo, norm_pergunta, limit]
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
        -- f_unaccent é o wrapper IMMUTABLE criado em 034_bula_unaccent.sql;
        -- precisa bater com o que está no GENERATED da coluna conteudo_tsv,
        -- senão queries com/sem acento perdem matches.
        SELECT websearch_to_tsquery('portuguese', public.f_unaccent($2)) AS tsq
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
