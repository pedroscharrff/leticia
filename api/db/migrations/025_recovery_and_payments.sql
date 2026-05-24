-- ─────────────────────────────────────────────────────────────────────────────
-- Recuperação proativa + log de pagamentos.
--
-- 1) {schema}.cart.sent_recovery_at — quando a última mensagem de recuperação
--    foi enviada para este carrinho (NULL = nunca). Usado pelo job
--    `recover_abandoned_carts` para não martelar o mesmo cliente.
--
-- 2) {schema}.cart.recovery_attempts — contador de tentativas (gated por
--    config `max_attempts` da capability sales.abandoned_cart).
--
-- 3) public.payments_log — registro de todas as cobranças PIX criadas e seu
--    status. Atualizado pelo webhook do Asaas (capability payments.pix_asaas).
-- ─────────────────────────────────────────────────────────────────────────────

DO $migr$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            EXECUTE format($s$
                ALTER TABLE %I.cart
                    ADD COLUMN IF NOT EXISTS sent_recovery_at  TIMESTAMPTZ,
                    ADD COLUMN IF NOT EXISTS recovery_attempts INTEGER NOT NULL DEFAULT 0
            $s$, t.schema_name);

            -- Índice para o job buscar carrinhos elegíveis rapidamente
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON %I.cart (updated_at) '
                'WHERE recovery_attempts < 3',
                'idx_cart_recovery_' || t.schema_name, t.schema_name);
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped cart recovery cols %: %', t.schema_name, SQLERRM;
        END;
    END LOOP;
END $migr$;


-- Helper p/ novos tenants
CREATE OR REPLACE FUNCTION public.create_tenant_schema_recovery_ext(p_schema TEXT)
RETURNS VOID AS $$
BEGIN
    EXECUTE format($s$
        ALTER TABLE %I.cart
            ADD COLUMN IF NOT EXISTS sent_recovery_at  TIMESTAMPTZ,
            ADD COLUMN IF NOT EXISTS recovery_attempts INTEGER NOT NULL DEFAULT 0
    $s$, p_schema);
    EXECUTE format(
        'CREATE INDEX IF NOT EXISTS %I ON %I.cart (updated_at) '
        'WHERE recovery_attempts < 3',
        'idx_cart_recovery_' || p_schema, p_schema);
END;
$$ LANGUAGE plpgsql;


-- ── Log global de pagamentos PIX ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.payments_log (
    id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID         NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    schema_name     VARCHAR(63)  NOT NULL,
    order_id        UUID,                            -- aponta para {schema}.orders.id
    session_key     VARCHAR(100),
    phone           VARCHAR(20),
    provider        VARCHAR(20)  NOT NULL DEFAULT 'asaas',
    external_id     VARCHAR(200),                    -- id da cobrança no Asaas
    amount          NUMERIC(10,2) NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending'
                       CHECK (status IN ('pending','paid','expired','cancelled','refunded')),
    qr_code         TEXT,                            -- copia-cola
    qr_image_url    TEXT,                            -- URL da imagem do QR
    payment_url     TEXT,                            -- página hospedada do Asaas
    expires_at      TIMESTAMPTZ,
    paid_at         TIMESTAMPTZ,
    raw_payload     JSONB,
    created_at      TIMESTAMPTZ  DEFAULT NOW(),
    updated_at      TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payments_log_tenant     ON public.payments_log (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payments_log_external   ON public.payments_log (external_id);
CREATE INDEX IF NOT EXISTS idx_payments_log_order      ON public.payments_log (order_id);
CREATE INDEX IF NOT EXISTS idx_payments_log_pending    ON public.payments_log (status) WHERE status = 'pending';
