-- ═══════════════════════════════════════════════════════════════════════════
-- 034_bula_unaccent.sql
--
-- Conserta o FTS das bulas para casos com/sem acento. Sintoma: query
-- "dose maxima adulto" não casava com bula "dose máxima adulto" porque
-- o dicionário 'portuguese' faz stemming mas NÃO dobra acentos.
--
-- Solução: aplicar unaccent() antes do to_tsvector. unaccent é STABLE
-- por default e Postgres exige IMMUTABLE em colunas GENERATED — então
-- criamos um wrapper IMMUTABLE (padrão da própria doc do Postgres).
-- ═══════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS unaccent;

-- Wrapper IMMUTABLE — declarar IMMUTABLE manualmente é seguro porque o
-- dicionário 'unaccent' é estático e nunca muda em runtime (é o padrão
-- recomendado pela doc do Postgres p/ usar unaccent em índices/generated).
CREATE OR REPLACE FUNCTION public.f_unaccent(text)
RETURNS text
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
STRICT
AS $$
  SELECT public.unaccent('public.unaccent', $1);
$$;

-- Recria a coluna tsv usando f_unaccent. ALTER TABLE ... DROP COLUMN ...
-- também derruba o índice GIN que dependia dela; recriamos abaixo.
ALTER TABLE public.bula_secoes DROP COLUMN IF EXISTS conteudo_tsv;

ALTER TABLE public.bula_secoes
    ADD COLUMN conteudo_tsv TSVECTOR
    GENERATED ALWAYS AS (
        to_tsvector('portuguese', public.f_unaccent(coalesce(conteudo, '')))
    ) STORED;

CREATE INDEX IF NOT EXISTS idx_bula_secoes_tsv
    ON public.bula_secoes USING GIN (conteudo_tsv);
