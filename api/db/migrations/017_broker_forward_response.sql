-- ─────────────────────────────────────────────────────────────────────────────
-- Captura a resposta da API externa no modo `forward`.
--
-- Quando fazemos POST pra reply_url (Z-API, WAHA, etc.), guardamos:
--   - forward_status_code: HTTP status retornado
--   - forward_response:    body da resposta (JSON ou texto)
--   - forward_url:         a URL que foi chamada (útil pra histórico)
--
-- Sem isso, erros do gateway (token errado, formato inválido) só aparecem no log.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.broker_raw_events
    ADD COLUMN IF NOT EXISTS forward_url         TEXT,
    ADD COLUMN IF NOT EXISTS forward_status_code INT,
    ADD COLUMN IF NOT EXISTS forward_response    JSONB;
