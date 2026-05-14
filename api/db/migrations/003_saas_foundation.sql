-- ═══════════════════════════════════════════════════════════════════════════
-- 003_saas_foundation.sql  — SaaS multi-tenant foundation
-- ═══════════════════════════════════════════════════════════════════════════

-- ── 1. Enrich plans table ────────────────────────────────────────────────────
ALTER TABLE public.plans
    ADD COLUMN IF NOT EXISTS features    JSONB         NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS limits      JSONB         NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS stripe_price_id TEXT,
    ADD COLUMN IF NOT EXISTS asaas_plan_id   TEXT,
    ADD COLUMN IF NOT EXISTS trial_days  INTEGER       NOT NULL DEFAULT 7,
    ADD COLUMN IF NOT EXISTS active      BOOLEAN       NOT NULL DEFAULT TRUE;

-- Update existing plans with features/limits
UPDATE public.plans SET
    features = '{"channels":["whatsapp"],"connectors":["manual"],"skills_max":1,"users_max":2}',
    limits   = '{"msgs_month":500,"tokens_month":500000,"products_max":200,"customers_max":500}'
WHERE plan_name = 'basic';

UPDATE public.plans SET
    features = '{"channels":["whatsapp","telegram"],"connectors":["manual","rest_api"],"skills_max":4,"users_max":5}',
    limits   = '{"msgs_month":2000,"tokens_month":2000000,"products_max":2000,"customers_max":5000}'
WHERE plan_name = 'pro';

UPDATE public.plans SET
    features = '{"channels":["whatsapp","telegram","instagram","web"],"connectors":["manual","rest_api","sql","webhook"],"skills_max":99,"users_max":99}',
    limits   = '{"msgs_month":null,"tokens_month":null,"products_max":null,"customers_max":null}'
WHERE plan_name = 'enterprise';

-- ── 2. RBAC on tenant_users ──────────────────────────────────────────────────
ALTER TABLE public.tenant_users
    ADD COLUMN IF NOT EXISTS role VARCHAR(20) NOT NULL DEFAULT 'owner'
        CHECK (role IN ('owner','manager','operator','viewer')),
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS mfa_secret    TEXT;   -- TOTP seed (encrypted at app layer)

-- ── 3. Tenant secrets (app-layer encryption via Fernet) ──────────────────────
CREATE TABLE IF NOT EXISTS public.tenant_secrets (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    key          VARCHAR(100) NOT NULL,
    value_enc    BYTEA        NOT NULL,   -- Fernet encrypted
    created_at   TIMESTAMPTZ  DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  DEFAULT NOW(),
    UNIQUE (tenant_id, key)
);
CREATE INDEX IF NOT EXISTS idx_tenant_secrets_tenant ON public.tenant_secrets(tenant_id);

-- ── 4. Global audit log ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.audit_events (
    id         BIGSERIAL    PRIMARY KEY,
    tenant_id  UUID         REFERENCES public.tenants(id) ON DELETE SET NULL,
    actor_type VARCHAR(20)  NOT NULL DEFAULT 'user',  -- 'admin','user','system'
    actor_id   VARCHAR(200) NOT NULL,                  -- email or service name
    action     VARCHAR(100) NOT NULL,
    target     VARCHAR(200),
    meta       JSONB        DEFAULT '{}',
    ip_addr    INET,
    created_at TIMESTAMPTZ  DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant    ON public.audit_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_actor     ON public.audit_events(actor_id,  created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action    ON public.audit_events(action);

-- ── 5. Tenant channels ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenant_channels (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    channel_type    VARCHAR(30) NOT NULL CHECK (channel_type IN ('whatsapp_cloud','whatsapp_zapi','telegram','instagram','web_widget')),
    display_name    VARCHAR(100),
    credentials_ref VARCHAR(100),       -- key in tenant_secrets
    webhook_secret  VARCHAR(200),       -- HMAC signing secret (stored encrypted)
    active          BOOLEAN     NOT NULL DEFAULT TRUE,
    config_json     JSONB       DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, channel_type, display_name)
);
CREATE INDEX IF NOT EXISTS idx_channels_tenant ON public.tenant_channels(tenant_id);

-- ── 6. Subscriptions & billing ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.subscriptions (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID        UNIQUE NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    plan_name           VARCHAR(20) NOT NULL REFERENCES public.plans(plan_name),
    provider            VARCHAR(20) NOT NULL DEFAULT 'stripe' CHECK (provider IN ('stripe','asaas','manual')),
    external_id         VARCHAR(200),                       -- Stripe subscription ID / Asaas ID
    status              VARCHAR(20) NOT NULL DEFAULT 'trialing'
                            CHECK (status IN ('trialing','active','past_due','canceled','paused')),
    trial_ends_at       TIMESTAMPTZ,
    current_period_start TIMESTAMPTZ,
    current_period_end   TIMESTAMPTZ,
    canceled_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.invoices (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    subscription_id UUID        REFERENCES public.subscriptions(id),
    provider        VARCHAR(20) NOT NULL,
    external_id     VARCHAR(200),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','paid','failed','refunded','void')),
    amount_brl      NUMERIC(10,2) NOT NULL,
    due_date        DATE,
    paid_at         TIMESTAMPTZ,
    invoice_url     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_invoices_tenant ON public.invoices(tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS public.usage_records (
    id          BIGSERIAL PRIMARY KEY,
    tenant_id   UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    metric      VARCHAR(50) NOT NULL,   -- 'msgs','tokens_in','tokens_out','api_calls'
    qty         BIGINT      NOT NULL DEFAULT 0,
    period      DATE        NOT NULL,   -- first day of month
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (tenant_id, metric, period)
);
CREATE INDEX IF NOT EXISTS idx_usage_tenant ON public.usage_records(tenant_id, period DESC);

-- ── 7. Skill catalog (global, admin-managed) ─────────────────────────────────
CREATE TABLE IF NOT EXISTS public.skill_catalog (
    skill_name        VARCHAR(50) PRIMARY KEY,
    display_name      VARCHAR(100) NOT NULL,
    description       TEXT,
    category          VARCHAR(50)  DEFAULT 'general',
    plan_min          VARCHAR(20)  NOT NULL DEFAULT 'basic' REFERENCES public.plans(plan_name),
    channel_compat    TEXT[]       DEFAULT ARRAY['whatsapp_cloud','whatsapp_zapi'],
    default_llm       VARCHAR(100),
    default_provider  VARCHAR(50),
    prompt_template   TEXT,
    tools_json        JSONB        DEFAULT '[]',
    active            BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ  DEFAULT NOW()
);

-- Seed with existing skills
INSERT INTO public.skill_catalog (skill_name, display_name, description, category, plan_min) VALUES
    ('farmaceutico',    'Farmacêutico',        'Responde dúvidas sobre medicamentos, posologia e contraindicações',    'saude',    'basic'),
    ('principio_ativo', 'Princípio Ativo',     'Busca medicamentos por princípio ativo e sugere genéricos',            'catalogo', 'pro'),
    ('genericos',       'Genéricos',           'Verifica disponibilidade e preços de medicamentos genéricos',          'catalogo', 'pro'),
    ('vendedor',        'Vendedor',            'Conduz a jornada de compra e gerencia o carrinho de compras',          'vendas',   'pro'),
    ('recuperador',     'Recuperador',         'Reativa clientes inativos, envia promoções e recupera abandonos',      'crm',      'enterprise')
ON CONFLICT DO NOTHING;

-- ── 8. Update tenant schema factory to include new tables ────────────────────
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

    -- Skills config (references global catalog)
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

    -- Products / inventory
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
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.products(name text_pattern_ops)', 'idx_products_name_' || p_schema, p_schema);
    EXECUTE format('CREATE INDEX IF NOT EXISTS %I ON %I.products(sku)', 'idx_products_sku_' || p_schema, p_schema);

    -- Customers (CRM)
    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.customers (
            id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone             VARCHAR(20) UNIQUE NOT NULL,
            name              VARCHAR(200),
            email             VARCHAR(200),
            doc               VARCHAR(20),   -- CPF/CNPJ
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
