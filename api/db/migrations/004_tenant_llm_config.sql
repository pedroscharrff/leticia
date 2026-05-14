-- Migration 004: Per-tenant LLM configuration (BYOK vs platform credits)
--
-- mode = 'credits' → tenant uses platform API keys, usage is debited from their plan
-- mode = 'byok'    → tenant brings their own API key (stored encrypted)

CREATE TABLE IF NOT EXISTS public.tenant_llm_config (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id         UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    mode              VARCHAR(10) NOT NULL DEFAULT 'credits'
                          CHECK (mode IN ('byok', 'credits')),
    provider          VARCHAR(20),          -- 'anthropic' | 'openai' | 'google' | 'ollama'
    orchestrator_model VARCHAR(100),        -- override per-node model (optional)
    analyst_model      VARCHAR(100),
    skill_model        VARCHAR(100),
    api_key_enc       BYTEA,               -- Fernet-encrypted API key (byok only)
    ollama_base_url   VARCHAR(500),        -- self-hosted Ollama URL
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_llm_config_tenant
    ON public.tenant_llm_config (tenant_id);

-- Insert a default credits row for all existing tenants
INSERT INTO public.tenant_llm_config (tenant_id, mode)
SELECT id, 'credits' FROM public.tenants
ON CONFLICT (tenant_id) DO NOTHING;
