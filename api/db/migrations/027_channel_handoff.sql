-- ─────────────────────────────────────────────────────────────────────────────
-- Handoff por canal — antes existia apenas em tenant_integrations (webhook).
--
-- Motivação: cada canal nativo (WhatsApp Cloud, Z-API, Telegram, …) precisa
-- ter sua própria fila / config de transferência ao atendente humano. Um
-- mesmo tenant pode ter 3 lojas físicas, cada uma com WhatsApp próprio,
-- direcionando para queues diferentes.
--
-- Quando o broker (celery worker) decide transferir:
--   1) Se a mensagem veio de uma integração webhook → usa handoff_config dela.
--   2) Se veio de um canal nativo → usa o handoff_config do canal.
--   3) Se nenhum dos dois tiver handoff_config preenchido → não transfere.
--
-- Formato igual ao de tenant_integrations.handoff_config:
--   { enabled, provider, base_url, token, queue_id, transfer_message,
--     trigger_keywords }
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_channels
    ADD COLUMN IF NOT EXISTS handoff_config JSONB NOT NULL DEFAULT '{}'::jsonb;
