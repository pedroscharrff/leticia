-- ─────────────────────────────────────────────────────────────────────────────
-- Relações de produtos (cross-sell / substitutos / combos) + regras de frete.
--
-- 1) {schema}.product_relations — grafo de produtos relacionados (por tenant).
--    Usado pelo tool recomendar_complementos(product_id) que alimenta o
--    vendedor com sugestões de complemento após adicionar item ao carrinho.
--
-- 2) public.tenant_shipping_rules — regras de frete por faixa de CEP.
--    Usado pelo tool calcular_frete(cep, subtotal).
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. product_relations (por tenant) ────────────────────────────────────────

DO $migr$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            EXECUTE format($s$
                CREATE TABLE IF NOT EXISTS %I.product_relations (
                    id                  BIGSERIAL PRIMARY KEY,
                    product_id          UUID         NOT NULL REFERENCES %I.products(id) ON DELETE CASCADE,
                    related_product_id  UUID         NOT NULL REFERENCES %I.products(id) ON DELETE CASCADE,
                    relation_type       VARCHAR(20)  NOT NULL DEFAULT 'complementar'
                        CHECK (relation_type IN ('complementar','substituto','combo')),
                    weight              REAL         NOT NULL DEFAULT 0.5,
                    notes               TEXT,
                    created_at          TIMESTAMPTZ  DEFAULT NOW(),
                    UNIQUE (product_id, related_product_id, relation_type),
                    CHECK (product_id <> related_product_id)
                )
            $s$, t.schema_name, t.schema_name, t.schema_name);

            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON %I.product_relations (product_id, relation_type, weight DESC)',
                'idx_prod_rel_lookup_' || t.schema_name, t.schema_name);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped product_relations %: %', t.schema_name, SQLERRM;
        END;
    END LOOP;
END $migr$;


-- Helper para novos tenants (chamado a partir de onboarding.py)
CREATE OR REPLACE FUNCTION public.create_tenant_schema_relations_ext(p_schema TEXT)
RETURNS VOID AS $$
BEGIN
    EXECUTE format($s$
        CREATE TABLE IF NOT EXISTS %I.product_relations (
            id                  BIGSERIAL PRIMARY KEY,
            product_id          UUID         NOT NULL REFERENCES %I.products(id) ON DELETE CASCADE,
            related_product_id  UUID         NOT NULL REFERENCES %I.products(id) ON DELETE CASCADE,
            relation_type       VARCHAR(20)  NOT NULL DEFAULT 'complementar'
                CHECK (relation_type IN ('complementar','substituto','combo')),
            weight              REAL         NOT NULL DEFAULT 0.5,
            notes               TEXT,
            created_at          TIMESTAMPTZ  DEFAULT NOW(),
            UNIQUE (product_id, related_product_id, relation_type),
            CHECK (product_id <> related_product_id)
        )
    $s$, p_schema, p_schema, p_schema);
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS %I ON %I.product_relations (product_id, relation_type, weight DESC)',
        'idx_prod_rel_lookup_' || p_schema, p_schema);
END;
$$ LANGUAGE plpgsql;


-- ── 2. tenant_shipping_rules (global, com tenant_id) ────────────────────────

CREATE TABLE IF NOT EXISTS public.tenant_shipping_rules (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID         NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    label           VARCHAR(120) NOT NULL,
    cep_start       VARCHAR(9)   NOT NULL,   -- formato '01000-000' (com hífen) ou '01000000'
    cep_end         VARCHAR(9)   NOT NULL,
    valor           NUMERIC(10,2) NOT NULL DEFAULT 0,
    prazo_dias      INTEGER      NOT NULL DEFAULT 2,
    gratis_acima    NUMERIC(10,2),            -- frete grátis acima desse subtotal (NULL = nunca)
    active          BOOLEAN      NOT NULL DEFAULT TRUE,
    sort_order      INTEGER      NOT NULL DEFAULT 100,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_shipping_rules_tenant
    ON public.tenant_shipping_rules (tenant_id, active, sort_order);
