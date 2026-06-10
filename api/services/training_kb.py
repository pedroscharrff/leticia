"""
Base de conhecimento curada pelo admin geral (RAG) — pipeline de ingestão e
retrieval.

Fluxo:
    upload (router) → MinIO + row em public.training_documents (status=pending)
        → Celery task ingest_document(doc_id)
            → baixa do MinIO (ou usa raw_text)
            → extrai texto (pypdf via services.bula_extractor.pdf_to_text)
            → chunk (~700 tokens, overlap 80)
            → embed em batch (OpenAI text-embedding-3-small, 1536d)
            → grava em public.training_chunks
            → atualiza training_documents.status='ready', chunk_count=N

    skill chama retrieve(query, categoria, tags, k)
        → embed da query
        → busca por cosine similarity em pgvector
        → retorna lista de Chunk (com título do doc)

Decisões:
    - Embedding via OpenAI (settings.openai_api_key já existe). Provider único
      por enquanto; abstrair só se aparecer 2º caso de uso.
    - Vector(1536) — text-embedding-3-small. Trocar dimensão exige nova migration.
    - Tudo no schema public (base GLOBAL, não per-tenant).
    - Bucket MinIO próprio: settings.minio_bucket_training (fallback ao bucket
      principal sob prefix `admin-training/`).
"""
from __future__ import annotations

import asyncio
import io
import json
import uuid
from dataclasses import dataclass

import structlog
from minio import Minio
from minio.error import S3Error

from config import settings
from db.postgres import get_db_conn

log = structlog.get_logger()

# ── Constantes de chunking ───────────────────────────────────────────────────

CHUNK_TOKENS_TARGET = 700
CHUNK_TOKENS_OVERLAP = 80
EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
EMBED_BATCH = 64

# Prefix dentro do bucket. Reusamos o bucket de mídia padrão (settings.minio_bucket)
# mas com prefix dedicado pra facilitar policies/limpeza.
TRAINING_PREFIX = "admin-training"

# Tamanho máximo do upload (PDF) — 50 MB. Acima disso provavelmente é cópia
# escaneada não-OCR; processar não vai ajudar o retrieval.
MAX_PDF_BYTES = 50 * 1024 * 1024


# ── Tipos públicos ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RetrievedChunk:
    """Trecho retornado pela busca semântica — pronto pra concatenar no prompt."""
    document_id: str
    document_title: str
    category: str | None
    chunk_index: int
    content: str
    distance: float  # cosine distance (0 = idêntico)


# ── MinIO helpers (uploads do admin) ─────────────────────────────────────────

_minio: Minio | None = None


def _client() -> Minio:
    """Cliente MinIO singleton — mesma instância do storage de ofertas."""
    global _minio
    if _minio is None:
        if not settings.minio_secret_key:
            raise RuntimeError("MinIO não configurado (MINIO_SECRET_KEY ausente).")
        _minio = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _minio


def _ensure_bucket(c: Minio) -> None:
    """Idempotente. Garante bucket + política pública de leitura."""
    bucket = settings.minio_bucket
    if c.bucket_exists(bucket):
        return
    c.make_bucket(bucket)
    policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action":    ["s3:GetObject"],
            "Resource":  [f"arn:aws:s3:::{bucket}/*"],
        }],
    }
    c.set_bucket_policy(bucket, json.dumps(policy))


def upload_pdf_sync(data: bytes, filename: str) -> tuple[str, str]:
    """Sobe um PDF para o bucket de treino. Retorna (public_url, storage_key).

    Síncrono (minio SDK é blocking). Chamadores async devem usar
    `asyncio.to_thread`.
    """
    if len(data) > MAX_PDF_BYTES:
        raise ValueError(f"PDF acima do limite de {MAX_PDF_BYTES // (1024*1024)} MB.")
    c = _client()
    _ensure_bucket(c)
    key = f"{TRAINING_PREFIX}/{uuid.uuid4().hex}.pdf"
    try:
        c.put_object(
            settings.minio_bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/pdf",
        )
    except S3Error as exc:
        raise RuntimeError(f"Falha ao subir PDF: {exc.code}") from exc
    public_url = f"{settings.minio_public_url.rstrip('/')}/{settings.minio_bucket}/{key}"
    return public_url, key


def download_pdf_sync(storage_key: str) -> bytes:
    c = _client()
    try:
        resp = c.get_object(settings.minio_bucket, storage_key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()
    except S3Error as exc:
        raise RuntimeError(f"Falha ao baixar PDF: {exc.code}") from exc


def delete_pdf_sync(storage_key: str) -> None:
    if not storage_key:
        return
    try:
        _client().remove_object(settings.minio_bucket, storage_key)
    except S3Error as exc:
        # delete falho não bloqueia o delete do row (storage é eventually consistent)
        log.warning("training.minio.delete_failed", key=storage_key, code=exc.code)


# ── Embeddings + chunking ────────────────────────────────────────────────────

def _get_openai_client():
    """Cliente OpenAI lazy. Levanta RuntimeError se chave não configurada."""
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY não configurada — embeddings indisponíveis.")
    from openai import OpenAI
    return OpenAI(api_key=settings.openai_api_key)


def _embed_batch_sync(texts: list[str]) -> list[list[float]]:
    """Chama OpenAI embeddings em batch. Síncrono."""
    if not texts:
        return []
    client = _get_openai_client()
    resp = client.embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]


async def _embed_batch(texts: list[str]) -> list[list[float]]:
    return await asyncio.to_thread(_embed_batch_sync, texts)


def _chunk_text(text: str) -> list[tuple[str, int]]:
    """Divide texto em chunks aproximados de CHUNK_TOKENS_TARGET tokens com
    overlap de CHUNK_TOKENS_OVERLAP. Retorna lista de (texto, tokens).

    Estratégia: tokeniza com tiktoken (cl100k_base — usado por embedding e
    Anthropic-compatível em estimativa), faz janelas com overlap. Sem
    sentence splitting fancy — bom o bastante para retrieval.
    """
    if not text or not text.strip():
        return []
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
    except Exception:  # noqa: BLE001
        # Fallback bobo: ~4 chars por token
        approx = max(1, len(text) // (CHUNK_TOKENS_TARGET * 4))
        size = max(1, len(text) // approx)
        return [(text[i:i + size], size // 4) for i in range(0, len(text), size)]

    if not tokens:
        return []

    step = CHUNK_TOKENS_TARGET - CHUNK_TOKENS_OVERLAP
    out: list[tuple[str, int]] = []
    for i in range(0, len(tokens), step):
        window = tokens[i:i + CHUNK_TOKENS_TARGET]
        if not window:
            break
        out.append((enc.decode(window), len(window)))
        if i + CHUNK_TOKENS_TARGET >= len(tokens):
            break
    return out


def _to_pgvector_literal(vec: list[float]) -> str:
    """Formato aceito por cast ::vector em SQL — evita precisar registrar
    codec asyncpg para o tipo `vector`."""
    return "[" + ",".join(f"{v:.7f}" for v in vec) + "]"


# ── Ingestão (chamada pelo Celery task) ──────────────────────────────────────

async def ingest_document(doc_id: str) -> dict:
    """Pipeline completo de ingestão de um documento.

    Idempotente: deleta chunks anteriores antes de inserir (suporta reindex).
    Marca status='processing' → 'ready' ou 'failed'. Erros viram coluna `error`.
    """
    async with get_db_conn() as conn:
        doc = await conn.fetchrow(
            "SELECT * FROM public.training_documents WHERE id = $1::uuid",
            doc_id,
        )
        if not doc:
            log.warning("training.ingest.not_found", doc_id=doc_id)
            return {"ok": False, "error": "doc_not_found"}

        await conn.execute(
            "UPDATE public.training_documents "
            "SET status='processing', error=NULL, updated_at=NOW() WHERE id=$1::uuid",
            doc_id,
        )

    try:
        # 1) Carrega texto bruto
        if doc["source_type"] == "pdf":
            if not doc["storage_key"]:
                raise RuntimeError("PDF sem storage_key — upload incompleto.")
            pdf_bytes = await asyncio.to_thread(download_pdf_sync, doc["storage_key"])
            from services.bula_extractor import pdf_to_text  # reuso
            text = pdf_to_text(pdf_bytes)
        elif doc["source_type"] == "text":
            text = doc["raw_text"] or ""
        else:
            raise RuntimeError(f"source_type não suportado: {doc['source_type']}")

        text = (text or "").strip()
        if not text:
            raise RuntimeError("Conteúdo vazio após extração — PDF pode ser escaneado (sem OCR).")

        # 2) Chunking
        chunks = _chunk_text(text)
        if not chunks:
            raise RuntimeError("Chunking produziu zero chunks.")

        # 3) Embed em batches
        async with get_db_conn() as conn:
            await conn.execute(
                "DELETE FROM public.training_chunks WHERE document_id = $1::uuid",
                doc_id,
            )

            for batch_start in range(0, len(chunks), EMBED_BATCH):
                batch = chunks[batch_start:batch_start + EMBED_BATCH]
                texts = [c[0] for c in batch]
                embeddings = await _embed_batch(texts)
                if len(embeddings) != len(batch):
                    raise RuntimeError("embedding count mismatch")
                for offset, ((content, tokens), vec) in enumerate(zip(batch, embeddings)):
                    if len(vec) != EMBED_DIM:
                        raise RuntimeError(
                            f"Dimensão inesperada do embedding: {len(vec)} (esperado {EMBED_DIM})"
                        )
                    await conn.execute(
                        """
                        INSERT INTO public.training_chunks
                            (document_id, chunk_index, content, tokens, embedding)
                        VALUES ($1::uuid, $2, $3, $4, $5::vector)
                        """,
                        doc_id,
                        batch_start + offset,
                        content,
                        tokens,
                        _to_pgvector_literal(vec),
                    )

            await conn.execute(
                "UPDATE public.training_documents "
                "SET status='ready', chunk_count=$2, error=NULL, updated_at=NOW() "
                "WHERE id=$1::uuid",
                doc_id, len(chunks),
            )

        log.info("training.ingest.ok", doc_id=doc_id, chunks=len(chunks))
        return {"ok": True, "chunks": len(chunks)}

    except Exception as exc:  # noqa: BLE001
        log.error("training.ingest.failed", doc_id=doc_id, exc=str(exc))
        try:
            async with get_db_conn() as conn:
                await conn.execute(
                    "UPDATE public.training_documents "
                    "SET status='failed', error=$2, updated_at=NOW() WHERE id=$1::uuid",
                    doc_id, str(exc)[:500],
                )
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "error": str(exc)}


# ── Retrieval (chamado pela tool consultar_base_conhecimento) ───────────────

async def retrieve(
    query: str,
    *,
    categoria: str | None = None,
    tags: list[str] | None = None,
    k: int = 4,
) -> list[RetrievedChunk]:
    """Busca semântica nos chunks. Filtros opcionais por categoria e tags."""
    if not query or not query.strip():
        return []
    try:
        vecs = await _embed_batch([query.strip()])
    except Exception as exc:  # noqa: BLE001
        log.warning("training.retrieve.embed_failed", exc=str(exc))
        return []
    if not vecs:
        return []
    vec_literal = _to_pgvector_literal(vecs[0])

    filters = ["d.status = 'ready'"]
    params: list = [vec_literal]
    if categoria:
        params.append(categoria)
        filters.append(f"d.category = ${len(params)}")
    if tags:
        params.append(list(tags))
        filters.append(f"d.tags && ${len(params)}::text[]")
    params.append(k)
    where = " AND ".join(filters)

    sql = f"""
        SELECT
            d.id::text AS document_id,
            d.title,
            d.category,
            c.chunk_index,
            c.content,
            (c.embedding <=> $1::vector) AS distance
        FROM public.training_chunks c
        JOIN public.training_documents d ON d.id = c.document_id
        WHERE {where}
        ORDER BY c.embedding <=> $1::vector
        LIMIT ${len(params)}
    """

    async with get_db_conn() as conn:
        rows = await conn.fetch(sql, *params)

    return [
        RetrievedChunk(
            document_id=r["document_id"],
            document_title=r["title"],
            category=r["category"],
            chunk_index=r["chunk_index"],
            content=r["content"],
            distance=float(r["distance"]),
        )
        for r in rows
    ]
