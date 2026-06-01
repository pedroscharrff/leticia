-- ─────────────────────────────────────────────────────────────────────────────
-- 053_orders_status_expired.sql
--
-- Adiciona 'expired' à CHECK constraint `orders_status_check` em todos os
-- schemas per-tenant. Usado pelo job de expiração de carrinhos abandonados
-- (workers/jobs/expire_carts.py) para registrar o snapshot do pedido que
-- não vingou.
--
-- Status válidos depois desta migration:
--   pending, confirmed, processing, shipped, delivered, cancelled,
--   aguardando_balcao, expired
--
-- Padrão usado: função `add_orders_expired_status_to_schema(p_schema)`
-- idempotente (DROP IF EXISTS + ADD constraint completa) + plug em
-- `public.create_tenant_schema_full` (aggregator de bootstrap, migration 048,
-- invariante [[tenant-bootstrap-aggregator]]) para que tenants novos já
-- nasçam com o status estendido — em vez de duplicar a tabela orders inteira
-- numa migration nova, como aviso em 030.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.add_orders_expired_status_to_schema(p_schema TEXT)
RETURNS VOID AS $func$
BEGIN
    EXECUTE format($s$
        ALTER TABLE %I.orders
        DROP CONSTRAINT IF EXISTS orders_status_check
    $s$, p_schema);

    EXECUTE format($s$
        ALTER TABLE %I.orders
        ADD CONSTRAINT orders_status_check CHECK (
            status IN (
                'pending',
                'confirmed',
                'processing',
                'shipped',
                'delivered',
                'cancelled',
                'aguardando_balcao',
                'expired'
            )
        )
    $s$, p_schema);
END;
$func$ LANGUAGE plpgsql;


-- Re-define o aggregator para incluir a nova extensão. Mantém todas as
-- chamadas anteriores na MESMA ordem que 048 — só adiciona a linha nova
-- ao fim. Toda criação de tenant nova já vai passar por aqui.
CREATE OR REPLACE FUNCTION public.create_tenant_schema_full(p_schema TEXT)
RETURNS VOID AS $$
BEGIN
    PERFORM public.create_tenant_schema(p_schema);

    PERFORM public.add_agent_traces_to_schema(p_schema);          -- migration 020
    PERFORM public.add_sales_attempts_to_cart(p_schema);          -- migration 010
    PERFORM public.create_tenant_schema_memory_ext(p_schema);     -- migration 023
    PERFORM public.create_tenant_schema_relations_ext(p_schema);  -- migration 024
    PERFORM public.create_tenant_schema_recovery_ext(p_schema);   -- migration 025
    PERFORM public.create_tenant_schema_source_fix(p_schema);     -- migration 038
    PERFORM public.add_orders_expired_status_to_schema(p_schema); -- migration 053
END;
$$ LANGUAGE plpgsql;


-- ── Backfill: aplica a tenants existentes ───────────────────────────────────
-- Tenants criados antes desta migration têm a CHECK antiga (sem 'expired').
-- O loop abaixo aplica só essa extensão pra evitar correr o aggregator inteiro
-- num momento desnecessário (já foi corrido em 048).
DO $migr$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            PERFORM public.add_orders_expired_status_to_schema(t.schema_name);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'orders.status expired backfill falhou para %: % (SQLSTATE %)',
                t.schema_name, SQLERRM, SQLSTATE;
        END;
    END LOOP;
END $migr$;
