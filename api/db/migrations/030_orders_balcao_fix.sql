-- ═══════════════════════════════════════════════════════════════════════════
-- 030_orders_balcao_fix.sql
--
-- Corrige drift de schema na tabela <tenant>.orders que impedia o fluxo de
-- pré-atendimento (modo balcão) de gravar pedidos em produção.
--
-- Bugs corrigidos:
--   1) Coluna `requires_prescription` ausente.
--      `agents/tools/balcao.py` faz INSERT incluindo essa coluna mas ela
--      nunca foi adicionada à tabela criada por `create_tenant_schema()`
--      (definida em 003_saas_foundation.sql). Resultado em prod:
--        ERROR: column "requires_prescription" of relation "orders"
--               does not exist
--
--   2) CHECK constraint `orders_status_check` não permitia
--      'aguardando_balcao'.
--      A migration 026_preattendimento.sql afirmou no comentário que "a
--      coluna não tem CHECK constraint" — incorreto: a constraint existe
--      desde 003 e listava apenas pending/confirmed/processing/shipped/
--      delivered/cancelled. INSERTs do balcão falhavam com:
--        ERROR: new row violates check constraint "orders_status_check"
--
-- Aplicado em todos os schemas `tenant_%` ativos via loop. Idempotente.
--
-- ⚠️ Limitação conhecida: este patch corrige tenants EXISTENTES. A função
-- `create_tenant_schema()` em 003_saas_foundation.sql continua criando a
-- tabela orders sem `requires_prescription` e com a CHECK antiga. Tenant
-- criado depois desta migration precisa que esta migration seja re-rodada
-- no schema novo, OU a função do factory precisa ser corrigida em uma
-- migration futura (não fizemos aqui pra evitar duplicar 149 linhas).
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    t RECORD;
BEGIN
    FOR t IN
        SELECT schema_name
        FROM public.tenants
        WHERE active = TRUE
    LOOP
        RAISE NOTICE 'Patching orders in schema: %', t.schema_name;

        -- 1) Adiciona coluna requires_prescription se ainda não existir
        EXECUTE format($s$
            ALTER TABLE %I.orders
            ADD COLUMN IF NOT EXISTS requires_prescription
                BOOLEAN NOT NULL DEFAULT FALSE
        $s$, t.schema_name);

        -- 2) Recria CHECK constraint incluindo 'aguardando_balcao'
        EXECUTE format($s$
            ALTER TABLE %I.orders
            DROP CONSTRAINT IF EXISTS orders_status_check
        $s$, t.schema_name);

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
                    'aguardando_balcao'
                )
            )
        $s$, t.schema_name);
    END LOOP;
END$$;
