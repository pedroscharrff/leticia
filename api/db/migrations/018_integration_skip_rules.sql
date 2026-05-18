-- ─────────────────────────────────────────────────────────────────────────────
-- Skip rules: lista de filtros pra ignorar mensagens antes de processar.
--
-- Evita o loop bot ↔ gateway (ex: Z-API manda webhook da mensagem que NÓS
-- enviamos, agente vê como mensagem do cliente e responde infinitamente).
--
-- Formato: lista de objetos { path, equals, comment }
--   [
--     {"path": "$.fromMe",     "equals": true,            "comment": "Ignorar mensagens enviadas pelo próprio bot (Z-API)"},
--     {"path": "$.event_type", "equals": "message.ack",   "comment": "Ignorar acks (WAHA)"}
--   ]
-- Se qualquer regra bater → evento é marcado como 'skipped' e não processa.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS skip_rules JSONB NOT NULL DEFAULT '[]'::jsonb;
