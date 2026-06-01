-- ─────────────────────────────────────────────────────────────────────────────
-- 052_cart_expiration.sql
--
-- Expiração automática de carrinho após envio da mensagem de recuperação.
-- Estende a capability `sales.abandoned_cart` com:
--
--   • expire_minutes (int 0–240, default 60)
--       Tempo após `sent_recovery_at` para considerar o carrinho expirado se
--       o cliente não voltou. 0 desliga a expiração (mantém comportamento
--       anterior — só envia recuperação, nunca expira).
--
--   • expire_message_template (string)
--       Mensagem final enviada ao cliente quando o carrinho expira. Mesmos
--       placeholders do `message_template` ({saudacao}, {agent_name},
--       {itens}, {mais_itens}, {qtde_itens}, {nome_cliente}, {subtotal}).
--
-- Aplicado: job `expire_abandoned_carts` (workers/jobs/expire_carts.py) que
-- roda no beat a cada 2 min. Ver SPEC 09.
-- ─────────────────────────────────────────────────────────────────────────────

-- Default no catálogo (lido por _load_tenant_state como base + merge tenant)
UPDATE public.capability_catalog
   SET default_config = COALESCE(default_config, '{}'::jsonb)
                        || jsonb_build_object(
                             'expire_minutes', 60,
                             'expire_message_template',
                             'Oi{nome_cliente}! Aqui é o(a) {agent_name}. ' ||
                             'Como não tive retorno, encerrei o atendimento por aqui — ' ||
                             'mas seu interesse por *{itens}*{mais_itens} fica registrado. ' ||
                             'Quando quiser retomar, é só me chamar. 👋'
                           )
 WHERE key = 'sales.abandoned_cart'
   AND NOT (default_config ? 'expire_minutes');

-- Atualiza config_schema para os editores genéricos (cards de capability em
-- Vendas › Recursos). A página dedicada de Recuperação tem UI própria.
UPDATE public.capability_catalog
   SET config_schema = jsonb_set(
                         jsonb_set(
                           COALESCE(config_schema, '{"type":"object","properties":{}}'::jsonb),
                           '{properties,expire_minutes}',
                           '{"type":"integer",
                             "title":"Expirar carrinho após (minutos)",
                             "description":"Após enviar a mensagem de recuperação, encerra o ticket e arquiva o carrinho como expirado se o cliente não responder neste prazo. 0 desativa.",
                             "minimum":0,
                             "maximum":240,
                             "default":60}'::jsonb,
                           true
                         ),
                         '{properties,expire_message_template}',
                         '{"type":"string",
                           "title":"Mensagem de expiração",
                           "description":"Texto final enviado ao cliente quando o carrinho expira. Mesmos placeholders da mensagem de recuperação.",
                           "format":"textarea"}'::jsonb,
                         true
                       )
 WHERE key = 'sales.abandoned_cart';
