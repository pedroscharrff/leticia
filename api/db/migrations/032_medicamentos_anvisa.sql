-- ═══════════════════════════════════════════════════════════════════════════
-- 032_medicamentos_anvisa.sql
--
-- Base de conhecimento global de medicamentos consumida da API pública da
-- ANVISA (consultas.anvisa.gov.br). NÃO é per-tenant — todas as farmácias
-- compartilham o mesmo catálogo regulatório. Fica em `public` de propósito
-- para evitar drift entre schemas (cf. tenant-schema-drift).
--
-- Duas tabelas:
--   public.medicamentos_anvisa    — catálogo persistente, fonte de verdade local
--   public.bulario_query_cache    — cache de buscas (termo → IDs)
--
-- Estratégia de uso pelas tools:
--   1. Buscar local primeiro (trigram em nome_produto_norm + ILIKE em
--      principio_ativo) → resposta sub-100ms se hit
--   2. Miss/stale → chama ANVISA, upserta os top-N detalhes em paralelo,
--      grava cache da query
--   3. Refresh job (futuro) atualiza linhas com stale_after < NOW()
-- ═══════════════════════════════════════════════════════════════════════════

-- pg_trgm: índice GIN com gin_trgm_ops para busca fuzzy por nome
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── Catálogo de medicamentos ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.medicamentos_anvisa (
    id                   BIGSERIAL    PRIMARY KEY,
    num_processo         TEXT         NOT NULL UNIQUE,   -- chave natural ANVISA
    id_produto           BIGINT,                         -- codigoProduto / idProduto
    numero_registro      TEXT,
    nome_produto         TEXT         NOT NULL,
    nome_produto_norm    TEXT         NOT NULL,          -- lower(nome) p/ trigram
    nome_comercial       TEXT,                           -- vem do detail
    principio_ativo      TEXT,                           -- vem do detail
    razao_social         TEXT,                           -- fabricante (search)
    cnpj                 TEXT,
    classes_terapeuticas TEXT[]       NOT NULL DEFAULT '{}',
    atcs                 TEXT[]       NOT NULL DEFAULT '{}',
    mes_ano_vencimento   TEXT,                           -- "MMAAAA"
    codigo_bula_paciente TEXT,                           -- JWT p/ baixar PDF
    codigo_bula_profissional TEXT,
    raw_search           JSONB,                          -- payload do search
    raw_detail           JSONB,                          -- payload do detail
    has_detail           BOOLEAN      NOT NULL DEFAULT FALSE,
    source               TEXT         NOT NULL DEFAULT 'anvisa_api',
    fetched_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    detail_fetched_at    TIMESTAMPTZ,
    stale_after          TIMESTAMPTZ  NOT NULL DEFAULT (NOW() + INTERVAL '90 days')
);

CREATE INDEX IF NOT EXISTS idx_medicamentos_anvisa_nome_trgm
    ON public.medicamentos_anvisa USING GIN (nome_produto_norm gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_medicamentos_anvisa_principio_ativo
    ON public.medicamentos_anvisa (principio_ativo);

CREATE INDEX IF NOT EXISTS idx_medicamentos_anvisa_classes
    ON public.medicamentos_anvisa USING GIN (classes_terapeuticas);

CREATE INDEX IF NOT EXISTS idx_medicamentos_anvisa_stale
    ON public.medicamentos_anvisa (stale_after) WHERE has_detail;

-- ── Cache de buscas (termo → lista de num_processo) ─────────────────────────
CREATE TABLE IF NOT EXISTS public.bulario_query_cache (
    query_norm    TEXT         PRIMARY KEY,   -- lower(termo de busca)
    num_processos TEXT[]       NOT NULL,      -- ordem dos resultados
    total_anvisa  INTEGER,                    -- totalElements retornado
    hits          INTEGER      NOT NULL DEFAULT 0,
    cached_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at    TIMESTAMPTZ  NOT NULL DEFAULT (NOW() + INTERVAL '30 days')
);

CREATE INDEX IF NOT EXISTS idx_bulario_query_cache_expires
    ON public.bulario_query_cache (expires_at);
