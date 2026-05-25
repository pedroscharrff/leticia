-- ─────────────────────────────────────────────────────────────────────────────
-- Estado por conversa (tenant × telefone): permite pausar IA, encerrar
-- atendimento e configurar TTL por tenant.
--
-- Cenários:
--   1) Atendente humano assume → pausa IA por X horas (auto via handoff)
--   2) Operador clica "Pausar IA" no portal → ai_paused = TRUE
--   3) Operador encerra atendimento → closed_at preenchido
--   4) Tenant pode configurar quanto tempo dura uma sessão antes de expirar
--      (default 30 min, antes era hardcoded em config.py)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.conversation_state (
    tenant_id      UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    phone          TEXT        NOT NULL,
    ai_paused      BOOLEAN     NOT NULL DEFAULT FALSE,
    paused_until   TIMESTAMPTZ,
    paused_by      TEXT,
    paused_reason  TEXT,
    closed_at      TIMESTAMPTZ,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (tenant_id, phone)
);

CREATE INDEX IF NOT EXISTS idx_conv_state_paused
    ON public.conversation_state(tenant_id, paused_until)
 WHERE ai_paused = TRUE;

-- TTL de sessão configurável por tenant (default 30 min)
ALTER TABLE public.tenants
    ADD COLUMN IF NOT EXISTS session_ttl_minutes INTEGER NOT NULL DEFAULT 30;

-- Tempo de pausa automática após handoff (default 4h = 240 min) por canal
ALTER TABLE public.tenant_channels
    ADD COLUMN IF NOT EXISTS handoff_pause_minutes INTEGER NOT NULL DEFAULT 240;

-- Idem para integrações webhook (broker)
ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS handoff_pause_minutes INTEGER NOT NULL DEFAULT 240;
