-- ─────────────────────────────────────────────────────────────────────────────
-- recovery_batches — disparo manual de mensagens de recuperação em lote.
--
-- Cada execução do botão "Disparar agora" cria um row aqui. Um Celery task
-- consome a lista de session_keys, respeita rate-limit, checa
-- `cancel_requested` antes de cada envio e atualiza os contadores. O frontend
-- faz polling pra mostrar progresso em tempo real e oferece "Desfazer" depois
-- do término — undo NÃO desentrega mensagens (impossível), apenas reverte o
-- marcador `sent_recovery_at` / `recovery_attempts` no cart pra que o
-- carrinho volte a ser elegível pelo job automático.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.recovery_batches (
    id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id          UUID         NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    schema_name        VARCHAR(63)  NOT NULL,
    actor_email        VARCHAR(255),
    status             VARCHAR(20)  NOT NULL DEFAULT 'queued'
                          CHECK (status IN ('queued','running','completed','cancelled','undone','failed')),
    total              INTEGER      NOT NULL DEFAULT 0,
    sent               INTEGER      NOT NULL DEFAULT 0,
    failed             INTEGER      NOT NULL DEFAULT 0,
    skipped            INTEGER      NOT NULL DEFAULT 0,
    -- session_keys de entrada (o que o operador pediu pra notificar):
    session_keys       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    -- session_keys que efetivamente receberam mensagem (alvo do undo):
    sent_session_keys  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    cancel_requested   BOOLEAN      NOT NULL DEFAULT FALSE,
    error              TEXT,
    created_at         TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    started_at         TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_recovery_batches_tenant
    ON public.recovery_batches (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_recovery_batches_active
    ON public.recovery_batches (tenant_id) WHERE status IN ('queued','running');
