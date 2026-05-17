-- ─────────────────────────────────────────────────────────────────────────────
-- Message bundling (debounce) — agrupa mensagens picadas do cliente.
--
-- Quando habilitado, o broker armazena as mensagens recebidas num buffer Redis
-- e aguarda `bundle_window_seconds` de silêncio antes de processar tudo de
-- uma vez (concatenado). Só faz sentido com reply_mode='forward'.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS bundle_enabled        BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS bundle_window_seconds INT     NOT NULL DEFAULT 10;
