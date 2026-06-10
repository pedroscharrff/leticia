"""
Admin geral — Base de conhecimento (treinamentos) dos agentes.

Painel global (não per-tenant) onde o admin cura uma base de PDFs e textos
consultada por todos os tenants via a tool `consultar_base_conhecimento` no
skill `farmaceutico`.

Endpoints (todos exigem admin):
  POST   /admin/training/documents         — upload PDF (multipart) ou texto (JSON)
  GET    /admin/training/documents          — lista (filtros: status, category, q)
  GET    /admin/training/documents/{id}     — detalhe + preview de chunks
  PATCH  /admin/training/documents/{id}     — edita title/category/tags
  DELETE /admin/training/documents/{id}     — remove (cascade chunks + MinIO)
  POST   /admin/training/documents/{id}/reindex  — re-roda ingestão
  POST   /admin/training/search             — busca de teste pro admin validar

Ingestão é assíncrona via Celery (jobs.training_ingest_document).
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Annotated

import structlog
from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, UploadFile, status,
)
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_admin
from services import training_kb

log = structlog.get_logger()

admin_router = APIRouter(prefix="/admin/training", tags=["admin-training"])
AdminUser = Annotated[str, Depends(require_admin)]


# ── Schemas ──────────────────────────────────────────────────────────────────

class DocumentOut(BaseModel):
    id: str
    title: str
    category: str | None
    tags: list[str]
    source_type: str
    storage_url: str | None
    original_filename: str | None
    uploaded_by: str | None
    status: str
    chunk_count: int
    error: str | None
    created_at: str
    updated_at: str


class DocumentDetailOut(DocumentOut):
    chunks_preview: list[dict] = Field(default_factory=list)


class DocumentPatch(BaseModel):
    title: str | None = None
    category: str | None = None
    tags: list[str] | None = None


class TextDocumentIn(BaseModel):
    title: str
    category: str | None = None
    tags: list[str] = Field(default_factory=list)
    content: str


class SearchIn(BaseModel):
    query: str
    categoria: str | None = None
    tags: list[str] = Field(default_factory=list)
    k: int = 5


class SearchHitOut(BaseModel):
    document_id: str
    document_title: str
    category: str | None
    chunk_index: int
    content: str
    distance: float


def _row_to_out(r) -> DocumentOut:
    return DocumentOut(
        id=str(r["id"]),
        title=r["title"],
        category=r["category"],
        tags=list(r["tags"] or []),
        source_type=r["source_type"],
        storage_url=r["storage_url"],
        original_filename=r["original_filename"],
        uploaded_by=r["uploaded_by"],
        status=r["status"],
        chunk_count=r["chunk_count"],
        error=r["error"],
        created_at=r["created_at"].isoformat(),
        updated_at=r["updated_at"].isoformat(),
    )


def _enqueue_ingest(doc_id: str) -> None:
    """Dispara a Celery task. Import lazy pra não acoplar o módulo ao broker."""
    try:
        from workers.celery_app import training_ingest_document_task
        training_ingest_document_task.delay(doc_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("training.enqueue_failed", doc_id=doc_id, exc=str(exc))
        # Fallback: roda inline (degrada gracefully em dev sem broker).
        try:
            asyncio.create_task(training_kb.ingest_document(doc_id))
        except Exception as inner:  # noqa: BLE001
            log.error("training.ingest_inline_failed", doc_id=doc_id, exc=str(inner))


# ── Endpoints ────────────────────────────────────────────────────────────────

@admin_router.post(
    "/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_pdf_document(
    admin_email: AdminUser,
    file: UploadFile = File(...),
    title: str = Form(...),
    category: str | None = Form(None),
    tags: str | None = Form(None),  # CSV: "tag1,tag2"
) -> DocumentOut:
    """Sobe um PDF — cria row pendente e enfileira ingestão."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=422, detail="Arquivo deve ser .pdf")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="PDF vazio")

    try:
        public_url, key = await asyncio.to_thread(
            training_kb.upload_pdf_sync, data, file.filename,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.error("training.upload_failed", exc=str(exc))
        raise HTTPException(status_code=503, detail="Falha ao armazenar arquivo")

    tag_list = [t.strip() for t in (tags or "").split(",") if t.strip()]

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.training_documents
                (title, category, tags, source_type, storage_url, storage_key,
                 original_filename, uploaded_by, status)
            VALUES ($1,$2,$3,'pdf',$4,$5,$6,$7,'pending')
            RETURNING *
            """,
            title, category, tag_list, public_url, key, file.filename, admin_email,
        )

    _enqueue_ingest(str(row["id"]))
    log.info("training.uploaded.pdf", doc_id=str(row["id"]), title=title)
    return _row_to_out(row)


@admin_router.post(
    "/documents/text",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_text_document(body: TextDocumentIn, admin_email: AdminUser) -> DocumentOut:
    """Cria documento a partir de texto puro (sem PDF). Útil pra notas curtas."""
    if not body.content.strip():
        raise HTTPException(status_code=422, detail="Conteúdo vazio")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.training_documents
                (title, category, tags, source_type, raw_text, uploaded_by, status)
            VALUES ($1,$2,$3,'text',$4,$5,'pending')
            RETURNING *
            """,
            body.title, body.category, body.tags, body.content, admin_email,
        )
    _enqueue_ingest(str(row["id"]))
    log.info("training.uploaded.text", doc_id=str(row["id"]), title=body.title)
    return _row_to_out(row)


@admin_router.get("/documents", response_model=list[DocumentOut])
async def list_documents(
    _admin: AdminUser,
    status_filter: str | None = None,
    category: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[DocumentOut]:
    filters = []
    params: list = []
    if status_filter:
        params.append(status_filter)
        filters.append(f"status = ${len(params)}")
    if category:
        params.append(category)
        filters.append(f"category = ${len(params)}")
    if q:
        params.append(f"%{q.lower()}%")
        filters.append(f"LOWER(title) LIKE ${len(params)}")
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    params.append(min(max(1, limit), 500))
    params.append(max(0, offset))
    sql = (
        f"SELECT * FROM public.training_documents {where} "
        f"ORDER BY created_at DESC "
        f"LIMIT ${len(params) - 1} OFFSET ${len(params)}"
    )
    async with get_db_conn() as conn:
        rows = await conn.fetch(sql, *params)
    return [_row_to_out(r) for r in rows]


@admin_router.get("/documents/{doc_id}", response_model=DocumentDetailOut)
async def get_document(doc_id: str, _admin: AdminUser) -> DocumentDetailOut:
    _validate_uuid(doc_id)
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM public.training_documents WHERE id = $1::uuid", doc_id,
        )
        if not row:
            raise HTTPException(status_code=404, detail="Documento não encontrado")
        chunks = await conn.fetch(
            "SELECT chunk_index, content, tokens FROM public.training_chunks "
            "WHERE document_id = $1::uuid ORDER BY chunk_index LIMIT 20",
            doc_id,
        )
    base = _row_to_out(row).model_dump()
    base["chunks_preview"] = [
        {"chunk_index": c["chunk_index"], "tokens": c["tokens"],
         "preview": (c["content"] or "")[:500]}
        for c in chunks
    ]
    return DocumentDetailOut(**base)


@admin_router.patch("/documents/{doc_id}", response_model=DocumentOut)
async def patch_document(
    doc_id: str, body: DocumentPatch, _admin: AdminUser,
) -> DocumentOut:
    _validate_uuid(doc_id)
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Nada para atualizar")
    set_clauses = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(updates))
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            f"UPDATE public.training_documents SET {set_clauses}, updated_at = NOW() "
            f"WHERE id = $1::uuid RETURNING *",
            doc_id, *updates.values(),
        )
    if not row:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return _row_to_out(row)


@admin_router.delete(
    "/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
async def delete_document(doc_id: str, _admin: AdminUser) -> None:
    _validate_uuid(doc_id)
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "DELETE FROM public.training_documents WHERE id = $1::uuid "
            "RETURNING storage_key",
            doc_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    if row["storage_key"]:
        await asyncio.to_thread(training_kb.delete_pdf_sync, row["storage_key"])


@admin_router.post(
    "/documents/{doc_id}/reindex",
    response_model=DocumentOut,
)
async def reindex_document(doc_id: str, _admin: AdminUser) -> DocumentOut:
    _validate_uuid(doc_id)
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "UPDATE public.training_documents SET status='pending', error=NULL, "
            "updated_at=NOW() WHERE id = $1::uuid RETURNING *",
            doc_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    _enqueue_ingest(doc_id)
    return _row_to_out(row)


@admin_router.post("/search", response_model=list[SearchHitOut])
async def search_kb(body: SearchIn, _admin: AdminUser) -> list[SearchHitOut]:
    """Busca de teste — admin valida retrieval antes de confiar no agente."""
    hits = await training_kb.retrieve(
        body.query,
        categoria=body.categoria,
        tags=body.tags or None,
        k=min(max(1, body.k), 20),
    )
    return [
        SearchHitOut(
            document_id=h.document_id,
            document_title=h.document_title,
            category=h.category,
            chunk_index=h.chunk_index,
            content=h.content,
            distance=h.distance,
        )
        for h in hits
    ]


def _validate_uuid(s: str) -> None:
    try:
        uuid.UUID(s)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID inválido")
