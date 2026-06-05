-- ─────────────────────────────────────────────────────────────────────────────
-- Tabela de agregação diária de consumo LLM por tenant × modelo.
--
-- Populada por job Celery `aggregate_llm_usage_daily` (workers/jobs/aggregate_usage.py)
-- que roda às 00:05 todo dia, sumarizando `conversation_logs` do dia anterior
-- de cada tenant ativo. Idempotente: ON CONFLICT atualiza row existente
-- (permite re-rodar manualmente sem duplicar).
--
-- Por que não reusar `public.usage_records` (mig 003): aquela é mensal,
-- por métrica genérica ('msgs'/'tokens_in'/'tokens_out'/'api_calls'), e está
-- amarrada à lógica de billing. Aqui queremos granularidade dia × modelo
-- pra responder "quanto custou Sonnet vs Haiku ontem por tenant".
--
-- Cost_usd persistido (não calculado on-the-fly): pricing pode mudar,
-- queremos preservar o que foi cobrado no momento da agregação.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.llm_usage_daily (
    id           BIGSERIAL PRIMARY KEY,
    tenant_id    UUID         NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    day          DATE         NOT NULL,
    llm_model    VARCHAR(100) NOT NULL DEFAULT 'unknown',
    tokens_in    BIGINT       NOT NULL DEFAULT 0,
    tokens_out   BIGINT       NOT NULL DEFAULT 0,
    msg_count    INTEGER      NOT NULL DEFAULT 0,
    cost_usd     NUMERIC(12, 6) NOT NULL DEFAULT 0,
    created_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, day, llm_model)
);

CREATE INDEX IF NOT EXISTS idx_llm_usage_daily_tenant_day
    ON public.llm_usage_daily (tenant_id, day DESC);

CREATE INDEX IF NOT EXISTS idx_llm_usage_daily_day
    ON public.llm_usage_daily (day DESC);
