-- ─────────────────────────────────────────────────────────────────────────────
-- Corrige o config_schema de `sales.pharmacist_validation`.
--
-- A migration 057 tentou:
--     jsonb_set(COALESCE(config_schema, '{...}'), '{properties,not_found_message}', ...)
-- mas `config_schema` nasce '{}'::jsonb (NOT NULL DEFAULT — mig 022), então o
-- COALESCE nunca cai no default e o `jsonb_set` vira NO-OP: ele só cria o
-- elemento FINAL do path se o pai (`properties`) já existir. Resultado: o campo
-- editável `not_found_message` não aparecia no modal de Recursos (config_schema
-- continuava {}).
--
-- Aqui montamos o objeto inteiro com jsonb_build_object — não depende de
-- caminho aninhado pré-existente. Idempotente (seta o mesmo objeto).
--
-- ⚠️ Mesmo bug existe na mig 051 (sales.abandoned_cart) — só não apareceu
-- porque aquele campo é editado por página dedicada, não pelo modal de Recursos.
-- ─────────────────────────────────────────────────────────────────────────────

UPDATE public.capability_catalog
   SET config_schema = jsonb_build_object(
         'type', 'object',
         'properties', jsonb_build_object(
           'not_found_message', jsonb_build_object(
             'type',        'string',
             'title',       'Mensagem quando o remédio não está no bulário',
             'description', 'Frase que o agente envia ao cliente quando o medicamento citado não é encontrado na base da ANVISA, para coletar a dosagem/apresentação desejada.',
             'format',      'textarea',
             'default',     'Não localizei esse medicamento na minha base. Qual a dosagem e a apresentação que você gostaria? Assim já deixo anotado para o balcão.'
           )
         )
       )
 WHERE key = 'sales.pharmacist_validation';

-- Garante também o default_config (a 057 setou via ||, mas reasseguramos aqui
-- para o caso de a 057 não ter rodado o primeiro UPDATE por algum motivo).
UPDATE public.capability_catalog
   SET default_config = COALESCE(default_config, '{}'::jsonb)
                        || jsonb_build_object(
                             'not_found_message',
                             'Não localizei esse medicamento na minha base. Qual a ' ||
                             'dosagem e a apresentação que você gostaria? Assim já ' ||
                             'deixo anotado para o balcão.'
                           )
 WHERE key = 'sales.pharmacist_validation'
   AND NOT (default_config ? 'not_found_message');
