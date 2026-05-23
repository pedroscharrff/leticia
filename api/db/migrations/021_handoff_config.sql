-- ─────────────────────────────────────────────────────────────────────────────
-- Handoff config: transferência da conversa para o balcão / atendente humano.
--
-- Quando o agente sinaliza escalate=True OU o cliente envia uma palavra-chave
-- de transferência (ex: "atendente", "humano"), fazemos um POST para a API
-- externa do PDV/CRM (ex: ClickMassa / TalkFarma) que cria o ticket numa fila
-- de atendimento humano.
--
-- Formato:
--   {
--     "enabled": true,
--     "provider": "clickmassa",
--     "base_url": "https://chatapi.talkfarma.pro/v1/api/external/<uuid>",
--     "token": "<jwt>",
--     "queue_id": 4,
--     "transfer_message": "Vou te transferir para um atendente humano agora.",
--     "trigger_keywords": ["atendente", "humano", "balcão", "balcao"]
--   }
--
-- A URL final montada vira:
--   {base_url}/?token={token}
-- e o POST body:
--   { number, body, forceTicketToDepartment: true, queueId }
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS handoff_config JSONB NOT NULL DEFAULT '{}'::jsonb;
