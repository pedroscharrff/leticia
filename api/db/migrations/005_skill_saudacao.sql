-- ── Add 'saudacao' (reception/greeting) skill to the global catalog ──────────
-- Available to all plans and ensures the orchestrator has a safe default
-- destination for greetings and ambiguous first-contact messages.

INSERT INTO public.skill_catalog (skill_name, display_name, description, category, plan_min) VALUES
    ('saudacao', 'Recepção', 'Acolhe o cliente, responde saudações e direciona o atendimento', 'recepcao', 'basic')
ON CONFLICT (skill_name) DO NOTHING;

-- Activate 'saudacao' for every existing tenant schema
DO $$
DECLARE
    schema_rec RECORD;
BEGIN
    FOR schema_rec IN
        SELECT schema_name FROM public.tenants WHERE schema_name IS NOT NULL
    LOOP
        BEGIN
            EXECUTE format(
                'INSERT INTO %I.skills_config (skill_name, ativo) VALUES ($1, TRUE)
                 ON CONFLICT (skill_name) DO UPDATE SET ativo = TRUE',
                schema_rec.schema_name
            ) USING 'saudacao';
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipped %: %', schema_rec.schema_name, SQLERRM;
        END;
    END LOOP;
END $$;
