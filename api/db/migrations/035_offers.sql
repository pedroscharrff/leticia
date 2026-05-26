-- ─────────────────────────────────────────────────────────────────────────────
-- Offers: ofertas/promoções gerenciadas pelo tenant, exibidas como última
-- tentativa de retenção comercial ANTES de uma transferência para humano.
--
-- Consumida pelo hook em api/workers/celery_app.py (anexa lista de ofertas
-- vigentes à mensagem de handoff quando a capability `sales.pre_handoff_offers`
-- está ativa para o tenant).
--
-- Mora em `public` (não em schema de tenant) — mesmo padrão de tenant_secrets
-- e tenant_capabilities. Filtro por tenant_id no SELECT.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.offers (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    title        TEXT        NOT NULL,
    description  TEXT        NOT NULL DEFAULT '',
    valid_from   TIMESTAMPTZ,
    valid_until  TIMESTAMPTZ,
    priority     INTEGER     NOT NULL DEFAULT 0,
    active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_offers_tenant_active
    ON public.offers (tenant_id, priority DESC, created_at DESC)
    WHERE active = TRUE;
