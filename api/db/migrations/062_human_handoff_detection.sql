-- ─────────────────────────────────────────────────────────────────────────────
-- Detecção de resposta HUMANA (atendente) → pausa automática da IA.
--
-- Em gateways que ecoam mensagens de SAÍDA (TalkFarma/ClickMassa/WAHA/Evolution),
-- tanto o bot quanto o atendente humano enviam pelo mesmo número e o gateway
-- devolve ambas ao webhook. Quando o ATENDENTE responde, a IA deve pausar.
-- A distinção bot×humano é feita por fingerprint efêmero em Redis (services.bot_echo):
-- o eco que casa com algo que o bot acabou de mandar é ignorado; o que não casa
-- é tratado como resposta humana e dispara conversation_state.pause().
--
-- Formato:
--   {
--     "enabled": true,
--     "outbound_match": { "path": "$.fromMe", "equals": true },  -- marca msg de saída
--     "customer_phone_path": "$.to"                               -- telefone do CLIENTE
--   }
--
-- A duração da pausa reusa a coluna já existente `handoff_pause_minutes`
-- (migration 028). Aplica-se SOMENTE ao fluxo broker (/hooks); canais nativos
-- (Z-API/Meta) não ecoam o outbound do atendente.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS human_handoff_detection JSONB NOT NULL DEFAULT '{}'::jsonb;
