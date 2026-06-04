-- ─────────────────────────────────────────────────────────────────────────────
-- Desfaz double-encoding de jsonb em public.tenant_integrations.
--
-- Causa: `routers/broker.py::create_integration` chamava
-- `json.dumps(body.config_json or {})` antes de passar pro param `$9::jsonb`,
-- mas o codec em `db/postgres.py::_json_encoder` JÁ faz json.dumps. Resultado:
-- o objeto `{}` virou a STRING JSON `"{}"` no jsonb. Na leitura o decoder
-- devolve a str Python `'{}'`, e o Pydantic de `IntegrationOut.config_json`
-- (espera dict) estoura com `dict_type` → 500 em GET /portal/integrations.
--
-- Mesma classe de bug da migration 050 (que só cobriu cart.items e
-- customers.continuous_meds nos schemas de tenant). O fix do write está em
-- broker.py (passa o dict cru, sem json.dumps). Esta migration repara os
-- dados já gravados.
--
-- Idempotente: o WHERE filtra `jsonb_typeof = 'string'`, então rodar de novo
-- após o reparo é no-op. Cobre as 4 colunas jsonb-objeto da tabela por
-- segurança (config_json é a confirmada; as outras são write-raw via save_flow
-- e provavelmente íntegras, mas o filtro garante que só toca o que está torto).
-- ─────────────────────────────────────────────────────────────────────────────

DO $migr$
DECLARE
    col   TEXT;
    fixed INT;
BEGIN
    FOREACH col IN ARRAY ARRAY[
        'config_json', 'handoff_config', 'session_config', 'inbound_field_map'
    ] LOOP
        BEGIN
            EXECUTE format($s$
                WITH upd AS (
                    UPDATE public.tenant_integrations
                       SET %I = (%I #>> '{}')::jsonb
                     WHERE jsonb_typeof(%I) = 'string'
                       AND (%I #>> '{}') ~ '^\s*[\{\[]'
                    RETURNING 1
                )
                SELECT COUNT(*) FROM upd
            $s$, col, col, col, col) INTO fixed;

            IF fixed > 0 THEN
                RAISE NOTICE 'tenant_integrations.% unwrapped: % rows', col, fixed;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'unwrap de tenant_integrations.% falhou: % (SQLSTATE %)',
                col, SQLERRM, SQLSTATE;
        END;
    END LOOP;
END $migr$;
