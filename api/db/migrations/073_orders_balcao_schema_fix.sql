-- ─────────────────────────────────────────────────────────────────────────────
-- 073_orders_balcao_schema_fix.sql
--
-- Corrige schema da tabela <tenant>.orders para tenants CRIADOS DEPOIS da
-- migration 009 (que sobrescreveu `create_tenant_schema` removendo a coluna
-- `requires_prescription` e o status 'aguardando_balcao' da CHECK constraint).
--
-- Histórico:
--   migration 003 — `create_tenant_schema` original com requires_prescription
--                   + 'aguardando_balcao' na CHECK ✅
--   migration 009 — `CREATE OR REPLACE FUNCTION create_tenant_schema` que
--                   PERDEU requires_prescription e 'aguardando_balcao' ❌
--   migration 030 — `ALTER TABLE` em tenants EXISTENTES, mas NÃO corrigiu a
--                   função `create_tenant_schema` em si → tenants NOVOS
--                   continuavam quebrados (⚠️ limitação documentada na própria
--                   030, linhas 25-31)
--   migration 053 — Criou `add_orders_expired_status_to_schema` + plug no
--                   aggregator `create_tenant_schema_full`. Corrigiu a CHECK
--                   constraint (inclui 'aguardando_balcao') mas NÃO adicionou
--                   a coluna `requires_prescription`.
--   migration 073 — (esta) Adiciona `requires_prescription` e garante a CHECK
--                   constraint correta via function + plug no aggregator,
--                   seguindo o MESMO padrão da 053 (função idempotente +
--                   `create_tenant_schema_full` + backfill).
--
-- Efeito: toda tenant nova que passar pelo aggregator e toda tenant existente
-- no backfill terão a coluna e a CHECK corretas. Indempotente.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.add_balcao_orders_fields_to_schema(p_schema TEXT)
RETURNS VOID AS $func$
BEGIN
    -- 1) Adiciona requires_prescription se ainda não existir (idempotente)
    EXECUTE format($s$
        ALTER TABLE %I.orders
        ADD COLUMN IF NOT EXISTS requires_prescription
            BOOLEAN NOT NULL DEFAULT FALSE
    $s$, p_schema);

    -- 2) Garante a CHECK constraint com 'aguardando_balcao' (idempotente)
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
-- chamadas anteriores na MESMA ordem que 053 — só adiciona a linha nova.
-- Toda criação de tenant nova já vai passar por aqui.
CREATE OR REPLACE FUNCTION public.create_tenant_schema_full(p_schema TEXT)
RETURNS VOID AS $$
BEGIN
    PERFORM public.create_tenant_schema(p_schema);

    PERFORM public.add_agent_traces_to_schema(p_schema);             -- migration 020
    PERFORM public.add_sales_attempts_to_cart(p_schema);             -- migration 010
    PERFORM public.create_tenant_schema_memory_ext(p_schema);        -- migration 023
    PERFORM public.create_tenant_schema_relations_ext(p_schema);     -- migration 024
    PERFORM public.create_tenant_schema_recovery_ext(p_schema);      -- migration 025
    PERFORM public.create_tenant_schema_source_fix(p_schema);        -- migration 038
    PERFORM public.add_balcao_orders_fields_to_schema(p_schema);     -- migration 073
    PERFORM public.add_orders_expired_status_to_schema(p_schema);    -- migration 053
END;
$$ LANGUAGE plpgsql;


-- ── Backfill: aplica a todos os tenants existentes ───────────────────────────
-- Garante que mesmo tenants criados antes desta migration recebam a correção.
DO $migr$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            PERFORM public.add_balcao_orders_fields_to_schema(t.schema_name);
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'balcao fields backfill falhou para %: % (SQLSTATE %)',
                t.schema_name, SQLERRM, SQLSTATE;
        END;
    END LOOP;
END $migr$;
