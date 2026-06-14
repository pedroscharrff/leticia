-- ═══════════════════════════════════════════════════════════════════════════
-- 068_medicamentos_referencia_consultas.sql
--
-- Log detalhado de cada consulta do agente à base curada de medicamentos de
-- REFERÊNCIA (tool `consultar_medicamento_referencia`). Alimenta o painel de
-- monitoramento "Consultas" no superadmin `/admin/medicamentos`: permite ver,
-- turno a turno, o que o agente buscou, qual medicamento casou e quais seções
-- ATIVAS foram efetivamente devolvidas (consumidas).
--
-- Por que uma tabela e não só o Counter Prometheus: o Counter
-- (`saas_reference_clinical_used_total{secao}`) é agregado e não guarda o termo
-- buscado, o tenant, nem o caso "não encontrou". Para auditar curadoria
-- (quais termos não casam, quais seções nunca são usadas) precisamos do detalhe.
--
-- Pública/global (não per-tenant), igual a `medicamentos_referencia` — a base é
-- compartilhada. `tenant_id` é só rótulo de origem (qual farmácia consultou),
-- sem FK: é tabela de telemetria e um INSERT de log NUNCA pode falhar/bloquear
-- o turno do agente por causa de integridade referencial.
-- ═══════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.medicamentos_referencia_consultas (
    id              BIGSERIAL    PRIMARY KEY,
    tenant_id       UUID,                                  -- farmácia de origem (rótulo, sem FK)
    session_id      TEXT,                                  -- liga ao histórico da conversa
    skill           TEXT,                                  -- farmaceutico | principio_ativo | genericos
    termo           TEXT         NOT NULL,                 -- termo buscado pelo agente
    encontrado      BOOLEAN      NOT NULL DEFAULT FALSE,   -- houve match na base?
    num_resultados  INTEGER      NOT NULL DEFAULT 0,       -- nº de medicamentos casados
    -- medicamentos casados: [{"principio_ativo": ..., "nome_referencia": ...}]
    medicamentos    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    -- slugs das seções ATIVAS devolvidas (consumidas): ["indicacoes","posologia"]
    secoes          JSONB        NOT NULL DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

-- Caminho quente do painel: ordenação cronológica decrescente.
CREATE INDEX IF NOT EXISTS idx_med_ref_consultas_created
    ON public.medicamentos_referencia_consultas (created_at DESC);

-- Filtro por farmácia.
CREATE INDEX IF NOT EXISTS idx_med_ref_consultas_tenant
    ON public.medicamentos_referencia_consultas (tenant_id, created_at DESC);

-- Diagnóstico de curadoria: "quais termos NÃO casaram".
CREATE INDEX IF NOT EXISTS idx_med_ref_consultas_naoencontrado
    ON public.medicamentos_referencia_consultas (created_at DESC)
 WHERE encontrado = FALSE;
