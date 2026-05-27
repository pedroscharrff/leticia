-- ─────────────────────────────────────────────────────────────────────────────
-- 038_inventory_source_xlsx_google_sheets.sql
--
-- Adiciona 'google_sheets' e 'xlsx' aos CHECK constraints da coluna `source`
-- em products e customers de todos os schemas de tenant existentes.
--
-- Sem isso, os connectors novos (Google Sheets, Excel) batem em
-- "products_source_check" violation no INSERT/UPSERT.
--
-- Defensivo: alguns tenants antigos (pré-migration 009) podem não ter a coluna
-- `source` em `customers`. Detectamos antes de tentar ALTER.
-- ─────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    s TEXT;
    has_source BOOLEAN;
BEGIN
    FOR s IN
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name LIKE 'tenant\_%' ESCAPE '\'
    LOOP
        -- products.source — existe desde a migration 003 em todos os tenants
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = s AND table_name = 'products' AND column_name = 'source'
        ) INTO has_source;

        IF has_source THEN
            EXECUTE format(
                'ALTER TABLE %I.products DROP CONSTRAINT IF EXISTS products_source_check',
                s
            );
            EXECUTE format(
                'ALTER TABLE %I.products ADD CONSTRAINT products_source_check '
                'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
                s
            );
        END IF;

        -- customers.source — só existe em tenants criados após migration 009
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = s AND table_name = 'customers' AND column_name = 'source'
        ) INTO has_source;

        IF has_source THEN
            EXECUTE format(
                'ALTER TABLE %I.customers DROP CONSTRAINT IF EXISTS customers_source_check',
                s
            );
            EXECUTE format(
                'ALTER TABLE %I.customers ADD CONSTRAINT customers_source_check '
                'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
                s
            );
        END IF;
    END LOOP;
END $$;

-- ─────────────────────────────────────────────────────────────────────────────
-- Função de extensão chamada após create_tenant_schema() para garantir que
-- novos tenants já nasçam com os sources corretos. Segue o mesmo padrão das
-- funções create_tenant_schema_memory_ext / _relations_ext / _recovery_ext.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION public.create_tenant_schema_source_fix(p_schema TEXT)
RETURNS VOID AS $$
DECLARE
    has_source BOOLEAN;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = 'products' AND column_name = 'source'
    ) INTO has_source;

    IF has_source THEN
        EXECUTE format(
            'ALTER TABLE %I.products DROP CONSTRAINT IF EXISTS products_source_check',
            p_schema
        );
        EXECUTE format(
            'ALTER TABLE %I.products ADD CONSTRAINT products_source_check '
            'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
            p_schema
        );
    END IF;

    SELECT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = p_schema AND table_name = 'customers' AND column_name = 'source'
    ) INTO has_source;

    IF has_source THEN
        EXECUTE format(
            'ALTER TABLE %I.customers DROP CONSTRAINT IF EXISTS customers_source_check',
            p_schema
        );
        EXECUTE format(
            'ALTER TABLE %I.customers ADD CONSTRAINT customers_source_check '
            'CHECK (source IN (''manual'',''rest_api'',''sql'',''webhook'',''csv'',''xlsx'',''google_sheets''))',
            p_schema
        );
    END IF;
END;
$$ LANGUAGE plpgsql;
