-- ═══════════════════════════════════════════════════════════════════════════
-- 007_inventory_sync_v2.sql — Inventory v2: pharma fields + sync hardening
--
-- Adds:
--   • pharma columns (lote, validade, fabricante, classe, principio_ativo, prescription)
--   • content_hash + last_synced_at + source_id for incremental diff sync
--   • pg_trgm extension + GIN index on products.name for fuzzy search
--   • similarity helpers
--   • Updates create_tenant_schema() so NEW tenants get the v2 layout
--   • Backfill loop for EXISTING tenant schemas
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Extension (idempotent, global) ────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── 2. Backfill existing tenant schemas ──────────────────────────────────────
DO $$
DECLARE
    t_schema TEXT;
BEGIN
    FOR t_schema IN
        SELECT schema_name FROM public.tenants WHERE active = TRUE
    LOOP
        -- Pharma-specific columns
        EXECUTE format($f$
            ALTER TABLE %I.products
                ADD COLUMN IF NOT EXISTS principio_ativo     VARCHAR(200),
                ADD COLUMN IF NOT EXISTS classe_terapeutica  VARCHAR(150),
                ADD COLUMN IF NOT EXISTS fabricante          VARCHAR(150),
                ADD COLUMN IF NOT EXISTS lote                VARCHAR(50),
                ADD COLUMN IF NOT EXISTS expires_at          DATE,
                ADD COLUMN IF NOT EXISTS prescription_required BOOLEAN DEFAULT FALSE
        $f$, t_schema);

        -- Sync engine columns
        EXECUTE format($f$
            ALTER TABLE %I.products
                ADD COLUMN IF NOT EXISTS content_hash        CHAR(40),
                ADD COLUMN IF NOT EXISTS last_synced_at      TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS missing_since       TIMESTAMPTZ,
                ADD COLUMN IF NOT EXISTS source_ref_external VARCHAR(200)
        $f$, t_schema);

        -- Indexes (use unique names per schema to avoid collisions in pg_class)
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.products USING GIN (name gin_trgm_ops)',
            'idx_products_name_trgm_' || t_schema, t_schema
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.products (active, stock_qty)',
            'idx_products_active_stock_' || t_schema, t_schema
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.products (principio_ativo)',
            'idx_products_pa_' || t_schema, t_schema
        );
        EXECUTE format(
            'CREATE INDEX IF NOT EXISTS %I ON %I.products (barcode)',
            'idx_products_barcode_' || t_schema, t_schema
        );
    END LOOP;
END $$;

-- ── 3. Update create_tenant_schema so NEW tenants are born with v2 layout ────
CREATE OR REPLACE FUNCTION create_tenant_schema(p_schema TEXT) RETURNS void AS $$
BEGIN
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', p_schema);

    -- Sessions
    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.sessions (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone            VARCHAR(20)  NOT NULL,
            session_key      VARCHAR(100) UNIQUE NOT NULL,
            customer_profile VARCHAR(30),
            channel_type     VARCHAR(30)  DEFAULT 'whatsapp_cloud',
            turn_count       INTEGER DEFAULT 0,
            created_at       TIMESTAMPTZ DEFAULT NOW(),
            updated_at       TIMESTAMPTZ DEFAULT NOW()
        )
    $t$, p_schema);

    -- Cart
    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.cart (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_key VARCHAR(100) NOT NULL UNIQUE,
            items       JSONB DEFAULT '[]',
            subtotal    NUMERIC(10,2) DEFAULT 0,
            stock_mode  VARCHAR(20)  DEFAULT 'catalogo',
            updated_at  TIMESTAMPTZ DEFAULT NOW()
        )
    $t$, p_schema);

    -- Skills config
    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.skills_config (
            skill_name     VARCHAR(50) PRIMARY KEY,
            ativo          BOOLEAN DEFAULT FALSE,
            llm_model      VARCHAR(100),
            llm_provider   VARCHAR(50),
            prompt_version VARCHAR(20) DEFAULT 'v1',
            config_json    JSONB DEFAULT '{}'
        )
    $t$, p_schema);

    -- Products / inventory (v2)
    EXECUTE format($t$
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

            -- Pharma fields (v2)
            principio_ativo      VARCHAR(200),
            classe_terapeutica   VARCHAR(150),
            fabricante           VARCHAR(150),
            lote                 VARCHAR(50),
            expires_at           DATE,
            prescription_required BOOLEAN DEFAULT FALSE,

            -- Sync engine fields (v2)
            content_hash         CHAR(40),
            last_synced_at       TIMESTAMPTZ,
            missing_since        TIMESTAMPTZ,
            source_ref_external  VARCHAR(200),

            source       VARCHAR(30) DEFAULT 'manual'
                CHECK (source IN ('manual','rest_api','sql','webhook','csv')),
            source_ref   VARCHAR(500),
            active       BOOLEAN DEFAULT TRUE,
            tags         TEXT[],
            meta         JSONB DEFAULT '{}',
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )
    $t$, p_schema);

    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.products(sku)',
                   'idx_products_sku_' || p_schema, p_schema);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.products USING GIN (name gin_trgm_ops)',
                   'idx_products_name_trgm_' || p_schema, p_schema);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.products(active, stock_qty)',
                   'idx_products_active_stock_' || p_schema, p_schema);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.products(principio_ativo)',
                   'idx_products_pa_' || p_schema, p_schema);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.products(barcode)',
                   'idx_products_barcode_' || p_schema, p_schema);

    -- Customers
    EXECUTE format($t$
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
    $t$, p_schema);

    -- Orders
    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.orders (
            id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            customer_id  UUID REFERENCES %I.customers(id),
            session_key  VARCHAR(100),
            items        JSONB NOT NULL DEFAULT '[]',
            subtotal     NUMERIC(10,2) DEFAULT 0,
            discount     NUMERIC(10,2) DEFAULT 0,
            total        NUMERIC(10,2) DEFAULT 0,
            status       VARCHAR(20) DEFAULT 'pending'
                CHECK (status IN ('pending','confirmed','processing','shipped','delivered','cancelled')),
            notes        TEXT,
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )
    $t$, p_schema, p_schema);

    -- Inventory sync log
    EXECUTE format($t$
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
    $t$, p_schema);

    -- Conversation logs
    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.conversation_logs (
            id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_key VARCHAR(100) NOT NULL,
            role        VARCHAR(10)  NOT NULL,
            content     TEXT         NOT NULL,
            skill_used  VARCHAR(50),
            llm_model   VARCHAR(100),
            tokens_in   INTEGER,
            tokens_out  INTEGER,
            latency_ms  INTEGER,
            created_at  TIMESTAMPTZ DEFAULT NOW()
        )
    $t$, p_schema);

    -- Usage metrics
    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.usage_metrics (
            month         DATE NOT NULL,
            conversations INTEGER DEFAULT 0,
            tokens_total  INTEGER DEFAULT 0,
            cost_usd      NUMERIC(10,4) DEFAULT 0,
            PRIMARY KEY (month)
        )
    $t$, p_schema);
END;
$$ LANGUAGE plpgsql;
