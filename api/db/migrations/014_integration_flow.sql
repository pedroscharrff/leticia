-- ─────────────────────────────────────────────────────────────────────────────
-- Simplified per-integration flow.
--
-- For non-technical users, every integration has ONE flow:
--   1. Inbound field_map: extracts canonical input (phone, message, etc.)
--      from the incoming webhook payload.
--   2. Agent processes the message.
--   3. Reply: either returned in the same HTTP response, or POSTed to a
--      separate URL (forward mode).
--
-- The existing integration_mappings / broker_outbound_targets tables are
-- kept for power users (multiple events / advanced fan-out) but the UI
-- now uses these fields directly for the default case.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS inbound_field_map   JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS reply_mode          TEXT  NOT NULL DEFAULT 'response',
    ADD COLUMN IF NOT EXISTS reply_url           TEXT,
    ADD COLUMN IF NOT EXISTS reply_method        TEXT  DEFAULT 'POST',
    ADD COLUMN IF NOT EXISTS reply_headers       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS reply_body_template JSONB NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS reply_status_code   INT   DEFAULT 200;

-- reply_mode: 'response' (sync, return in same HTTP) | 'forward' (async, POST elsewhere)
