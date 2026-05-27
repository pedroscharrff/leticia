-- ─────────────────────────────────────────────────────────────────────────────
-- 038_inventory_source_xlsx_google_sheets.sql
--
-- Adiciona 'google_sheets' e 'xlsx' aos CHECK constraints da coluna `source`
-- em products e customers de todos os schemas de tenant existentes.
--
-- Sem isso, os connectors novos (Google Sheets, Excel) batem em
-- "products_source_check" violation no INSERT/UPSERT.
-- ─────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    s TEXT;
BEGIN
    FOR s IN
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name LIKE 'tenant\_%' ESCAPE '\'
    LOOP
        -- products
        EXECUTE format(
            'ALTER TABLE %I.products DROP CONSTRAINT IF EXISTS products_source_check',
            s
        );
        EXECUTE format(
            'ALTER TABLE %I.products ADD CONSTRAINT products_source_check '
            'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
            s
        );

        -- customers (mesmo problema potencial se algum dia importarmos clientes via planilha)
        EXECUTE format(
            'ALTER TABLE %I.customers DROP CONSTRAINT IF EXISTS customers_source_check',
            s
        );
        EXECUTE format(
            'ALTER TABLE %I.customers ADD CONSTRAINT customers_source_check '
            'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
            s
        );
    END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Função de extensão chamada após create_tenant_schema() para garantir que
-- novos tenants já nasçam com os sources corretos. Segue o mesmo padrão das
-- funções create_tenant_schema_memory_ext / _relations_ext / _recovery_ext.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.create_tenant_schema_source_fix(p_schema TEXT)
RETURNS VOID AS $$
BEGIN
    EXECUTE format(
        'ALTER TABLE %I.products DROP CONSTRAINT IF EXISTS products_source_check',
        p_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.products ADD CONSTRAINT products_source_check '
        'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
        p_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.customers DROP CONSTRAINT IF EXISTS customers_source_check',
        p_schema
    );
    EXECUTE format(
        'ALTER TABLE %I.customers ADD CONSTRAINT customers_source_check '
        'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
        p_schema
    );
END;
$$ LANGUAGE plpgsql;
