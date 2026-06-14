"""
Admin geral — Medicamentos (bulário ANVISA + base de referência).

Painel global (não per-tenant). Duas fontes públicas compartilhadas:
  • public.medicamentos_anvisa        — bulário cacheado da ANVISA (read-only)
  • public.medicamentos_referencia(+secoes) — guia curado de referência (editável)

A curadoria das seções clínicas mora aqui: cada seção tem status
pending/active/disabled; só `active` é exposta ao agente (filtro no
referencia_repo). O superadmin revisa o conteúdo (fonte de 2001) e ativa.

Endpoints (todos exigem admin):
  GET    /admin/medicamentos/bulario                 — lista/busca ANVISA (read-only)
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


# ── Bulário ANVISA (read-only) ──────────────────────────────────────────────

@admin_router.get("/bulario", response_model=list[BularioOut])
async def list_bulario(
    _admin: AdminUser,
    q: str | None = Query(None, description="busca por nome ou princípio ativo"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[BularioOut]:
    norm = _normalize(q) if q else None
    async with get_db_conn() as conn:
        if norm:
            rows = await conn.fetch(
                """
                SELECT num_processo, nome_produto, principio_ativo, razao_social,
                       classes_terapeuticas, has_detail
                  FROM public.medicamentos_anvisa
                 WHERE (nome_produto_norm % $1 AND similarity(nome_produto_norm, $1) >= 0.30)
                    OR principio_ativo ILIKE '%' || $1 || '%'
                 ORDER BY similarity(nome_produto_norm, $1) DESC NULLS LAST
                 LIMIT $2 OFFSET $3
                """,
                norm, limit, offset,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT num_processo, nome_produto, principio_ativo, razao_social,
                       classes_terapeuticas, has_detail
                  FROM public.medicamentos_anvisa
                 ORDER BY nome_produto
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
        )
        for r in rows
    ]


# ── Medicamentos de referência ──────────────────────────────────────────────

@admin_router.get("/referencia", response_model=list[ReferenciaListOut])
async def list_referencia(
    _admin: AdminUser,
    q: str | None = Query(None, description="busca por princípio ativo ou marca"),
    pendentes: bool = Query(False, description="só os que têm seção pendente"),
    limit: int = Query(50, ge=1, le=200),
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
