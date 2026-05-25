-- ═══════════════════════════════════════════════════════════════════════════
-- 033_bula_secoes.sql
--
-- Texto integral das bulas da ANVISA, particionado por seção, com índice
-- de full-text search em português. Alimenta a tool `consultar_bula_secao`
-- usada pelo skill farmaceutico — garante que o agente cite trechos REAIS
-- da bula em vez de inventar/divagar.
--
-- Por que FTS e não embeddings:
--   • A maioria das perguntas é lexical ("dose dipirona criança", "interação
--     com warfarina", "pode tomar grávida"). FTS resolve.
--   • Zero dependência externa (sem API de embedding). Tudo no Postgres.
--   • Quando aparecer caso semântico que FTS não pega, plugamos pgvector
--     como segunda camada — sem refactor desta tabela.
--
-- Granularidade: uma linha por (num_processo, secao). Permite ranking só na
-- seção relevante e retornar trecho com `ts_headline`.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.bula_secoes (
    id              BIGSERIAL    PRIMARY KEY,
    num_processo    TEXT         NOT NULL,
    secao           TEXT         NOT NULL,   -- slug: indicacoes, posologia, ...
    secao_titulo    TEXT,                    -- título original como veio no PDF
    conteudo        TEXT         NOT NULL,
    conteudo_tsv    TSVECTOR     GENERATED ALWAYS AS (
                        to_tsvector('portuguese', coalesce(conteudo, ''))
                    ) STORED,
    char_count      INTEGER      NOT NULL DEFAULT 0,
    source          TEXT         NOT NULL DEFAULT 'anvisa_bula_paciente',
    extracted_at    TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (num_processo, secao),
    FOREIGN KEY (num_processo)
        REFERENCES public.medicamentos_anvisa(num_processo)
        ON DELETE CASCADE
);
-- Nota: unaccent foi tentado no GENERATED mas é STABLE — Postgres exige
-- IMMUTABLE em generated columns. O dicionário 'portuguese' do tsvector já
-- faz stemming que cobre a maior parte dos casos (cefaleia/cefaléia, etc.).

CREATE INDEX IF NOT EXISTS idx_bula_secoes_tsv
    ON public.bula_secoes USING GIN (conteudo_tsv);

CREATE INDEX IF NOT EXISTS idx_bula_secoes_num_processo
    ON public.bula_secoes (num_processo);

CREATE INDEX IF NOT EXISTS idx_bula_secoes_secao
    ON public.bula_secoes (secao);
