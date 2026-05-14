-- ═══════════════════════════════════════════════════════════════════════════
-- 006_persona_and_prompts.sql — Per-tenant agent persona + prompt overrides
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Tenant persona ────────────────────────────────────────────────────────
-- One row per tenant. Drives the "humanization layer" injected into every
-- skill's system prompt.
CREATE TABLE IF NOT EXISTS public.tenant_persona (
    tenant_id            UUID PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,

    -- Identity
    agent_name           VARCHAR(60)  NOT NULL DEFAULT 'Atendente',
    agent_gender         VARCHAR(10)  NOT NULL DEFAULT 'feminino'
                            CHECK (agent_gender IN ('feminino','masculino','neutro')),
    pharmacy_name        VARCHAR(150),
    pharmacy_tagline     VARCHAR(200),

    -- Voice & style
    tone                 VARCHAR(20)  NOT NULL DEFAULT 'amigavel'
                            CHECK (tone IN ('formal','amigavel','informal','profissional','divertido')),
    formality            VARCHAR(10)  NOT NULL DEFAULT 'voce'
                            CHECK (formality IN ('tu','voce','senhor')),
    emoji_usage          VARCHAR(10)  NOT NULL DEFAULT 'light'
                            CHECK (emoji_usage IN ('none','light','moderate','heavy')),
    response_length      VARCHAR(10)  NOT NULL DEFAULT 'medium'
                            CHECK (response_length IN ('short','medium','long')),
    language             VARCHAR(10)  NOT NULL DEFAULT 'pt-BR',

    -- Free-form personalization
    persona_bio          TEXT,            -- 1-3 lines: "Você é a Letícia, atendente carinhosa..."
    greeting_template    TEXT,            -- ex.: "Oi! Aqui é a Letícia da Drogaria X 💊"
    signature            VARCHAR(200),    -- ex.: "— Letícia | Drogaria X"
    custom_instructions  TEXT,            -- regras extras injetadas em todas as skills
    forbidden_topics     TEXT,            -- ex.: "Não discuta política. Não compare com concorrentes."
    catchphrases         TEXT[],          -- frases típicas da marca

    -- Business context
    business_hours       VARCHAR(200),    -- "Seg-Sex 8h-22h, Sáb 8h-18h"
    location             VARCHAR(300),
    delivery_info        TEXT,
    payment_methods      TEXT,
    website              VARCHAR(200),
    instagram            VARCHAR(100),

    created_at           TIMESTAMPTZ DEFAULT NOW(),
    updated_at           TIMESTAMPTZ DEFAULT NOW()
);

-- ── 2. Per-tenant per-skill prompt overrides ─────────────────────────────────
-- NULL system_prompt = use catalog default. extra_instructions is always
-- appended to whatever base prompt is in effect.
CREATE TABLE IF NOT EXISTS public.tenant_skill_prompts (
    tenant_id          UUID         NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    skill_name         VARCHAR(50)  NOT NULL REFERENCES public.skill_catalog(skill_name) ON DELETE CASCADE,
    system_prompt      TEXT,
    extra_instructions TEXT,
    updated_by         VARCHAR(200),
    updated_at         TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (tenant_id, skill_name)
);
CREATE INDEX IF NOT EXISTS idx_tenant_prompts_tenant
    ON public.tenant_skill_prompts(tenant_id);

-- ── 3. Backfill: ensure every existing tenant has a default persona row ──────
INSERT INTO public.tenant_persona (tenant_id, agent_name, pharmacy_name)
SELECT id, 'Atendente', name FROM public.tenants
ON CONFLICT (tenant_id) DO NOTHING;
