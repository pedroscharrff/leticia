-- ─────────────────────────────────────────────────────────────────────────────
-- Alarga colunas `phone` em todos os schemas de tenant.
--
-- VARCHAR(20) estoura quando gateways tipo Z-API / WAHA mandam o telefone com
-- sufixo (ex: "5511999999999:21@s.whatsapp.net" = 29 chars). O broker já
-- sanitiza pra dígitos antes de gravar, mas alargar evita problemas com
-- outros canais e formatos internacionais.
-- ─────────────────────────────────────────────────────────────────────────────

DO $$
DECLARE
    s TEXT;
BEGIN
    FOR s IN
        SELECT schema_name FROM information_schema.schemata
        WHERE schema_name LIKE 'tenant\_%' ESCAPE '\'
    LOOP
        EXECUTE format('ALTER TABLE %I.customers ALTER COLUMN phone TYPE VARCHAR(60)', s);
        EXECUTE format('ALTER TABLE %I.sessions  ALTER COLUMN phone TYPE VARCHAR(60)', s);
    END LOOP;
END $$;

-- Atualiza também o template do schema_factory pra novos tenants nascerem com VARCHAR(60).
-- (As migrations 003/007/009 criaram a função create_tenant_schema; recriamos com a coluna maior.)
-- Como não temos certeza da última versão da função sem reimplementá-la inteira,
-- deixamos isto como TODO — novos tenants ainda nascem com VARCHAR(20) mas a
-- migration acima alarga em rodadas subsequentes. Para fixar definitivamente,
-- atualize a função create_tenant_schema na próxima migration de schema.
