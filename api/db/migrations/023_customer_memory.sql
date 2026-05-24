-- ─────────────────────────────────────────────────────────────────────────────
-- Customer memory: extensão da tabela {schema}.customers para suportar
-- memória de longo prazo do cliente (alergias, medicamentos contínuos,
-- preferências, LTV e segmentação).
--
-- Aplica em:
--   1. Todos os schemas de tenants existentes (loop dinâmico)
--   2. Schema factory create_tenant_schema (para tenants futuros)
-- ─────────────────────────────────────────────────────────────────────────────

DO $migr$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            EXECUTE format($s$
                ALTER TABLE %I.customers
                    ADD COLUMN IF NOT EXISTS allergies        TEXT[]      DEFAULT '{}',
                    ADD COLUMN IF NOT EXISTS continuous_meds  JSONB       DEFAULT '[]',
                    ADD COLUMN IF NOT EXISTS preferences      JSONB       DEFAULT '{}',
                    ADD COLUMN IF NOT EXISTS ltv              NUMERIC(12,2) DEFAULT 0,
                    ADD COLUMN IF NOT EXISTS last_purchase_at TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS segment          VARCHAR(20) DEFAULT 'esporadico'
            $s$, t.schema_name);

            -- Índices úteis: GIN em alergias + segmentação
            EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.customers USING GIN (allergies)',
                           'idx_customers_allergies_' || t.schema_name, t.schema_name);
            EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.customers (segment)',
                           'idx_customers_segment_' || t.schema_name, t.schema_name);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped %: %', t.schema_name, SQLERRM;
        END;
    END LOOP;
END $migr$;


-- Atualiza schema factory (provisional_tenant_schema) — append new columns ao
-- statement de CREATE TABLE customers. Como a factory já roda IF NOT EXISTS,
-- tenants novos terão sempre as colunas.
CREATE OR REPLACE FUNCTION public.create_tenant_schema_memory_ext(p_schema TEXT)
RETURNS VOID AS $$
BEGIN
    -- helper idempotente: se o tenant já existe via factory antiga,
    -- garantimos que as novas colunas existem.
    EXECUTE format($s$
        ALTER TABLE %I.customers
            ADD COLUMN IF NOT EXISTS allergies        TEXT[]      DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS continuous_meds  JSONB       DEFAULT '[]',
            ADD COLUMN IF NOT EXISTS preferences      JSONB       DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS ltv              NUMERIC(12,2) DEFAULT 0,
            ADD COLUMN IF NOT EXISTS last_purchase_at TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS segment          VARCHAR(20) DEFAULT 'esporadico'
    $s$, p_schema);
END;
$$ LANGUAGE plpgsql;
