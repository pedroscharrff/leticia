-- ─────────────────────────────────────────────────────────────────────────────
-- Encerramento de sessão por palavra-chave + reset automático pós-handoff
--
-- Motivação: hoje só dá pra encerrar uma conversa manualmente pelo portal.
-- Queremos:
--   1) Permitir ao tenant definir palavras-chave (ex: "encerrar", "tchau",
--      "fim") que, ao serem enviadas pelo cliente, fecham a sessão e zeram
--      o histórico — o próximo contato começa do zero.
--   2) Após handoff p/ atendente humano: marcar a sessão como encerrada de
--      modo que, quando o cliente voltar a falar (após a janela de pausa),
--      um NOVO atendimento seja aberto em vez de continuar o anterior.
--
-- Formato de session_config (JSONB):
--   {
--     "close_keywords": ["encerrar", "tchau", "fim"],
--     "close_message":  "Atendimento encerrado. Quando precisar, é só chamar!",
--     "reset_after_handoff": true
--   }
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS session_config JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE public.tenant_channels
    ADD COLUMN IF NOT EXISTS session_config JSONB NOT NULL DEFAULT '{}'::jsonb;
