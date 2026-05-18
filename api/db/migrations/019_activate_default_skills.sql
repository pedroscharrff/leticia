-- ── Activate baseline skills (saudacao + farmaceutico) for tenants seeded ─────
-- Bug fix: onboarding.py inseriu skills sem `ativo`, default FALSE da tabela.
-- Resultado: orquestrador via `available_skills=[]` e caía no fallback hardcoded
-- "farmaceutico", impedindo saudacao e demais skills de serem roteados.
-- Aqui forçamos saudacao + farmaceutico ATIVOS em todos os tenants para
-- recuperar o mínimo viável de atendimento.

DO $$
DECLARE
    schema_rec RECORD;
BEGIN
    FOR schema_rec IN
        SELECT schema_name FROM public.tenants WHERE schema_name IS NOT NULL
    LOOP
        BEGIN
            EXECUTE format(
                'INSERT INTO %I.skills_config (skill_name, ativo) VALUES
                    (''saudacao'',     TRUE),
                    (''farmaceutico'', TRUE)
                 ON CONFLICT (skill_name) DO UPDATE SET ativo = TRUE',
                schema_rec.schema_name
            );
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped %: %', schema_rec.schema_name, SQLERRM;
        END;
    END LOOP;
END $$;
