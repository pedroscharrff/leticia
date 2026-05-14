-- ─── Global (SaaS metadata) ────────────────────────────────────────────────

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TABLE IF NOT EXISTS public.tenants (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name         VARCHAR(200)  NOT NULL,
    api_key      VARCHAR(100)  UNIQUE NOT NULL,
    callback_url TEXT          NOT NULL,
    plan         VARCHAR(20)   NOT NULL DEFAULT 'basic',
    schema_name  VARCHAR(63)   UNIQUE NOT NULL,
    active       BOOLEAN       DEFAULT TRUE,
    created_at   TIMESTAMPTZ   DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.plans (
    plan_name     VARCHAR(20) PRIMARY KEY,
    monthly_limit INTEGER,          -- NULL = unlimited
    base_skills   TEXT[],
    price_brl     NUMERIC(10,2)
);

INSERT INTO public.plans (plan_name, monthly_limit, base_skills, price_brl)
VALUES
    ('basic',      500,  ARRAY['farmaceutico'],                                              97.00),
    ('pro',        2000, ARRAY['farmaceutico','principio_ativo','genericos','vendedor'],     297.00),
    ('enterprise', NULL, ARRAY['farmaceutico','principio_ativo','genericos','vendedor','recuperador'], 697.00)
ON CONFLICT DO NOTHING;

-- ─── Per-tenant schema factory ──────────────────────────────────────────────

CREATE OR REPLACE FUNCTION create_tenant_schema(p_schema TEXT) RETURNS void AS $$
BEGIN
    EXECUTE format('CREATE SCHEMA IF NOT EXISTS %I', p_schema);

    EXECUTE format($t$
        CREATE TABLE IF NOT EXISTS %I.sessions (
            id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            phone            VARCHAR(20)  NOT NULL,
            session_key      VARCHAR(100) UNIQUE NOT NULL,
            customer_profile VARCHAR(30),
            turn_count       INTEGER DEFAULT 0,
            created_at       TIMESTAMPTZ DEFAULT NOW(),
            updated_at       TIMESTAMPTZ DEFAULT NOW()
        )
    $t$, p_schema);

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
