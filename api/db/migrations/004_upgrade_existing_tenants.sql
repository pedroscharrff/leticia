-- ═══════════════════════════════════════════════════════════════════════════
-- 004_upgrade_existing_tenants.sql
-- Adiciona as tabelas novas (products, customers, orders, inventory_sync_log)
-- em todos os schemas de tenants já existentes.
-- Safe to re-run: usa CREATE TABLE IF NOT EXISTS.
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants WHERE active = TRUE LOOP
        RAISE NOTICE 'Upgrading tenant schema: %', t.schema_name;

        -- products
        EXECUTE format($s$
            CREATE TABLE IF NOT EXISTS %I.products (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                sku          VARCHAR(100) UNIQUE,
                name         VARCHAR(300) NOT NULL,
                brand        VARCHAR(100),
                category     VARCHAR(100),
                description  TEXT,
                price        NUMERIC(10,2),
                stock_qty    INTEGER DEFAULT 0,
                unit         VARCHAR(20) DEFAULT 'un',
                barcode      VARCHAR(50),
                source       VARCHAR(30) DEFAULT 'manual',
                source_ref   VARCHAR(500),
                active       BOOLEAN DEFAULT TRUE,
                tags         TEXT[],
                meta         JSONB DEFAULT '{}',
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                updated_at   TIMESTAMPTZ DEFAULT NOW()
            )
        $s$, t.schema_name);

        -- customers
        EXECUTE format($s$
            CREATE TABLE IF NOT EXISTS %I.customers (
                id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                phone             VARCHAR(20) UNIQUE NOT NULL,
                name              VARCHAR(200),
                email             VARCHAR(200),
                doc               VARCHAR(20),
                birth_date        DATE,
                tags              TEXT[],
                notes             TEXT,
                lgpd_consent_at   TIMESTAMPTZ,
                last_contact_at   TIMESTAMPTZ,
                total_orders      INTEGER DEFAULT 0,
                total_spent       NUMERIC(12,2) DEFAULT 0,
                meta              JSONB DEFAULT '{}',
                created_at        TIMESTAMPTZ DEFAULT NOW(),
                updated_at        TIMESTAMPTZ DEFAULT NOW()
            )
        $s$, t.schema_name);

        -- orders
        EXECUTE format($s$
            CREATE TABLE IF NOT EXISTS %I.orders (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                customer_id  UUID,
                session_key  VARCHAR(100),
                items        JSONB NOT NULL DEFAULT '[]',
                subtotal     NUMERIC(10,2) DEFAULT 0,
                discount     NUMERIC(10,2) DEFAULT 0,
                total        NUMERIC(10,2) DEFAULT 0,
                status       VARCHAR(20) DEFAULT 'pending',
                notes        TEXT,
                created_at   TIMESTAMPTZ DEFAULT NOW(),
                updated_at   TIMESTAMPTZ DEFAULT NOW()
            )
        $s$, t.schema_name);

        -- inventory_sync_log
        EXECUTE format($s$
            CREATE TABLE IF NOT EXISTS %I.inventory_sync_log (
                id           BIGSERIAL PRIMARY KEY,
                connector    VARCHAR(30) NOT NULL,
                status       VARCHAR(20) NOT NULL DEFAULT 'ok',
                records_in   INTEGER DEFAULT 0,
                records_upd  INTEGER DEFAULT 0,
                errors       JSONB DEFAULT '[]',
                duration_ms  INTEGER,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
        $s$, t.schema_name);

        -- agent_traces (new)
        EXECUTE format($s$
            CREATE TABLE IF NOT EXISTS %I.agent_traces (
                id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                session_key  VARCHAR(100) NOT NULL,
                phone        VARCHAR(20),
                message_in   TEXT,
                steps        JSONB NOT NULL DEFAULT '[]',
                final_response TEXT,
                skill_used   VARCHAR(50),
                intent       VARCHAR(200),
                confidence   NUMERIC(4,3),
                latency_ms   INTEGER,
                error        TEXT,
                created_at   TIMESTAMPTZ DEFAULT NOW()
            )
        $s$, t.schema_name);

        EXECUTE format($s$
            CREATE INDEX IF NOT EXISTS agent_traces_session_key_idx
            ON %I.agent_traces (session_key)
        $s$, t.schema_name);

        -- add channel_type column to sessions if missing
        EXECUTE format($s$
            ALTER TABLE %I.sessions
            ADD COLUMN IF NOT EXISTS channel_type VARCHAR(30) DEFAULT 'whatsapp_cloud'
        $s$, t.schema_name);

        -- seed skills_config from catalog for missing skills
        EXECUTE format($s$
            INSERT INTO %I.skills_config (skill_name)
            SELECT skill_name FROM public.skill_catalog
            WHERE active = TRUE
            ON CONFLICT (skill_name) DO NOTHING
        $s$, t.schema_name);

    END LOOP;
END;
$$;

-- Ensure subscriptions exist for all tenants (trial)
INSERT INTO public.subscriptions (tenant_id, plan_name, provider, status)
SELECT id, plan, 'manual', 'trialing'
FROM public.tenants
WHERE active = TRUE
  AND id NOT IN (SELECT tenant_id FROM public.subscriptions)
ON CONFLICT DO NOTHING;
