-- ═══════════════════════════════════════════════════════════════════════════
-- 031_agent_traces_unwrap_steps.sql
--
-- Corrige linhas existentes em <tenant>.agent_traces.steps que foram gravadas
-- como jsonb scalar string (double-encoded) em vez de array jsonb nativo.
--
-- Causa raiz: services/agent_traces.py fazia json.dumps(steps) antes de
-- passar para asyncpg, que por sua vez tem codec jsonb configurado em
-- db/postgres.py que também faz json.dumps(). Resultado: jsonb_array_elements
-- falhava com "cannot extract elements from a scalar" e o trace ficava
-- inutilizável para queries estruturadas (precisava do truque `#>>'{}'`).
--
-- O service já foi corrigido no código (passa lista crua). Esta migration
-- normaliza o histórico para que queries SQL antigas e novas funcionem
-- igualmente em todas as linhas.
--
-- Estratégia: para cada schema tenant_%, fazer UPDATE em linhas onde
-- jsonb_typeof(steps) = 'string' (scalar), aplicando steps::text::jsonb
-- via #>>'{}' (extrai o texto interno) e re-casta. Linhas que já estão
-- como array jsonb são deixadas em paz (idempotente).
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    t RECORD;
    fixed_count INTEGER;
BEGIN
    FOR t IN
        SELECT schema_name
        FROM public.tenants
        WHERE active = TRUE
    LOOP
        EXECUTE format($s$
            UPDATE %I.agent_traces
               SET steps = ((steps #>> '{}')::jsonb)
             WHERE jsonb_typeof(steps) = 'string'
        $s$, t.schema_name);

        GET DIAGNOSTICS fixed_count = ROW_COUNT;
        RAISE NOTICE 'Tenant %: unwrapped % rows', t.schema_name, fixed_count;
    END LOOP;
END$$;
