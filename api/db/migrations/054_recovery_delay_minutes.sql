-- ─────────────────────────────────────────────────────────────────────────────
-- 054_recovery_delay_minutes.sql
--
-- Granularidade fina pro disparo da recuperação. Antes: `delay_hours` (int).
-- Agora: `delay_minutes` é o campo canônico (1–1440), e `delay_hours` vira
-- legacy — o job lê `delay_minutes` se presente, senão cai pra `delay_hours*60`.
--
-- Default novo: 240 min (= 4h, mantém o comportamento de antes).
--
-- Motivo: operadores querem testar com janelas curtas (5/15 min) sem precisar
-- mexer em SQL — o campo `delay_hours` (int) não acomodava.
-- ─────────────────────────────────────────────────────────────────────────────

UPDATE public.capability_catalog
   SET default_config = COALESCE(default_config, '{}'::jsonb)
                        || jsonb_build_object('delay_minutes', 240)
 WHERE key = 'sales.abandoned_cart'
   AND NOT (default_config ? 'delay_minutes');

-- Atualiza config_schema: adiciona delay_minutes e marca delay_hours como
-- legacy (description sinaliza que delay_minutes tem precedência). Não
-- removemos delay_hours pra não quebrar tenants que tinham override salvo.
UPDATE public.capability_catalog
   SET config_schema = jsonb_set(
                         jsonb_set(
                           COALESCE(config_schema, '{"type":"object","properties":{}}'::jsonb),
                           '{properties,delay_minutes}',
                           '{"type":"integer",
                             "title":"Aguardar antes do nudge (minutos)",
                             "description":"Tempo de inatividade do cliente antes de disparar a mensagem de recuperação. 4h = 240 min.",
                             "minimum":1,
                             "maximum":1440,
                             "default":240}'::jsonb,
                           true
                         ),
                         '{properties,delay_hours}',
                         '{"type":"integer",
                           "title":"(Legado) Aguardar antes do nudge (horas)",
                           "description":"Ignorado se delay_minutes estiver presente. Mantido para compatibilidade com tenants antigos.",
                           "minimum":1,
                           "maximum":24,
                           "deprecated":true}'::jsonb,
                         true
                       )
 WHERE key = 'sales.abandoned_cart';
