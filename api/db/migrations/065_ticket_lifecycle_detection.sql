-- ─────────────────────────────────────────────────────────────────────────────
-- Detecção de eventos de ticket externos (lifecycle event-driven).
--
-- Plataformas de multiatendimento (TalkFarma/ClickMassa/etc.) emitem webhooks
-- de "ticket aberto" e "ticket fechado" no mesmo endpoint /hooks usado para
-- mensagens, distinguindo apenas pelo tipo do evento. Quando habilitado, esse
-- recurso transforma a pausa pós-handoff em event-driven: a IA volta a
-- atender SOMENTE quando o ticket fecha na plataforma (não por timer).
--
-- Formato:
--   {
--     "enabled": true,
--     "close_match": { "path": "$.event", "equals": "ticket.closed" },
--     "open_match":  { "path": "$.event", "equals": "ticket.opened" },  -- opcional
--     "customer_phone_path": "$.contact.phone",
--     "fallback_minutes": 480        -- safety net (0 = nunca expira)
--   }
--
-- Coexiste com human_handoff_detection (migration 062): quando os dois estão
-- ligados, o human_reply continua pausando, mas com paused_until baseado em
-- fallback_minutes (ou NULL = indefinido), e o evento close_match é quem
-- libera a IA via reset_session (limpa closed_at + histórico Redis).
--
-- Aplica-se SOMENTE ao fluxo broker (/hooks); canais nativos (Z-API/Meta)
-- não recebem eventos de ticket.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS ticket_lifecycle_detection JSONB NOT NULL DEFAULT '{}'::jsonb;
