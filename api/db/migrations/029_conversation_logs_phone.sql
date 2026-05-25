-- ─────────────────────────────────────────────────────────────────────────────
-- Adiciona coluna `phone` em <schema>.conversation_logs para agrupamento
-- consistente por número (estilo WhatsApp), independente do `session_key`.
--
-- Motivação: o session_key pode ser:
--   • "tenant_uuid:5511999..."  (webhook nativo padrão)
--   • "5511999..."              (broker simples)
--   • "session_externo_xyz"     (broker recebendo session_id de outro sistema)
-- Não podemos extrair o telefone de forma confiável só com string-split.
--
-- Backfill: para registros antigos, tentamos:
--   1) Pegar do agent_traces pelo mesmo session_key (campo phone existe lá)
--   2) Se falhar, extrair última parte numérica do session_key
-- ─────────────────────────────────────────────────────────────────────────────

DO $migr$
DECLARE
    t RECORD;
BEGIN
    FOR t IN SELECT schema_name FROM public.tenants
              WHERE schema_name IS NOT NULL LOOP
        BEGIN
            -- 1) Adicionar coluna (idempotente)
            EXECUTE format($s$
                ALTER TABLE %I.conversation_logs
                    ADD COLUMN IF NOT EXISTS phone TEXT
            $s$, t.schema_name);

            -- 2) Índice para o agrupamento da inbox
            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON %I.conversation_logs (phone, created_at DESC)',
                'idx_conv_logs_phone_' || t.schema_name,
                t.schema_name
            );

            -- 3) Backfill: tenta via agent_traces primeiro
            EXECUTE format($s$
                UPDATE %I.conversation_logs cl
                   SET phone = at.phone
                  FROM %I.agent_traces at
                 WHERE cl.session_key = at.session_key
                   AND cl.phone IS NULL
                   AND at.phone IS NOT NULL
            $s$, t.schema_name, t.schema_name);

            -- 4) Backfill heurístico: se session_key contiver ':', pega a última parte.
            --    Se contiver SÓ dígitos, é o phone direto.
            EXECUTE format($s$
                UPDATE %I.conversation_logs
                   SET phone = CASE
                       WHEN session_key ~ '^[0-9]+$' THEN session_key
                       WHEN session_key ~ ':' THEN
                            regexp_replace(
                                split_part(session_key, ':',
                                    array_length(string_to_array(session_key, ':'), 1)),
                                '[^0-9]', '', 'g'
                            )
                       ELSE NULL
                   END
                 WHERE phone IS NULL
            $s$, t.schema_name);

        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Falha em %: %', t.schema_name, SQLERRM;
        END;
    END LOOP;
END
$migr$;

-- Também adiciona ao template create_tenant_schema (para tenants novos)
-- Procura pela função e atualiza se existir.
-- (Nada a fazer aqui se a função do schema já tem a coluna — fica como
--  documentação para o próximo tenant criado.)
