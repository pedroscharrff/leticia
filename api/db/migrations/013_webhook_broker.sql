-- ─────────────────────────────────────────────────────────────────────────────
-- Webhook broker — universal inbound/outbound integration layer.
--
-- An "integration" represents one external source system per tenant
-- (ex: Shopify, Tray, ERP-X). Each integration owns:
--   - inbound mappings  → translate source payload → canonical event
--   - outbound targets  → translate canonical event → POST to destination
--
-- Raw payloads are always persisted in broker_raw_events so failed/changed
-- mappings can be replayed without losing data.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.tenant_integrations (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    slug            TEXT NOT NULL,                  -- url-safe, ex "shopify"
    name            TEXT NOT NULL,                  -- display name
    direction       TEXT NOT NULL DEFAULT 'inbound', -- inbound | outbound | both
    hmac_secret     TEXT,                           -- optional shared secret
    hmac_header     TEXT,                           -- ex "X-Shopify-Hmac-Sha256"
    hmac_algorithm  TEXT DEFAULT 'sha256',
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, slug)
);

CREATE INDEX IF NOT EXISTS idx_tenant_integrations_tenant
    ON public.tenant_integrations(tenant_id);


CREATE TABLE IF NOT EXISTS public.integration_mappings (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id  UUID NOT NULL REFERENCES public.tenant_integrations(id) ON DELETE CASCADE,
    canonical_event TEXT NOT NULL,            -- ex "order.created", "message.received"
    -- Match rule: incoming payload only uses this mapping when all
    -- match_rules entries evaluate true. Example:
    --   {"$.event_type": "orders/create"}
    match_rules     JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Field map: canonical_field → source_expression
    -- source_expression supports:
    --   "$.path.to.value"      JSONPath read
    --   "$.items[0].name"      array index
    --   "$.items[*].sku"       array projection (returns list)
    --   "={{$.first}} {{$.last}}"  template (= prefix)
    --   "=literal string"      literal
    field_map       JSONB NOT NULL DEFAULT '{}'::jsonb,
    direction       TEXT NOT NULL DEFAULT 'inbound', -- inbound | outbound
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    version         INT NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_integration_mappings_integration
    ON public.integration_mappings(integration_id, direction, enabled);


CREATE TABLE IF NOT EXISTS public.broker_outbound_targets (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    integration_id  UUID NOT NULL REFERENCES public.tenant_integrations(id) ON DELETE CASCADE,
    canonical_event TEXT NOT NULL,            -- the event type that triggers this target
    url             TEXT NOT NULL,
    method          TEXT NOT NULL DEFAULT 'POST',
    headers         JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Same field_map syntax as integration_mappings; resolves against canonical event
    field_map       JSONB NOT NULL DEFAULT '{}'::jsonb,
    enabled         BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbound_targets_event
    ON public.broker_outbound_targets(canonical_event, enabled);


CREATE TABLE IF NOT EXISTS public.broker_raw_events (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id       UUID NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    integration_id  UUID REFERENCES public.tenant_integrations(id) ON DELETE SET NULL,
    integration_slug TEXT NOT NULL,
    direction       TEXT NOT NULL DEFAULT 'inbound',
    payload         JSONB NOT NULL,
    headers         JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key TEXT,                     -- hash(payload) or external id
    status          TEXT NOT NULL DEFAULT 'pending',  -- pending|processed|failed|dlq|skipped
    canonical_event TEXT,
    canonical_payload JSONB,
    matched_mapping_id UUID REFERENCES public.integration_mappings(id) ON DELETE SET NULL,
    error           TEXT,
    attempts        INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_raw_events_tenant_created
    ON public.broker_raw_events(tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_raw_events_status
    ON public.broker_raw_events(status, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS uq_raw_events_idem
    ON public.broker_raw_events(tenant_id, integration_slug, idempotency_key)
    WHERE idempotency_key IS NOT NULL;
