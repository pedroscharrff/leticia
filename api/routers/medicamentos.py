"""
Admin geral — Medicamentos (bulário ANVISA + base de referência).

Painel global (não per-tenant). Duas fontes públicas compartilhadas:
  • public.medicamentos_anvisa        — bulário cacheado da ANVISA (read-only)
  • public.medicamentos_referencia(+secoes) — guia curado de referência (editável)

A curadoria das seções clínicas mora aqui: cada seção tem status
pending/active/disabled; só `active` é exposta ao agente (filtro no
referencia_repo). O superadmin revisa o conteúdo (fonte de 2001) e ativa.

A ingestão do bulário deixou de ser só sob demanda (disparada pelos agentes):
o superadmin agora alimenta o catálogo manualmente daqui, termo a termo
(consulta manual) ou em lote (inserção em massa). Ambos reusam o cold path
real das tools (`bulario_repo.get_or_fetch`): ANVISA → upsert + detail + bula.

Endpoints (todos exigem admin):
  GET    /admin/medicamentos/bulario                 — lista/busca ANVISA (cache local)
  GET    /admin/medicamentos/bulario/stats           — resumo do cache (total/detalhe/bula)
  POST   /admin/medicamentos/bulario/consultar       — consulta manual: busca na ANVISA + insere
  POST   /admin/medicamentos/bulario/bulk            — inserção em massa (vários termos)
  GET    /admin/medicamentos/bulario/{num_processo}  — detalhe + seções de bula extraídas
  GET    /admin/medicamentos/referencia              — lista/busca referência (+resumo de status)
  GET    /admin/medicamentos/referencia/{id}         — pai + todas as seções
  POST   /admin/medicamentos/referencia              — cria entrada manual
  PATCH  /admin/medicamentos/referencia/{id}         — edita campos do pai
  DELETE /admin/medicamentos/referencia/{id}         — remove (cascade seções)
  PATCH  /admin/medicamentos/referencia/{id}/secoes/{secao} — curadoria (conteúdo + status)
"""
from __future__ import annotations

from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_admin
from services import bulario_repo
from services.anvisa_client import AnvisaClient

log = structlog.get_logger()

admin_router = APIRouter(prefix="/admin/medicamentos", tags=["admin-medicamentos"])
AdminUser = Annotated[str, Depends(require_admin)]

_VALID_SECOES = (
    "indicacoes", "posologia", "contraindicacoes",
    "efeitos_adversos", "interacoes", "precaucoes",
)


def _normalize(term: str) -> str:
    return " ".join((term or "").lower().split())


# ── Schemas ──────────────────────────────────────────────────────────────────

class BularioOut(BaseModel):
    num_processo: str
    nome_produto: str
    principio_ativo: str | None
    razao_social: str | None
    classes_terapeuticas: list[str]
    has_detail: bool
    has_bula: bool = False


class BularioStats(BaseModel):
    total: int
    com_detalhe: int
    com_bula: int


class BularioIngest(BaseModel):
    """Consulta manual — busca um termo na ANVISA e insere no cache local."""
    termo: str = Field(..., min_length=2, description="nome ou princípio ativo")
    top_n: int = Field(3, ge=1, le=10, description="quantos detalhes/bulas baixar")


class BularioIngestResult(BaseModel):
    termo: str
    encontrados: int          # linhas devolvidas pelo cold path
    com_detalhe: int          # quantas já têm detail enriquecido
    com_bula: int             # quantas têm seções de bula extraídas
    itens: list[BularioOut] = Field(default_factory=list)
    erro: str | None = None   # preenchido quando a ANVISA falha


class BularioBulkIngest(BaseModel):
    """Inserção em massa — vários termos numa tacada."""
    termos: list[str] = Field(..., min_length=1, max_length=100)
    top_n: int = Field(3, ge=1, le=10)


class BularioBulkResult(BaseModel):
    total_termos: int
    com_resultado: int        # termos que trouxeram ≥1 medicamento
    sem_resultado: int        # termos sem match na ANVISA
    com_erro: int             # termos que falharam (rede/ANVISA)
    novos_no_cache: int       # medicamentos que não existiam antes
    resultados: list[BularioIngestResult] = Field(default_factory=list)


class BularioSecaoOut(BaseModel):
    secao: str
    secao_titulo: str | None
    conteudo: str
    char_count: int


class BularioDetailOut(BaseModel):
    num_processo: str
    nome_produto: str
    nome_comercial: str | None
    principio_ativo: str | None
    razao_social: str | None
    classes_terapeuticas: list[str]
    has_detail: bool
    fetched_at: str | None
    detail_fetched_at: str | None
    secoes: list[BularioSecaoOut] = Field(default_factory=list)


class SecaoOut(BaseModel):
    secao: str
    conteudo: str
    status: str
    reviewed_at: str | None
    reviewed_by: str | None


class ReferenciaListOut(BaseModel):
    id: int
    principio_ativo: str
    nome_referencia: str | None
    forma_farmaceutica: str | None
    categoria: str | None
    secoes_active: int
    secoes_total: int


class ReferenciaDetailOut(BaseModel):
    id: int
    principio_ativo: str
    nome_referencia: str | None
    forma_farmaceutica: str | None
    categoria: str | None
    source: str | None
    page_ref: int | None
    secoes: list[SecaoOut] = Field(default_factory=list)


class ReferenciaPatch(BaseModel):
    principio_ativo: str | None = None
    nome_referencia: str | None = None
    forma_farmaceutica: str | None = None
    categoria: str | None = None


class ReferenciaCreate(BaseModel):
    principio_ativo: str
    nome_referencia: str | None = None
    forma_farmaceutica: str | None = None
    categoria: str | None = None


class SecaoPatch(BaseModel):
    conteudo: str | None = None
    status: Literal["pending", "active", "disabled"] | None = None


class BulkMedStatus(BaseModel):
    """Muda o status de TODAS as seções de um medicamento de uma vez."""
    status: Literal["pending", "active", "disabled"]
    only_pending: bool = False   # se True, só toca seções que estão `pending`


class BulkGlobalStatus(BaseModel):
    """Muda o status de seções em MASSA (todos os medicamentos)."""
    status: Literal["pending", "active", "disabled"]
    secao: str | None = None     # opcional: restringe a uma seção (slug)
    only_pending: bool = False   # opcional: só afeta as que estão `pending`


class BulkResult(BaseModel):
    updated: int


class ReferenciaStats(BaseModel):
    medicamentos: int
    secoes_total: int
    secoes_active: int
    secoes_pending: int
    secoes_disabled: int


class ConsultaOut(BaseModel):
    id: int
    tenant_id: str | None
    session_id: str | None
    skill: str | None
    termo: str
    encontrado: bool
    num_resultados: int
    medicamentos: list[dict] = Field(default_factory=list)
    secoes: list[str] = Field(default_factory=list)
    created_at: str


class ConsultasStats(BaseModel):
    total: int
    encontrados: int
    nao_encontrados: int
    # encontraram o medicamento mas NENHUMA seção ativa foi devolvida
    # (sinal de curadoria pendente para aquele medicamento)
    sem_secao_ativa: int
    # slug da seção → nº de consultas que a devolveram (consumo real por seção)
    por_secao: dict[str, int] = Field(default_factory=dict)


# ── Bulário ANVISA (read-only) ──────────────────────────────────────────────

@admin_router.get("/bulario", response_model=list[BularioOut])
async def list_bulario(
    _admin: AdminUser,
    q: str | None = Query(None, description="busca por nome ou princípio ativo"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[BularioOut]:
    norm = _normalize(q) if q else None
    async with get_db_conn() as conn:
        if norm:
            rows = await conn.fetch(
                """
                SELECT m.num_processo, m.nome_produto, m.principio_ativo, m.razao_social,
                       m.classes_terapeuticas, m.has_detail,
                       EXISTS (SELECT 1 FROM public.bula_secoes b
                                WHERE b.num_processo = m.num_processo) AS has_bula
                  FROM public.medicamentos_anvisa m
                 WHERE (m.nome_produto_norm % $1 AND similarity(m.nome_produto_norm, $1) >= 0.30)
                    OR m.principio_ativo ILIKE '%' || $1 || '%'
                 ORDER BY similarity(m.nome_produto_norm, $1) DESC NULLS LAST
                 LIMIT $2 OFFSET $3
                """,
                norm, limit, offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT m.num_processo, m.nome_produto, m.principio_ativo, m.razao_social,
                       m.classes_terapeuticas, m.has_detail,
                       EXISTS (SELECT 1 FROM public.bula_secoes b
                                WHERE b.num_processo = m.num_processo) AS has_bula
                  FROM public.medicamentos_anvisa m
                 ORDER BY m.nome_produto
                 LIMIT $1 OFFSET $2
                """,
                limit, offset,
            )
    return [
        BularioOut(
            num_processo=r["num_processo"],
            nome_produto=r["nome_produto"],
            principio_ativo=r["principio_ativo"],
            razao_social=r["razao_social"],
            classes_terapeuticas=list(r["classes_terapeuticas"] or []),
            has_detail=r["has_detail"],
            has_bula=r["has_bula"],
        )
        for r in rows
    ]


# ── Bulário ANVISA — alimentação manual (consulta + inserção em massa) ────────
# IMPORTANTE: rotas estáticas (`/stats`, `/consultar`, `/bulk`) declaradas ANTES
# de `/bulario/{num_processo}` — senão o FastAPI casaria "stats" como num_processo
# e nunca chegaria aqui.

async def _bula_counts(conn, num_processos: list[str]) -> set[str]:
    """num_processos que já têm ≥1 seção de bula extraída."""
    if not num_processos:
        return set()
    rows = await conn.fetch(
        "SELECT DISTINCT num_processo FROM public.bula_secoes "
        "WHERE num_processo = ANY($1::text[])",
        num_processos,
    )
    return {r["num_processo"] for r in rows}


async def _rows_to_bulario_out(num_processos: list[str]) -> list[BularioOut]:
    """Carrega BularioOut (com has_bula) para uma lista de num_processos, preservando ordem."""
    if not num_processos:
        return []
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT num_processo, nome_produto, principio_ativo, razao_social,
                   classes_terapeuticas, has_detail
              FROM public.medicamentos_anvisa
             WHERE num_processo = ANY($1::text[])
            """,
            num_processos,
        )
        com_bula = await _bula_counts(conn, num_processos)
    by_np = {r["num_processo"]: r for r in rows}
    out: list[BularioOut] = []
    for np in num_processos:
        r = by_np.get(np)
        if not r:
            continue
        out.append(BularioOut(
            num_processo=r["num_processo"],
            nome_produto=r["nome_produto"],
            principio_ativo=r["principio_ativo"],
            razao_social=r["razao_social"],
            classes_terapeuticas=list(r["classes_terapeuticas"] or []),
            has_detail=r["has_detail"],
            has_bula=np in com_bula,
        ))
    return out


async def _ingest_termo(termo: str, top_n: int, client: AnvisaClient) -> BularioIngestResult:
    """Roda o cold path real (get_or_fetch) para um termo e resume o resultado."""
    termo = (termo or "").strip()
    try:
        rows = await bulario_repo.get_or_fetch(termo, client=client, limit=max(top_n, 5))
    except Exception as exc:  # noqa: BLE001 — ANVISA/rede; não pode derrubar o lote
        log.warning("bulario.ingest.failed", termo=termo, exc=str(exc))
        return BularioIngestResult(termo=termo, encontrados=0, com_detalhe=0,
                                   com_bula=0, erro=str(exc)[:200])
    nps = [r["num_processo"] for r in rows]
    itens = await _rows_to_bulario_out(nps)
    return BularioIngestResult(
        termo=termo,
        encontrados=len(itens),
        com_detalhe=sum(1 for i in itens if i.has_detail),
        com_bula=sum(1 for i in itens if i.has_bula),
        itens=itens,
    )


@admin_router.get("/bulario/stats", response_model=BularioStats)
async def bulario_stats(_admin: AdminUser) -> BularioStats:
    """Resumo do cache local do bulário, para o header do painel."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE has_detail) AS com_detalhe
              FROM public.medicamentos_anvisa
            """
        )
        com_bula = await conn.fetchval(
            "SELECT count(DISTINCT num_processo) FROM public.bula_secoes"
        )
    return BularioStats(
        total=row["total"] or 0,
        com_detalhe=row["com_detalhe"] or 0,
        com_bula=com_bula or 0,
    )


@admin_router.post("/bulario/consultar", response_model=BularioIngestResult)
async def ingest_bulario(_admin: AdminUser, body: BularioIngest) -> BularioIngestResult:
    """
    Consulta manual: busca o termo na ANVISA (cold path real das tools) e
    persiste no cache local — catálogo + detail + seções de bula dos top-N.
    Idempotente: reconsultar o mesmo termo só reaproveita/atualiza o cache.
    """
    termo = body.termo.strip()
    if len(termo) < 2:
        raise HTTPException(status_code=422, detail="termo muito curto")
    cli = AnvisaClient()
    try:
        res = await _ingest_termo(termo, body.top_n, cli)
    finally:
        await cli.close()
    log.info("bulario.ingest.manual", actor=_admin, termo=termo,
             encontrados=res.encontrados, com_bula=res.com_bula, erro=res.erro)
    return res


@admin_router.post("/bulario/bulk", response_model=BularioBulkResult)
async def ingest_bulario_bulk(_admin: AdminUser, body: BularioBulkIngest) -> BularioBulkResult:
    """
    Inserção em massa: roda a consulta manual para cada termo, sequencialmente,
    reusando um único AnvisaClient (warmup/cookies compartilhados; a API da
    ANVISA é throttled, então paralelismo agressivo só toma rate-limit).
    """
    # Dedup preservando ordem; ignora vazios.
    seen: list[str] = []
    for t in body.termos:
        t = (t or "").strip()
        if t and t.lower() not in {s.lower() for s in seen}:
            seen.append(t)
    if not seen:
        raise HTTPException(status_code=422, detail="nenhum termo válido")

    async with get_db_conn() as conn:
        antes = await conn.fetchval("SELECT count(*) FROM public.medicamentos_anvisa") or 0

    cli = AnvisaClient()
    resultados: list[BularioIngestResult] = []
    try:
        for termo in seen:
            resultados.append(await _ingest_termo(termo, body.top_n, cli))
    finally:
        await cli.close()

    async with get_db_conn() as conn:
        depois = await conn.fetchval("SELECT count(*) FROM public.medicamentos_anvisa") or 0

    com_resultado = sum(1 for r in resultados if r.encontrados > 0)
    com_erro = sum(1 for r in resultados if r.erro)
    log.info("bulario.ingest.bulk", actor=_admin, termos=len(seen),
             com_resultado=com_resultado, com_erro=com_erro, novos=depois - antes)
    return BularioBulkResult(
        total_termos=len(seen),
        com_resultado=com_resultado,
        sem_resultado=sum(1 for r in resultados if r.encontrados == 0 and not r.erro),
        com_erro=com_erro,
        novos_no_cache=max(0, depois - antes),
        resultados=resultados,
    )


@admin_router.get("/bulario/{num_processo}", response_model=BularioDetailOut)
async def get_bulario_detail(_admin: AdminUser, num_processo: str) -> BularioDetailOut:
    """Detalhe de um medicamento do cache + seções de bula extraídas (para conferência)."""
    async with get_db_conn() as conn:
        m = await conn.fetchrow(
            """
            SELECT num_processo, nome_produto, nome_comercial, principio_ativo,
                   razao_social, classes_terapeuticas, has_detail,
                   fetched_at, detail_fetched_at
              FROM public.medicamentos_anvisa
             WHERE num_processo = $1
            """,
            num_processo,
        )
        if not m:
            raise HTTPException(status_code=404, detail="Medicamento não encontrado no cache")
        secoes = await conn.fetch(
            """
            SELECT secao, secao_titulo, conteudo, char_count
              FROM public.bula_secoes
             WHERE num_processo = $1
             ORDER BY secao
            """,
            num_processo,
        )
    return BularioDetailOut(
        num_processo=m["num_processo"],
        nome_produto=m["nome_produto"],
        nome_comercial=m["nome_comercial"],
        principio_ativo=m["principio_ativo"],
        razao_social=m["razao_social"],
        classes_terapeuticas=list(m["classes_terapeuticas"] or []),
        has_detail=m["has_detail"],
        fetched_at=m["fetched_at"].isoformat() if m["fetched_at"] else None,
        detail_fetched_at=m["detail_fetched_at"].isoformat() if m["detail_fetched_at"] else None,
        secoes=[
            BularioSecaoOut(
                secao=s["secao"],
                secao_titulo=s["secao_titulo"],
                conteudo=s["conteudo"],
                char_count=s["char_count"],
            )
            for s in secoes
        ],
    )


# ── Medicamentos de referência ──────────────────────────────────────────────

@admin_router.get("/referencia", response_model=list[ReferenciaListOut])
async def list_referencia(
    _admin: AdminUser,
    q: str | None = Query(None, description="busca por princípio ativo ou marca"),
    pendentes: bool = Query(False, description="só os que têm seção pendente"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[ReferenciaListOut]:
    norm = _normalize(q) if q else None
    where = []
    params: list = []
    if norm:
        params.append(norm)
        where.append(
            f"(mr.principio_ativo_norm ILIKE '%' || ${len(params)} || '%' "
            f"OR mr.nome_referencia_norm ILIKE '%' || ${len(params)} || '%')"
        )
    having = ""
    if pendentes:
        having = "HAVING count(s.id) FILTER (WHERE s.status = 'pending') > 0"
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    params.extend([limit, offset])
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT mr.id, mr.principio_ativo, mr.nome_referencia,
                   mr.forma_farmaceutica, mr.categoria,
                   count(s.id) FILTER (WHERE s.status = 'active') AS secoes_active,
                   count(s.id) AS secoes_total
              FROM public.medicamentos_referencia mr
              LEFT JOIN public.medicamentos_referencia_secoes s
                     ON s.referencia_id = mr.id
             {where_sql}
             GROUP BY mr.id
             {having}
             ORDER BY mr.principio_ativo
             LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
    return [
        ReferenciaListOut(
            id=r["id"],
            principio_ativo=r["principio_ativo"],
            nome_referencia=r["nome_referencia"],
            forma_farmaceutica=r["forma_farmaceutica"],
            categoria=r["categoria"],
            secoes_active=r["secoes_active"],
            secoes_total=r["secoes_total"],
        )
        for r in rows
    ]


# IMPORTANTE: declarar ANTES de `/referencia/{ref_id}` — senão o FastAPI casa
# "stats" como {ref_id} (int) e devolve 422 em vez de cair aqui.
@admin_router.get("/referencia/stats", response_model=ReferenciaStats)
async def referencia_stats(_admin: AdminUser) -> ReferenciaStats:
    """Resumo para o painel: total de medicamentos e contagem de seções por status."""
    async with get_db_conn() as conn:
        meds = await conn.fetchval("SELECT count(*) FROM public.medicamentos_referencia")
        row = await conn.fetchrow(
            """
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE status = 'active')   AS active,
                   count(*) FILTER (WHERE status = 'pending')  AS pending,
                   count(*) FILTER (WHERE status = 'disabled') AS disabled
              FROM public.medicamentos_referencia_secoes
            """
        )
    return ReferenciaStats(
        medicamentos=meds or 0,
        secoes_total=row["total"] or 0,
        secoes_active=row["active"] or 0,
        secoes_pending=row["pending"] or 0,
        secoes_disabled=row["disabled"] or 0,
    )


# ── Consultas (log de uso da base pelo agente) ──────────────────────────────
# IMPORTANTE: declarar ANTES de `/referencia/{ref_id}` — "consultas" casaria
# como {ref_id:int} e devolveria 422 (mesma pegadinha de `/referencia/stats`).

@admin_router.get("/referencia/consultas/stats", response_model=ConsultasStats)
async def consultas_stats(
    _admin: AdminUser,
    tenant_id: str | None = Query(None, description="filtra por farmácia (UUID)"),
) -> ConsultasStats:
    """Resumo do consumo da base de referência pelos agentes."""
    where = []
    params: list = []
    if tenant_id:
        params.append(tenant_id)
        where.append(f"tenant_id = ${len(params)}::uuid")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"""
            SELECT count(*) AS total,
                   count(*) FILTER (WHERE encontrado) AS encontrados,
                   count(*) FILTER (WHERE NOT encontrado) AS nao_encontrados,
                   count(*) FILTER (
                       WHERE encontrado AND jsonb_array_length(secoes) = 0
                   ) AS sem_secao_ativa
              FROM public.medicamentos_referencia_consultas
            {where_sql}
            """,
            *params,
        )
        sec_rows = await conn.fetch(
            f"""
            SELECT s.slug AS slug, count(*) AS n
              FROM public.medicamentos_referencia_consultas c
              CROSS JOIN LATERAL jsonb_array_elements_text(c.secoes) AS s(slug)
            {where_sql}
             GROUP BY s.slug
             ORDER BY n DESC
            """,
            *params,
        )
    return ConsultasStats(
        total=row["total"] or 0,
        encontrados=row["encontrados"] or 0,
        nao_encontrados=row["nao_encontrados"] or 0,
        sem_secao_ativa=row["sem_secao_ativa"] or 0,
        por_secao={r["slug"]: r["n"] for r in sec_rows},
    )


@admin_router.get("/referencia/consultas", response_model=list[ConsultaOut])
async def list_consultas(
    _admin: AdminUser,
    q: str | None = Query(None, description="busca no termo consultado"),
    tenant_id: str | None = Query(None, description="filtra por farmácia (UUID)"),
    skill: str | None = Query(None, description="farmaceutico | principio_ativo | genericos"),
    encontrado: bool | None = Query(None, description="só com/sem match"),
    secao: str | None = Query(None, description="só consultas que devolveram esta seção (slug)"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[ConsultaOut]:
    where: list[str] = []
    params: list = []
    if q:
        params.append(_normalize(q))
        where.append(f"lower(termo) ILIKE '%' || ${len(params)} || '%'")
    if tenant_id:
        params.append(tenant_id)
        where.append(f"tenant_id = ${len(params)}::uuid")
    if skill:
        params.append(skill)
        where.append(f"skill = ${len(params)}")
    if encontrado is not None:
        params.append(encontrado)
        where.append(f"encontrado = ${len(params)}")
    if secao:
        params.append(secao)
        where.append(f"secoes ? ${len(params)}")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    params.extend([limit, offset])
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, tenant_id, session_id, skill, termo, encontrado,
                   num_resultados, medicamentos, secoes, created_at
              FROM public.medicamentos_referencia_consultas
            {where_sql}
             ORDER BY created_at DESC
             LIMIT ${len(params) - 1} OFFSET ${len(params)}
            """,
            *params,
        )
    return [
        ConsultaOut(
            id=r["id"],
            tenant_id=str(r["tenant_id"]) if r["tenant_id"] else None,
            session_id=r["session_id"],
            skill=r["skill"],
            termo=r["termo"],
            encontrado=r["encontrado"],
            num_resultados=r["num_resultados"],
            medicamentos=list(r["medicamentos"] or []),
            secoes=list(r["secoes"] or []),
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]


@admin_router.get("/referencia/{ref_id}", response_model=ReferenciaDetailOut)
async def get_referencia(_admin: AdminUser, ref_id: int) -> ReferenciaDetailOut:
    async with get_db_conn() as conn:
        parent = await conn.fetchrow(
            """
            SELECT id, principio_ativo, nome_referencia, forma_farmaceutica,
                   categoria, source, page_ref
              FROM public.medicamentos_referencia
             WHERE id = $1
            """,
            ref_id,
        )
        if not parent:
            raise HTTPException(status_code=404, detail="Medicamento não encontrado")
        secoes = await conn.fetch(
            """
            SELECT secao, conteudo, status, reviewed_at, reviewed_by
              FROM public.medicamentos_referencia_secoes
             WHERE referencia_id = $1
             ORDER BY secao
            """,
            ref_id,
        )
    return ReferenciaDetailOut(
        id=parent["id"],
        principio_ativo=parent["principio_ativo"],
        nome_referencia=parent["nome_referencia"],
        forma_farmaceutica=parent["forma_farmaceutica"],
        categoria=parent["categoria"],
        source=parent["source"],
        page_ref=parent["page_ref"],
        secoes=[
            SecaoOut(
                secao=s["secao"],
                conteudo=s["conteudo"],
                status=s["status"],
                reviewed_at=s["reviewed_at"].isoformat() if s["reviewed_at"] else None,
                reviewed_by=s["reviewed_by"],
            )
            for s in secoes
        ],
    )


@admin_router.post(
    "/referencia", response_model=ReferenciaDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_referencia(_admin: AdminUser, body: ReferenciaCreate) -> ReferenciaDetailOut:
    pa = body.principio_ativo.strip()
    if not pa:
        raise HTTPException(status_code=422, detail="principio_ativo obrigatório")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.medicamentos_referencia
                (principio_ativo, principio_ativo_norm, nome_referencia,
                 nome_referencia_norm, forma_farmaceutica, categoria,
                 source, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, 'manual', NOW())
            ON CONFLICT (principio_ativo, nome_referencia) DO NOTHING
            RETURNING id
            """,
            pa, _normalize(pa), body.nome_referencia,
            _normalize(body.nome_referencia) if body.nome_referencia else None,
            body.forma_farmaceutica, body.categoria,
        )
        if not row:
            raise HTTPException(status_code=409, detail="Já existe esse par princípio ativo + marca")
    return await get_referencia(_admin, row["id"])


@admin_router.patch("/referencia/{ref_id}", response_model=ReferenciaDetailOut)
async def patch_referencia(
    _admin: AdminUser, ref_id: int, body: ReferenciaPatch
) -> ReferenciaDetailOut:
    sets: list[str] = []
    params: list = []
    data = body.model_dump(exclude_unset=True)
    for field in ("principio_ativo", "nome_referencia", "forma_farmaceutica", "categoria"):
        if field in data:
            params.append(data[field])
            sets.append(f"{field} = ${len(params)}")
            # mantém os *_norm em sincronia
            if field == "principio_ativo":
                params.append(_normalize(data[field] or ""))
                sets.append(f"principio_ativo_norm = ${len(params)}")
            elif field == "nome_referencia":
                params.append(_normalize(data[field]) if data[field] else None)
                sets.append(f"nome_referencia_norm = ${len(params)}")
    if not sets:
        return await get_referencia(_admin, ref_id)
    params.append(ref_id)
    async with get_db_conn() as conn:
        res = await conn.execute(
            f"UPDATE public.medicamentos_referencia "
            f"SET {', '.join(sets)}, updated_at = NOW() WHERE id = ${len(params)}",
            *params,
        )
    if res.split()[-1] == "0":
        raise HTTPException(status_code=404, detail="Medicamento não encontrado")
    return await get_referencia(_admin, ref_id)


@admin_router.delete(
    "/referencia/{ref_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_referencia(_admin: AdminUser, ref_id: int) -> None:
    async with get_db_conn() as conn:
        res = await conn.execute(
            "DELETE FROM public.medicamentos_referencia WHERE id = $1", ref_id
        )
    if res.split()[-1] == "0":
        raise HTTPException(status_code=404, detail="Medicamento não encontrado")


@admin_router.patch("/referencia/{ref_id}/secoes/{secao}", response_model=SecaoOut)
async def patch_secao(
    admin_email: AdminUser, ref_id: int, secao: str, body: SecaoPatch
) -> SecaoOut:
    """Curadoria: edita conteúdo e/ou muda status de uma seção clínica."""
    if secao not in _VALID_SECOES:
        raise HTTPException(status_code=422, detail=f"seção inválida: {secao}")

    sets: list[str] = []
    params: list = []
    data = body.model_dump(exclude_unset=True)
    if "conteudo" in data and data["conteudo"] is not None:
        params.append(data["conteudo"])
        sets.append(f"conteudo = ${len(params)}")
    if "status" in data and data["status"] is not None:
        params.append(data["status"])
        sets.append(f"status = ${len(params)}")
        # registra a revisão sempre que o status é tocado
        params.append(admin_email)
        sets.append(f"reviewed_by = ${len(params)}")
        sets.append("reviewed_at = NOW()")
    if not sets:
        raise HTTPException(status_code=422, detail="nada para atualizar")

    params.extend([ref_id, secao])
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"""
            UPDATE public.medicamentos_referencia_secoes
               SET {', '.join(sets)}, updated_at = NOW()
             WHERE referencia_id = ${len(params) - 1} AND secao = ${len(params)}
            RETURNING secao, conteudo, status, reviewed_at, reviewed_by
            """,
            *params,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Seção não encontrada")
    return SecaoOut(
        secao=row["secao"],
        conteudo=row["conteudo"],
        status=row["status"],
        reviewed_at=row["reviewed_at"].isoformat() if row["reviewed_at"] else None,
        reviewed_by=row["reviewed_by"],
    )


# ── Curadoria em massa ──────────────────────────────────────────────────────

@admin_router.patch("/referencia/{ref_id}/secoes", response_model=ReferenciaDetailOut)
async def bulk_patch_med_secoes(
    admin_email: AdminUser, ref_id: int, body: BulkMedStatus
) -> ReferenciaDetailOut:
    """
    Muda o status de TODAS as seções de UM medicamento de uma vez
    ("ativar todos os campos"). `only_pending=True` não reverte o que já foi
    desativado/ativado manualmente — só promove as `pending`.
    """
    where = ["referencia_id = $3"]
    params: list = [body.status, admin_email, ref_id]
    if body.only_pending:
        where.append("status = 'pending'")
    async with get_db_conn() as conn:
        exists = await conn.fetchval(
            "SELECT 1 FROM public.medicamentos_referencia WHERE id = $1", ref_id
        )
        if not exists:
            raise HTTPException(status_code=404, detail="Medicamento não encontrado")
        await conn.execute(
            f"""
            UPDATE public.medicamentos_referencia_secoes
               SET status = $1, reviewed_by = $2, reviewed_at = NOW(), updated_at = NOW()
             WHERE {' AND '.join(where)}
            """,
            *params,
        )
    return await get_referencia(admin_email, ref_id)


@admin_router.post("/referencia/bulk/status", response_model=BulkResult)
async def bulk_patch_all_secoes(
    admin_email: AdminUser, body: BulkGlobalStatus
) -> BulkResult:
    """
    Muda o status de seções em MASSA, em TODOS os medicamentos
    ("ativar todos os medicamentos"). Opcionalmente restringe a uma `secao` e/ou
    só às que estão `pending`. Retorna a contagem de seções afetadas.
    """
    where: list[str] = []
    params: list = [body.status, admin_email]
    if body.secao:
        if body.secao not in _VALID_SECOES:
            raise HTTPException(status_code=422, detail=f"seção inválida: {body.secao}")
        params.append(body.secao)
        where.append(f"secao = ${len(params)}")
    if body.only_pending:
        where.append("status = 'pending'")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    async with get_db_conn() as conn:
        res = await conn.execute(
            f"""
            UPDATE public.medicamentos_referencia_secoes
               SET status = $1, reviewed_by = $2, reviewed_at = NOW(), updated_at = NOW()
            {where_sql}
            """,
            *params,
        )
    try:
        updated = int(res.split()[-1])
    except (ValueError, IndexError):
        updated = 0
    log.info("referencia.bulk_status", actor=admin_email,
             status=body.status, secao=body.secao,
             only_pending=body.only_pending, updated=updated)
    return BulkResult(updated=updated)
