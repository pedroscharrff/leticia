-- ── Garante que TODOS os tenants tenham a tabela agent_traces ────────────────
-- A migration 004 criou agent_traces apenas em tenants existentes naquele
-- momento. Tenants criados depois via signup não receberam a tabela porque a
-- função create_tenant_schema() não a incluía.
--
-- Esta migration:
--   1) Cria agent_traces (idempotente) em todo tenant existente
--   2) Expõe public.add_agent_traces_to_schema(schema) para ser chamada pelo
--      onboarding.py logo após create_tenant_schema(), garantindo que novos
--      tenants também recebam a tabela.

-- ── 1. Helper reusável ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION public.add_agent_traces_to_schema(p_schema TEXT)
RETURNS void AS $func$
BEGIN
    EXECUTE format($s$
        CREATE TABLE IF NOT EXISTS %I.agent_traces (
            id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_key    VARCHAR(100) NOT NULL,
            phone          VARCHAR(20),
            message_in     TEXT,
            steps          JSONB NOT NULL DEFAULT '[]',
            final_response TEXT,
            skill_used     VARCHAR(50),
            intent         VARCHAR(200),
            confidence     NUMERIC(4,3),
            latency_ms     INTEGER,
            error          TEXT,
            created_at     TIMESTAMPTZ DEFAULT NOW()
        )
    $s$, p_schema);

    EXECUTE format($s$
        CREATE INDEX IF NOT EXISTS agent_traces_session_key_idx
        ON %I.agent_traces (session_key)
    $s$, p_schema);

    EXECUTE format($s$
        CREATE INDEX IF NOT EXISTS agent_traces_created_at_idx
        ON %I.agent_traces (created_at DESC)
    $s$, p_schema);
END;
$func$ LANGUAGE plpgsql;


-- ── 2. Backfill em tenants existentes ────────────────────────────────────────
DO $$
DECLARE
    schema_rec RECORD;
BEGIN
    FOR schema_rec IN
        SELECT schema_name FROM public.tenants WHERE schema_name IS NOT NULL
    LOOP
        BEGIN
            PERFORM public.add_agent_traces_to_schema(schema_rec.schema_name);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped %: %', schema_rec.schema_name, SQLERRM;
        END;
    END LOOP;
END $$;
