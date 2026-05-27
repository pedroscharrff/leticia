-- ─────────────────────────────────────────────────────────────────────────────
-- 040_propagate_cart_sales_attempts.sql
--
-- Aplica `public.add_sales_attempts_to_cart()` em todos os tenants existentes.
--
-- A função foi criada em migration anterior (010) e é chamada no onboarding
-- de novos tenants, mas tenants criados ANTES dessa migration nunca
-- receberam a coluna `sales_attempts` na tabela `cart`. Isso quebra o
-- `context.db.save_failed` no skill vendedor e pode causar cascata de erros.
-- ─────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    s TEXT;
BEGIN
    FOR s IN
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name LIKE 'tenant\_%' ESCAPE '\'
    LOOP
        BEGIN
            PERFORM public.add_sales_attempts_to_cart(s);
        EXCEPTION
            WHEN OTHERS THEN
                RAISE WARNING 'add_sales_attempts_to_cart failed for %: %', s, SQLERRM;
        END;
    END LOOP;
END $$;
