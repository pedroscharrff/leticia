-- ─────────────────────────────────────────────────────────────────────────────
-- Guard-rail "medicamento não encontrado na bula" (validação farmacêutica).
--
-- Quando `sales.pharmacist_validation` está ON e o cliente cita um medicamento
-- que NÃO existe no bulário da ANVISA, o farmacêutico não pode inventar
-- apresentação/dosagem nem afirmar disponibilidade. Em vez disso, pergunta ao
-- cliente qual dosagem/apresentação ele deseja — e essa frase é editável por
-- tenant via `not_found_message` no config da capability.
--
-- Lido em `agents/nodes/skills/farmaceutico.py` → repassado para
-- `make_consultar_bula_tool(not_found_message=...)`. Default herdado por merge
-- {**default, **tenant} em capabilities; tenant sobrescreve pelo portal.
-- ─────────────────────────────────────────────────────────────────────────────

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

UPDATE public.capability_catalog
   SET config_schema = jsonb_set(
       COALESCE(config_schema, '{"type":"object","properties":{}}'::jsonb),
       '{properties,not_found_message}',
       '{"type":"string",
         "title":"Mensagem quando o remédio não está no bulário",
         "description":"Frase que o agente envia ao cliente quando o medicamento citado não é encontrado na base da ANVISA, para coletar a dosagem/apresentação desejada.",
         "format":"textarea",
         "default":"Não localizei esse medicamento na minha base. Qual a dosagem e a apresentação que você gostaria? Assim já deixo anotado para o balcão."
        }'::jsonb,
       true
     )
 WHERE key = 'sales.pharmacist_validation';
