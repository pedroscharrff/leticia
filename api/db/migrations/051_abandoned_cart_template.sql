-- ─────────────────────────────────────────────────────────────────────────────
-- Template editável da mensagem de recuperação de carrinho abandonado.
--
-- Antes: `_build_message` em `api/workers/jobs/abandoned_cart.py` tinha o
-- texto hardcoded com a única personalização sendo o nome do agente e a
-- lista de itens. Agora a mensagem vem do `message_template` dentro da
-- config da capability `sales.abandoned_cart` — operador edita pelo portal,
-- sem deploy.
--
-- Placeholders disponíveis (todos opcionais; placeholder ausente vira ""):
--   {saudacao}       — "Oi {nome}!" se houver nome, senão "Oi!"
--   {nome_cliente}   — nome do customer ou ""
--   {agent_name}     — nome configurado na persona ("Atendente" por default)
--   {itens}          — primeiros 3 itens em texto, ex: "Dipirona, Tylenol"
--   {qtde_itens}     — número total de itens no carrinho
--   {mais_itens}     — " e mais N item(ns)" quando passar de 3, senão ""
--   {subtotal}       — subtotal formatado em BRL ("R$ 89,90")
-- ─────────────────────────────────────────────────────────────────────────────

UPDATE public.capability_catalog
   SET default_config = COALESCE(default_config, '{}'::jsonb)
                        || jsonb_build_object(
                             'message_template',
                             '{saudacao} Aqui é o(a) {agent_name}. 👋 ' ||
                             'Vi que você deixou *{itens}*{mais_itens} no carrinho mais cedo. ' ||
                             'Quer que eu finalize o pedido pra você, ou prefere ajustar algo?'
                           )
 WHERE key = 'sales.abandoned_cart'
   AND NOT (default_config ? 'message_template');

-- Atualiza o config_schema (JSON Schema usado por editores genéricos da UI)
-- para incluir message_template como textarea longo. O editor dedicado na
-- página de Recuperação não depende disso, mas o card em Vendas › Recursos
-- sim — assim os dois caminhos ficam coerentes.
UPDATE public.capability_catalog
   SET config_schema = jsonb_set(
       COALESCE(config_schema, '{"type":"object","properties":{}}'::jsonb),
       '{properties,message_template}',
       '{"type":"string",
         "title":"Texto da mensagem",
         "description":"Use {saudacao}, {agent_name}, {itens}, {mais_itens}, {qtde_itens}, {nome_cliente}, {subtotal}.",
         "format":"textarea",
         "default":"{saudacao} Aqui é o(a) {agent_name}. 👋 Vi que você deixou *{itens}*{mais_itens} no carrinho mais cedo. Quer que eu finalize o pedido pra você, ou prefere ajustar algo?"
        }'::jsonb,
       true
     )
 WHERE key = 'sales.abandoned_cart';

-- Tenants que JÁ tinham override de config (sem template) recebem o default
-- só na leitura via merge {**default, **tenant} em `_load_tenant_state`, então
-- não precisa de UPDATE em `tenant_capabilities` aqui — quem ainda não tem o
-- campo herda o default automaticamente.
