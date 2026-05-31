-- ─────────────────────────────────────────────────────────────────────────────
-- Tenant bootstrap aggregator.
--
-- Histórico: novas colunas/tabelas por-tenant foram entregues em migrations
-- separadas (023 memory_ext, 024 relations_ext, 025 recovery_ext, 038 source_fix,
-- 010 sales_attempts, 020 agent_traces). Cada call site de criação de tenant
-- (`routers/onboarding.py`, `routers/tenants.py`, `scripts/create_tenant.py`)
-- precisava ser atualizado a mão para chamar a extensão nova — e ao menos um
-- ficou para trás (tenants.py + create_tenant.py não chamavam memory_ext,
-- relations_ext, recovery_ext, agent_traces). Resultado: schema drift em
-- tenants novos, e endpoints como /portal/recovery/stats quebrando.
--
-- Esta migration:
--   1) Define `public.create_tenant_schema_full(p_schema)` — ponto único de
--      bootstrap que chama o `create_tenant_schema` base e TODAS as extensões
--      pós-criação na ordem correta. Daqui pra frente, toda extensão de schema
--      per-tenant nova deve ser anexada aqui (e SÓ aqui — call sites não
--      precisam mudar).
--   2) Faz backfill: roda o aggregator em todos os tenants existentes para
--      curar drift acumulado de schemas criados antes de cada extensão existir
--      (ou via call sites incompletos).
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.create_tenant_schema_full(p_schema TEXT)
RETURNS VOID AS $$
BEGIN
    -- Base: tabelas core (products, customers, cart, orders, conversation_logs,
    -- skills_config, ...). Recriado a cada migration que muda o template.
    PERFORM public.create_tenant_schema(p_schema);

    -- Extensões pós-criação. Cada uma é idempotente (ADD COLUMN IF NOT EXISTS,
    -- CREATE TABLE IF NOT EXISTS, etc.), então é seguro chamar em schema já
    -- bootstrapado.
    PERFORM public.add_agent_traces_to_schema(p_schema);          -- migration 020
    PERFORM public.add_sales_attempts_to_cart(p_schema);          -- migration 010
    PERFORM public.create_tenant_schema_memory_ext(p_schema);     -- migration 023
    PERFORM public.create_tenant_schema_relations_ext(p_schema);  -- migration 024
    PERFORM public.create_tenant_schema_recovery_ext(p_schema);   -- migration 025
    PERFORM public.create_tenant_schema_source_fix(p_schema);     -- migration 038
END;
$$ LANGUAGE plpgsql;


-- ── Backfill: cura drift em tenants existentes ──────────────────────────────
DO $migr$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            PERFORM public.create_tenant_schema_full(t.schema_name);
        EXCEPTION WHEN OTHERS THEN
            -- Aqui logamos o SQLERRM (e não só RAISE NOTICE genérico) para que
            -- drift residual fique visível nos logs da migration, em vez de
            -- ser silenciosamente engolido como nas 023/025 originais.
            RAISE WARNING 'Bootstrap aggregator falhou para schema %: % (SQLSTATE %)',
                t.schema_name, SQLERRM, SQLSTATE;
        END;
    END LOOP;
END $migr$;
