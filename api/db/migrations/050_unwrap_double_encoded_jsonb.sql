-- ─────────────────────────────────────────────────────────────────────────────
-- Desfaz o double-encoding de jsonb em colunas críticas.
--
-- Causa: `agents/nodes/context.py:265` chamava `json.dumps(cart.items)` antes
-- de passar pra asyncpg, mas o codec em `db/postgres.py:_json_encoder` JÁ faz
-- json.dumps. Resultado: o que deveria ser array `[{...}]` virou string JSON
-- `"[{...}]"` no jsonb. `jsonb_typeof` retorna 'string', `jsonb_array_length`
-- estoura, e queries que filtram por array_length>0 escondem tudo.
--
-- Esta migration desempacota: se `items` (ou `continuous_meds`) está como
-- string E o conteúdo unwrapped parseia como JSON válido, substitui pelo
-- valor real. Caso contrário deixa quieto. Idempotente: rodar de novo é
-- no-op porque o WHERE filtra `jsonb_typeof = 'string'`.
--
-- Aplica para TODOS os tenants existentes E vira parte do template via
-- `create_tenant_schema_full` (não é necessário aqui — é fix de dados, não
-- de schema). Toda nova migração de dado idempotente cabe nesse padrão.
-- ─────────────────────────────────────────────────────────────────────────────

-- Helper resiliente: conta itens de um jsonb mesmo se algum row ainda estiver
-- gravado como string (pode acontecer se outro call site ainda não migrado
-- continuar double-encoding, ou em row novo entre o deploy do backend e o
-- run desta migration). Substituir `jsonb_array_length(x)` por
-- `public.safe_jsonb_array_length(x)` em queries que iteram dados do tenant.
CREATE OR REPLACE FUNCTION public.safe_jsonb_array_length(v jsonb)
RETURNS INTEGER AS $$
DECLARE
    inner_t TEXT;
BEGIN
    IF v IS NULL THEN RETURN 0; END IF;
    IF jsonb_typeof(v) = 'array' THEN
        RETURN jsonb_array_length(v);
    END IF;
    IF jsonb_typeof(v) = 'string' THEN
        BEGIN
            inner_t := v #>> '{}';
            IF inner_t ~ '^\s*\[' THEN
                RETURN jsonb_array_length(inner_t::jsonb);
            END IF;
            RETURN 0;
        EXCEPTION WHEN OTHERS THEN
            RETURN 0;
        END;
    END IF;
    RETURN 0;
END;
$$ LANGUAGE plpgsql IMMUTABLE;


DO $migr$
DECLARE
    t RECORD;
    cart_fixed INT;
    cm_fixed   INT;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            -- cart.items: string → array
            EXECUTE format($s$
                WITH upd AS (
                    UPDATE %I.cart
                       SET items = (items #>> '{}')::jsonb
                     WHERE jsonb_typeof(items) = 'string'
                       AND (items #>> '{}') ~ '^\s*\['
                    RETURNING 1
                )
                SELECT COUNT(*) FROM upd
            $s$, t.schema_name) INTO cart_fixed;

            -- customers.continuous_meds: string → array
            EXECUTE format($s$
                WITH upd AS (
                    UPDATE %I.customers
                       SET continuous_meds = (continuous_meds #>> '{}')::jsonb
                     WHERE jsonb_typeof(continuous_meds) = 'string'
                       AND (continuous_meds #>> '{}') ~ '^\s*\['
                    RETURNING 1
                )
                SELECT COUNT(*) FROM upd
            $s$, t.schema_name) INTO cm_fixed;

            IF cart_fixed > 0 OR cm_fixed > 0 THEN
                RAISE NOTICE 'Schema % unwrapped: % carts, % customers',
                    t.schema_name, cart_fixed, cm_fixed;
            END IF;
        EXCEPTION WHEN OTHERS THEN
            -- Loga o SQLERRM real (não usa RAISE NOTICE mudo — ver [[tenant-schema-drift]])
            RAISE WARNING 'Schema % falhou no unwrap: % (SQLSTATE %)',
                t.schema_name, SQLERRM, SQLSTATE;
        END;
    END LOOP;
END $migr$;
