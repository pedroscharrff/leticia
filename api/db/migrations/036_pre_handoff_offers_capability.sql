-- ─────────────────────────────────────────────────────────────────────────────
-- Capability: sales.pre_handoff_offers
--
-- Antes do bot transferir a conversa para um atendente humano, anexa uma lista
-- curta de ofertas vigentes (tabela public.offers) à mensagem que vai ao
-- cliente. É a última oportunidade de retenção comercial.
--
-- Migration aditiva: usa ON CONFLICT para não interferir com o seed do 022.
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('sales.pre_handoff_offers',
 'Ofertas antes da Transferência',
 'vendas',
 'Antes de transferir para um atendente, o robô envia 1-3 ofertas vigentes como última tentativa de retenção.',
 $md$**Como funciona**
Quando o robô decide transferir a conversa para um humano (escalate), antes de chamar o handoff ele consulta a tabela de ofertas (`public.offers`) do tenant e anexa as ofertas vigentes (top N por prioridade) à mensagem que vai ao cliente.

**Quando ativar**
Quando você tem campanhas/ofertas ativas e quer aproveitar o momento de saída do bot para uma última tentativa de venda. Funciona bem para cupons de primeira compra, kits sazonais e queima de estoque.

**Quando NÃO ativar**
Se o motivo dominante de transferência for reclamação/SAC — anunciar oferta nesse momento incomoda. Use só se a sua operação tem volume de transferência por dúvida comercial.

**Exemplo**
> Robô (mensagem de transferência): "Estou te passando para um atendente, um momento."
> + bloco de ofertas: "Antes de transferir, veja nossas ofertas: • Kit Gripe 12% OFF • Frete grátis acima de R$ 80"$md$,
 'Última chance de conversão',
 'basic', '{}', '{}',
 '{"type":"object","properties":{
    "max_offers":{"type":"integer","title":"Máximo de ofertas exibidas","default":3,"minimum":1,"maximum":5},
    "header_text":{"type":"string","title":"Texto introdutório do bloco","default":"Antes de transferir, veja nossas ofertas:"}
 }}'::jsonb,
 '{"max_offers":3,"header_text":"Antes de transferir, veja nossas ofertas:"}'::jsonb,
 FALSE, 'ga', 'tag', 45)
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
