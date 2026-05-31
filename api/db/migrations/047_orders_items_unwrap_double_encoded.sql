-- ═══════════════════════════════════════════════════════════════════════════
-- 047_orders_items_unwrap_double_encoded.sql
--
-- Saneia linhas em <tenant>.orders.items que foram gravadas como STRING JSON
-- em vez de array JSON real (double-encoding via `json.dumps(...)` + codec
-- jsonb do asyncpg que também serializa). Sintoma em produção:
--
--   * Cards da página /portal/pedidos todos em 0.
--   * Causa raiz: a query de métricas usa `jsonb_array_elements(items)`, que
--     estoura quando o valor JSONB é do tipo string em vez de array. O
--     try/except do endpoint engole o erro e devolve OrderMetrics zerado.
--
-- Os call sites (`agents/tools/balcao.py` e `agents/tools/inventory.py`)
-- já foram corrigidos para passar o objeto Python direto (sem json.dumps).
-- Esta migration desembrulha o que ficou no banco.
--
-- Idempotente: roda só onde jsonb_typeof = 'string' e o conteúdo da string
-- ainda parsea como array. Caso já esteja como array (linha sã), pula.
-- ═══════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    t RECORD;
BEGIN
    FOR t IN
        SELECT schema_name
        FROM public.tenants
        WHERE active = TRUE
    LOOP
        RAISE NOTICE 'Unwrapping orders.items in schema: %', t.schema_name;

        EXECUTE format($s$
            UPDATE %I.orders
               SET items = (items #>> '{}')::jsonb
             WHERE jsonb_typeof(items) = 'string'
               AND jsonb_typeof((items #>> '{}')::jsonb) = 'array'
        $s$, t.schema_name);
    END LOOP;
END$$;
