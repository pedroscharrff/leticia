-- Migration 066 — Base de conhecimento curada pelo admin geral (RAG)
--
-- Tabelas globais no schema public:
--   * training_documents — metadados do documento (PDF/texto) subido pelo admin
--   * training_chunks    — pedaços do documento com embedding vetorial (pgvector)
--
-- Decisões:
--   * Base GLOBAL, não per-tenant. Admin geral cura uma única base; todos os
--     tenants consultam o mesmo acervo (ex.: sítios de ligação, interações
--     medicamentosas, literatura técnica de farmácia).
--   * Embedding: vector(1536) — alinhado a OpenAI text-embedding-3-small
--     (provider já habilitado em settings.openai_api_key). Trocar o provider
--     exige migration nova (alterar dimensão é destrutivo).
--   * IVFFLAT com cosine — bom para acervos pequenos/médios (até dezenas de
--     milhares de chunks). Para HNSW, precisaríamos pgvector 0.5+ — fica como
--     upgrade futuro se a base crescer muito.
--   * Idempotente: CREATE EXTENSION/TABLE/INDEX IF NOT EXISTS.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS public.training_documents (
    id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    title              TEXT         NOT NULL,
    category           TEXT,                          -- ex.: "sitios_ligacao", "interacoes", "dosagem_pediatrica"
    tags               TEXT[]       NOT NULL DEFAULT '{}',
    source_type        TEXT         NOT NULL DEFAULT 'pdf' CHECK (source_type IN ('pdf','text','url')),
    storage_url        TEXT,                          -- URL pública no MinIO (NULL para source_type='text')
    storage_key        TEXT,                          -- key interna do MinIO (para delete)
    original_filename  TEXT,
    raw_text           TEXT,                          -- usado quando source_type='text' (sem PDF)
    uploaded_by        TEXT,                          -- email do admin que subiu
    status             TEXT         NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','processing','ready','failed')),
    chunk_count        INT          NOT NULL DEFAULT 0,
    error              TEXT,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_training_documents_status   ON public.training_documents (status);
CREATE INDEX IF NOT EXISTS idx_training_documents_category ON public.training_documents (category);
CREATE INDEX IF NOT EXISTS idx_training_documents_tags     ON public.training_documents USING GIN (tags);
CREATE INDEX IF NOT EXISTS idx_training_documents_created  ON public.training_documents (created_at DESC);

CREATE TABLE IF NOT EXISTS public.training_chunks (
    id           UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id  UUID         NOT NULL REFERENCES public.training_documents(id) ON DELETE CASCADE,
    chunk_index  INT          NOT NULL,
    content      TEXT         NOT NULL,
    tokens       INT          NOT NULL DEFAULT 0,
    embedding    vector(1536),                        -- text-embedding-3-small
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (document_id, chunk_index)
);

-- IVFFLAT para busca por similaridade de cosseno. lists=100 é razoável até
-- ~100k chunks; reindexar com lists ~ sqrt(N) quando crescer.
CREATE INDEX IF NOT EXISTS idx_training_chunks_embedding
    ON public.training_chunks USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

CREATE INDEX IF NOT EXISTS idx_training_chunks_document ON public.training_chunks (document_id, chunk_index);
