-- ═══════════════════════════════════════════════════════════════════════════
-- 067_medicamentos_referencia.sql
--
-- Base curada de medicamentos de REFERÊNCIA (marca original ↔ princípio ativo),
-- ingerida do "Guia de Medicamentos Genéricos". Preenche um buraco que a API da
-- ANVISA não cobre bem: responder "qual o original/de referência da Buspirona?".
-- NÃO é per-tenant — vive em `public`, compartilhada por todas as farmácias
-- (cf. tenant-schema-drift), igual a medicamentos_anvisa.
--
-- Duas tabelas, espelhando a simetria medicamentos_anvisa ↔ bula_secoes:
--   public.medicamentos_referencia          — PAI: mapeamento seguro
--   public.medicamentos_referencia_secoes   — FILHA: seções clínicas curadas
--
-- ⚠️  Curadoria: a fonte (guia de 2001) tem info clínica DESATUALIZADA. Por isso
-- cada seção clínica nasce `status='pending'` e só é exposta ao agente quando o
-- superadmin revisa e marca `active`. A camada de repo/tool filtra por
-- `status='active'` — o gate é determinístico, não depende de prompt.
-- O mapeamento (princípio ativo, marca, forma, categoria) não tem esse risco e
-- fica sempre disponível.
-- ═══════════════════════════════════════════════════════════════════════════

-- pg_trgm já criado na 032, mas idempotente por garantia.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── PAI: mapeamento seguro (sempre exposto pela tool) ───────────────────────
CREATE TABLE IF NOT EXISTS public.medicamentos_referencia (
    id                   BIGSERIAL    PRIMARY KEY,
    principio_ativo      TEXT         NOT NULL,          -- "DOBUTAMINA (CLORIDRATO)"
    principio_ativo_norm TEXT         NOT NULL,          -- lower/normalizado p/ trigram
    nome_referencia      TEXT,                           -- "DOBUTREX" (marca original)
    nome_referencia_norm TEXT,
    forma_farmaceutica   TEXT,                           -- "Solução injetável - 250mg"
    categoria            TEXT,                           -- rodapé: "Agentes Inotrópicos"
    source               TEXT         NOT NULL DEFAULT 'guia_genericos_2001',
    page_ref             INTEGER,                         -- página do PDF (rastreio)
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (principio_ativo, nome_referencia)
);

CREATE INDEX IF NOT EXISTS idx_med_ref_pa_trgm
    ON public.medicamentos_referencia USING GIN (principio_ativo_norm gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_med_ref_ref_trgm
    ON public.medicamentos_referencia USING GIN (nome_referencia_norm gin_trgm_ops);

-- ── FILHA: seções clínicas, curadas individualmente ─────────────────────────
CREATE TABLE IF NOT EXISTS public.medicamentos_referencia_secoes (
    id              BIGSERIAL    PRIMARY KEY,
    referencia_id   BIGINT       NOT NULL
                        REFERENCES public.medicamentos_referencia(id) ON DELETE CASCADE,
    secao           TEXT         NOT NULL,   -- slug: indicacoes, posologia, contraindicacoes,
                                             --       efeitos_adversos, interacoes, precaucoes
    conteudo        TEXT         NOT NULL,
    status          TEXT         NOT NULL DEFAULT 'pending',  -- pending | active | disabled
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     TEXT,                                     -- email do admin que revisou
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (referencia_id, secao),
    CHECK (status IN ('pending', 'active', 'disabled'))
);

-- Índice parcial: o repo/tool só lê seções `active`, então otimiza o caminho quente.
CREATE INDEX IF NOT EXISTS idx_med_ref_secoes_active
    ON public.medicamentos_referencia_secoes (referencia_id)
 WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_med_ref_secoes_referencia
    ON public.medicamentos_referencia_secoes (referencia_id);
