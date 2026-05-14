-- ═══════════════════════════════════════════════════════════════════════════
-- 008_skill_examples.sql — Per-tenant few-shot examples for "training" agents
--
-- Tenants curate examples of ideal responses per skill. The graph injects up
-- to N matching examples into the skill's system prompt at run time, giving
-- the LLM concrete templates to follow without retraining.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.tenant_skill_examples (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    skill_name      VARCHAR(50) NOT NULL,
    user_message    TEXT NOT NULL,
    ideal_response  TEXT NOT NULL,
    tags            TEXT[]     DEFAULT '{}',
    notes           TEXT,                            -- internal notes from the tenant
    enabled         BOOLEAN    NOT NULL DEFAULT TRUE,
    weight          INTEGER    NOT NULL DEFAULT 1,   -- higher = preferred when limit kicks in
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_skill_examples_tenant_skill
    ON public.tenant_skill_examples (tenant_id, skill_name)
    WHERE enabled = TRUE;

-- Trigram index so we can later rank examples by similarity to the current
-- customer message instead of always returning the same N.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX IF NOT EXISTS idx_skill_examples_message_trgm
    ON public.tenant_skill_examples USING GIN (user_message gin_trgm_ops);
