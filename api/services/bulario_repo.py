"""
Repositório da base de conhecimento de medicamentos (public.medicamentos_anvisa).

Camada entre as tools do agente e a API da ANVISA:
  • search_local(termo)            — busca por trigram no DB
  • get_or_fetch(termo)             — local first; miss → ANVISA + upsert
  • upsert_search_results(...)      — grava resultados de search
  • upsert_detail(num_processo, d)  — grava detalhe enriquecido

Nunca passa `json.dumps()` para campos JSONB — o codec configurado em
db/postgres.py já cuida disso (cf. jsonb-double-encoding).
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog

from db.postgres import get_db_conn
from services.anvisa_client import AnvisaClient, AnvisaError

log = structlog.get_logger()

# Quantos `detail` paralelos buscar quando recebemos resultados frescos de
# search. Trade-off: latência vs. enriquecimento. 3 é o sweet spot (~1-2s
# adicionais no cold path, depois cache).
TOP_N_DETAIL = 3

# Tempo que um cache de query permanece válido sem refetch.
QUERY_CACHE_MIN_RESULTS = 1


def _normalize(term: str) -> str:
    return " ".join((term or "").lower().split())


async def search_local(termo: str, limit: int = 10) -> list[dict]:
    """
    Busca medicamentos no catálogo local. Combina match exato em
    principio_ativo + similaridade trigram em nome_produto_norm.
    """
    norm = _normalize(termo)
    if not norm:
        return []

    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT num_processo, nome_produto, principio_ativo, razao_social,
                   classes_terapeuticas, mes_ano_vencimento, has_detail,
                   similarity(nome_produto_norm, $1) AS sim
              FROM public.medicamentos_anvisa
             WHERE nome_produto_norm % $1
                OR principio_ativo ILIKE '%' || $1 || '%'
             ORDER BY sim DESC NULLS LAST
             LIMIT $2
            """,
            norm,
            limit,
        )
    return [dict(r) for r in rows]


async def _bump_query_cache_hit(query_norm: str) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE public.bulario_query_cache "
            "   SET hits = hits + 1 "
            " WHERE query_norm = $1",
            query_norm,
        )


async def _get_cached_query(query_norm: str) -> list[str] | None:
    """Retorna lista de num_processos se cache fresco, senão None."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT num_processos
              FROM public.bulario_query_cache
             WHERE query_norm = $1
               AND expires_at > NOW()
            """,
            query_norm,
        )
    return list(row["num_processos"]) if row else None


async def _save_query_cache(
    query_norm: str, num_processos: list[str], total: int | None
) -> None:
    async with get_db_conn() as conn:
        await conn.execute(
            """
            INSERT INTO public.bulario_query_cache
                (query_norm, num_processos, total_anvisa, cached_at, expires_at)
            VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '30 days')
            ON CONFLICT (query_norm) DO UPDATE
               SET num_processos = EXCLUDED.num_processos,
                   total_anvisa  = EXCLUDED.total_anvisa,
                   cached_at     = NOW(),
                   expires_at    = EXCLUDED.expires_at
            """,
            query_norm,
            num_processos,
            total,
        )


async def upsert_search_results(items: list[dict]) -> list[str]:
    """
    Grava resultados de /consulta/bulario. Retorna lista de num_processos
    na ordem dos itens recebidos. Idempotente — usa ON CONFLICT no
    num_processo.
    """
    if not items:
        return []
    num_processos: list[str] = []
    async with get_db_conn() as conn:
        for it in items:
            num_proc = str(it.get("numProcesso") or "").strip()
            if not num_proc:
                # Sem chave natural — pula. Isso não deveria acontecer com a API
                # atual, mas evita explodir se a ANVISA mudar.
                log.warning("bulario.upsert.no_num_processo", item_keys=list(it))
                continue
            nome = it.get("nomeProduto") or ""
            await conn.execute(
                """
                INSERT INTO public.medicamentos_anvisa
                    (num_processo, id_produto, numero_registro, nome_produto,
                     nome_produto_norm, razao_social, cnpj, raw_search,
                     fetched_at, stale_after)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8,
                        NOW(), NOW() + INTERVAL '90 days')
                ON CONFLICT (num_processo) DO UPDATE
                   SET id_produto      = COALESCE(EXCLUDED.id_produto, public.medicamentos_anvisa.id_produto),
                       numero_registro = COALESCE(EXCLUDED.numero_registro, public.medicamentos_anvisa.numero_registro),
                       nome_produto    = EXCLUDED.nome_produto,
                       nome_produto_norm = EXCLUDED.nome_produto_norm,
                       razao_social    = COALESCE(EXCLUDED.razao_social, public.medicamentos_anvisa.razao_social),
                       cnpj            = COALESCE(EXCLUDED.cnpj, public.medicamentos_anvisa.cnpj),
                       raw_search      = EXCLUDED.raw_search,
                       fetched_at      = NOW(),
                       stale_after     = NOW() + INTERVAL '90 days'
                """,
                num_proc,
                it.get("idProduto"),
                it.get("numeroRegistro"),
                nome,
                _normalize(nome),
                it.get("razaoSocial"),
                it.get("cnpj"),
                it,
            )
            num_processos.append(num_proc)
    return num_processos


def _principio_ativo_text(detail: dict) -> str | None:
    """
    Extrai princípio ativo do detail. A ANVISA às vezes retorna string,
    às vezes lista (string ou dicts com `nomePrincipio` / `concentracao`).
    Normaliza para uma string legível, sem duplicatas (a ANVISA chega a
    retornar `["DIPIRONA", "dipirona monoidratada"]` — colapsamos).
    """
    pa = detail.get("principioAtivo")
    if not pa:
        return None
    if isinstance(pa, str):
        return pa.strip().title() or None
    if isinstance(pa, list):
        parts: list[str] = []
        for entry in pa:
            if isinstance(entry, str):
                parts.append(entry.strip())
            elif isinstance(entry, dict):
                nome = (entry.get("nomePrincipio") or entry.get("nome") or "").strip()
                conc = (entry.get("concentracao") or "").strip()
                parts.append(f"{nome} {conc}".strip())
        # Dedup case-insensitive preservando ordem; descarta substring de outro.
        seen: list[str] = []
        for p in parts:
            if not p:
                continue
            low = p.lower()
            if any(low in s.lower() or s.lower() in low for s in seen):
                # Mantém a versão mais longa
                seen = [p if low in s.lower() else s for s in seen]
                continue
            seen.append(p)
        joined = " + ".join(s.title() for s in seen)
        return joined or None
    return None


async def upsert_detail(num_processo: str, detail: dict) -> None:
    """Grava resultado de /consulta/medicamento/produtos/{numProcesso}."""
    if not detail:
        return
    pa = _principio_ativo_text(detail)
    classes = detail.get("classesTerapeuticas") or []
    atcs    = detail.get("atcs") or []
    nome_comercial = detail.get("nomeComercial")

    async with get_db_conn() as conn:
        await conn.execute(
            """
            UPDATE public.medicamentos_anvisa
               SET nome_comercial            = COALESCE($2, nome_comercial),
                   principio_ativo           = COALESCE($3, principio_ativo),
                   classes_terapeuticas      = $4,
                   atcs                      = $5,
                   mes_ano_vencimento        = COALESCE($6, mes_ano_vencimento),
                   codigo_bula_paciente      = COALESCE($7, codigo_bula_paciente),
                   codigo_bula_profissional  = COALESCE($8, codigo_bula_profissional),
                   raw_detail                = $9,
                   has_detail                = TRUE,
                   detail_fetched_at         = NOW(),
                   stale_after               = NOW() + INTERVAL '90 days'
             WHERE num_processo = $1
            """,
            num_processo,
            nome_comercial,
            pa,
            list(classes),
            list(atcs),
            detail.get("mesAnoVencimento"),
            detail.get("codigoBulaPaciente"),
            detail.get("codigoBulaProfissional"),
            detail,
        )


async def _fetch_top_details(
    cli: AnvisaClient, num_processos: list[str], n: int = TOP_N_DETAIL
) -> None:
    """
    Busca em paralelo os top-N detalhes e grava no DB. Aproveita o JWT
    fresco (~5min de validade) pra também baixar e extrair o texto da
    bula. Tolerante a falhas — extração de bula pode falhar sem quebrar
    o resto do fluxo.
    """
    targets = num_processos[:n]
    if not targets:
        return

    async def _one(np: str) -> None:
        try:
            det = await cli.detail(np)
        except AnvisaError as exc:
            log.warning("bulario.detail.failed", num_processo=np, exc=str(exc))
            return
        try:
            await upsert_detail(np, det)
        except Exception as exc:  # noqa: BLE001
            log.warning("bulario.detail.upsert_failed", num_processo=np, exc=str(exc))
            return

        # Bula em PDF — usa o JWT que acabamos de receber (vence em ~5min).
        # Falha aqui não impede o resto do fluxo.
        codigo_bula = det.get("codigoBulaPaciente") or det.get("codigoBulaProfissional")
        if not codigo_bula:
            return
        try:
            from services.bula_repo import upsert_secoes, has_bula
            if await has_bula(np):
                return
            from services.bula_extractor import pdf_to_text, split_secoes
            pdf_bytes = await cli.download_bula_pdf(codigo_bula)
            text = pdf_to_text(pdf_bytes)
            secoes = split_secoes(text)
            n_secoes = await upsert_secoes(np, secoes)
            log.info(
                "bulario.bula_extracted",
                num_processo=np, secoes=n_secoes, chars=len(text),
            )
        except AnvisaError as exc:
            log.warning("bulario.bula_download.failed", num_processo=np, exc=str(exc))
        except Exception as exc:  # noqa: BLE001
            log.warning("bulario.bula_extract.failed", num_processo=np, exc=str(exc))

    await asyncio.gather(*[_one(np) for np in targets])


async def get_or_fetch(
    termo: str,
    *,
    client: AnvisaClient | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Ponto de entrada principal das tools.

    1. Tenta cache de query → resolve para linhas locais (já com detail).
    2. Tenta busca local trigram (cobre casos onde o termo já apareceu via
       outra query).
    3. Se nada útil, chama ANVISA, faz upsert, busca top-N details em
       paralelo e devolve as linhas recém-gravadas.
    """
    norm = _normalize(termo)
    if not norm:
        return []

    # 1) Cache de query
    cached_nps = await _get_cached_query(norm)
    if cached_nps:
        async with get_db_conn() as conn:
            rows = await conn.fetch(
                """
                SELECT num_processo, nome_produto, principio_ativo,
                       razao_social, classes_terapeuticas, mes_ano_vencimento,
                       has_detail
                  FROM public.medicamentos_anvisa
                 WHERE num_processo = ANY($1::text[])
                """,
                cached_nps,
            )
        if rows:
            await _bump_query_cache_hit(norm)
            # Preserva ordem da cache key
            by_np = {r["num_processo"]: dict(r) for r in rows}
            ordered = [by_np[np] for np in cached_nps if np in by_np]
            log.info("bulario.cache.hit", termo=norm, n=len(ordered))
            return ordered

    # 2) Busca local fuzzy
    local = await search_local(norm, limit=limit)
    if local and any(r["has_detail"] for r in local):
        log.info("bulario.local.hit", termo=norm, n=len(local))
        return local

    # 3) Fetch ANVISA
    own_client = client is None
    cli = client or AnvisaClient()
    try:
        log.info("bulario.fetch.anvisa", termo=norm)
        res = await cli.search(norm, count=limit)
        content = res.get("content") or []
        total   = res.get("totalElements")
        if not content:
            await _save_query_cache(norm, [], total or 0)
            return []
        num_processos = await upsert_search_results(content)
        await _save_query_cache(norm, num_processos, total)
        await _fetch_top_details(cli, num_processos, n=TOP_N_DETAIL)
    finally:
        if own_client:
            await cli.close()

    # Retorna linhas pós-upsert (agora com detail nos top-N)
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT num_processo, nome_produto, principio_ativo,
                   razao_social, classes_terapeuticas, mes_ano_vencimento,
                   has_detail
              FROM public.medicamentos_anvisa
             WHERE num_processo = ANY($1::text[])
            """,
            num_processos,
        )
    by_np = {r["num_processo"]: dict(r) for r in rows}
    return [by_np[np] for np in num_processos if np in by_np]
