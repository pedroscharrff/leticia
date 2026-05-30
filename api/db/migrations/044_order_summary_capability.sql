-- 044_order_summary_capability.sql
-- Capability: enviar um resumo do pedido do cliente logo após a transferência
-- para o atendente humano (handoff). Template 100% customizável pelo tenant.
--
-- Default OFF → nenhum tenant existente muda de comportamento até ligar em
-- /portal/recursos. Espelha o padrão de 036_pre_handoff_offers_capability,
-- inclusive ON CONFLICT para ser idempotente / re-rodável.

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('sales.order_summary_after_handoff',
 'Resumo do pedido na transferência',
 'vendas',
 'Ao transferir para um atendente, envia um resumo do que o cliente pediu (itens, quantidades e total).',
 $md$**Como funciona**
Quando o atendimento é transferido para um humano (balcão), o robô envia automaticamente um resumo do carrinho do cliente como uma mensagem separada, logo após a mensagem de transferência. O atendente recebe a conversa já com o pedido organizado.

**Personalização**
O texto é totalmente customizável: cabeçalho, modelo de cada item (com os placeholders `{quantidade}` `{nome}` `{preco_unit}` `{preco_total}`), exibição do total e rodapé.

**Quando ativar**
Operações que fecham pedido no balcão/atendente e querem que o humano já receba a lista pronta, reduzindo retrabalho.$md$,
 'Acelera o fechamento: o atendente recebe o pedido já estruturado.',
 'basic', '{}', '{}',
 '{"type":"object","properties":{
    "header_text":{"type":"string","title":"Cabeçalho do resumo","default":"📋 *Resumo do seu pedido:*"},
    "item_template":{"type":"string","title":"Modelo de cada item","default":"• {quantidade}x {nome} — {preco_total}"},
    "show_total":{"type":"boolean","title":"Mostrar total","default":true},
    "total_label":{"type":"string","title":"Rótulo do total","default":"*Total*"},
    "footer_text":{"type":"string","title":"Rodapé (opcional)","default":""}
 }}'::jsonb,
 '{"header_text":"📋 *Resumo do seu pedido:*","item_template":"• {quantidade}x {nome} — {preco_total}","show_total":true,"total_label":"*Total*","footer_text":"Um atendente vai confirmar disponibilidade e finalizar. 😊"}'::jsonb,
 FALSE, 'ga', 'receipt', 46)
ON CONFLICT (key) DO UPDATE SET
    name            = EXCLUDED.name,
    category        = EXCLUDED.category,
    short_desc      = EXCLUDED.short_desc,
    long_desc       = EXCLUDED.long_desc,
    impact_label    = EXCLUDED.impact_label,
    min_plan        = EXCLUDED.min_plan,
    depends_on      = EXCLUDED.depends_on,
    requires_secret = EXCLUDED.requires_secret,
    config_schema   = EXCLUDED.config_schema,
    default_config  = EXCLUDED.default_config,
    status          = EXCLUDED.status,
    icon            = EXCLUDED.icon,
    sort_order      = EXCLUDED.sort_order,
    updated_at      = NOW();
