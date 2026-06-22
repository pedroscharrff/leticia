-- 070_order_summary_payment_address.sql
-- Estende a capability `sales.order_summary_after_handoff` (mig 044) com duas
-- linhas DETERMINÍSTICAS opcionais no resumo do pedido:
--   • Forma de pagamento (quando houver — sai de cart.last_order.payment)
--   • Endereço de entrega (quando houver — montado do cadastro do cliente)
--
-- Ambas "quando houver": sem dado → linha omitida (pré-atendimento não tem
-- forma de pagamento; retirada/cadastro vazio não tem endereço).
--
-- Só mexe no catálogo público (default_config + config_schema). Idempotente via
-- ON CONFLICT (key) DO UPDATE. Tenants que já customizaram o template herdam os
-- novos campos pelos defaults (config esparsa — ver SPEC 04).

UPDATE public.capability_catalog
SET
    config_schema = '{"type":"object","properties":{
        "header_text":{"type":"string","title":"Cabeçalho do resumo","default":"📋 *Resumo do seu pedido:*"},
        "item_template":{"type":"string","title":"Modelo de cada item","default":"• {quantidade}x {nome} — {preco_total}"},
        "show_total":{"type":"boolean","title":"Mostrar total","default":true},
        "total_label":{"type":"string","title":"Rótulo do total","default":"*Total*"},
        "show_payment":{"type":"boolean","title":"Mostrar forma de pagamento (quando houver)","default":true},
        "payment_label":{"type":"string","title":"Rótulo do pagamento","default":"*Pagamento*"},
        "show_address":{"type":"boolean","title":"Mostrar endereço de entrega (quando houver)","default":true},
        "address_label":{"type":"string","title":"Rótulo do endereço","default":"*Entrega*"},
        "footer_text":{"type":"string","title":"Rodapé (opcional)","default":""}
     }}'::jsonb,
    default_config = '{
        "header_text":"📋 *Resumo do seu pedido:*",
        "item_template":"• {quantidade}x {nome} — {preco_total}",
        "show_total":true,
        "total_label":"*Total*",
        "show_payment":true,
        "payment_label":"*Pagamento*",
        "show_address":true,
        "address_label":"*Entrega*",
        "footer_text":"Um atendente vai confirmar disponibilidade e finalizar. 😊"
     }'::jsonb,
    long_desc = $md$**Como funciona**
Quando o atendimento é transferido para um humano (balcão), o robô envia automaticamente um resumo do carrinho do cliente como uma mensagem separada, logo após a mensagem de transferência. O atendente recebe a conversa já com o pedido organizado.

**Personalização**
O texto é totalmente customizável: cabeçalho, modelo de cada item (com os placeholders `{quantidade}` `{nome}` `{preco_unit}` `{preco_total}`), exibição do total, **forma de pagamento**, **endereço de entrega** e rodapé.

**Pagamento e endereço (determinísticos)**
A forma de pagamento e o endereço de entrega são preenchidos automaticamente a partir do que já foi coletado no atendimento — sem depender da interpretação do robô. Cada linha aparece **só quando há o dado**: pré-atendimento (sem forma de pagamento) e pedidos de retirada (sem endereço) omitem a linha correspondente.

**Quando ativar**
Operações que fecham pedido no balcão/atendente e querem que o humano já receba a lista pronta, reduzindo retrabalho.$md$,
    updated_at = NOW()
WHERE key = 'sales.order_summary_after_handoff';
