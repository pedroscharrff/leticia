-- ═══════════════════════════════════════════════════════════════════════════
-- 010_sales_config.sql — Per-tenant required customer fields for sales
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Tenants pick which customer fields the vendedor agent MUST collect before
-- closing an order via `criar_pedido`. The agent will insist up to
-- max_attempts; after that it relays `fallback_message` and stops trying.

CREATE TABLE IF NOT EXISTS public.tenant_sales_config (
    tenant_id        UUID PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,

    -- Required customer fields. Allowed keys (validated in API):
    --   nome, cpf_cnpj, email, telefone, cep, rua, numero,
    --   complemento, bairro, cidade, estado, observacoes
    required_fields  TEXT[] NOT NULL DEFAULT ARRAY['nome']::TEXT[],

    max_attempts     INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 10),
    fallback_message TEXT NOT NULL DEFAULT
        'Para finalizar o pedido eu preciso desses dados. Quando puder me passar, é só me chamar de volta que finalizo na hora!',

    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);

-- Backfill defaults for existing tenants
INSERT INTO public.tenant_sales_config (tenant_id)
SELECT id FROM public.tenants
ON CONFLICT (tenant_id) DO NOTHING;

-- ── Per-session attempt counter on cart (tenant schemas) ────────────────────
DO $$
DECLARE
    s TEXT;
BEGIN
    FOR s IN
        SELECT schema_name FROM public.tenants WHERE schema_name IS NOT NULL
    LOOP
        EXECUTE format(
            'ALTER TABLE %I.cart ADD COLUMN IF NOT EXISTS sales_attempts INTEGER NOT NULL DEFAULT 0',
            s
        );
    END LOOP;
END$$;

-- Update schema factory so newly-created tenants get the column too
CREATE OR REPLACE FUNCTION public.add_sales_attempts_to_cart(p_schema TEXT) RETURNS void AS $$
BEGIN
    EXECUTE format(
        'ALTER TABLE %I.cart ADD COLUMN IF NOT EXISTS sales_attempts INTEGER NOT NULL DEFAULT 0',
        p_schema
    );
END$$ LANGUAGE plpgsql;
