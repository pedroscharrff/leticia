-- ─────────────────────────────────────────────────────────────────────────────
-- Capability: inventory.track_stock
--
-- Controla se o AGENTE recebe a quantidade em estoque do produto na consulta
-- ao catálogo. Em farmácias com integração em tempo real (ERP/PDV via REST/SQL),
-- a quantidade é confiável e ajuda o agente a tomar decisões internas:
--   • sugerir genérico quando estoque baixo
--   • limitar quantidade no carrinho
--   • alertar balconista quando produto está perto de zerar
--
-- IMPORTANTE: mesmo quando ATIVA, o agente é instruído a NUNCA citar o número
-- ao cliente final. Para o cliente, a resposta é sempre "tem" ou "não tem".
-- A quantidade é informação interna para decisões do bot.
--
-- Em farmácias com catálogo via Sheets/Excel/CSV (estoque não-autoritativo),
-- esta capability deve ficar OFF — o agente sabe apenas se o produto existe.
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('inventory.track_stock',
 'Quantidade em estoque para o agente',
 'vendas',
 'O agente passa a conhecer a quantidade em estoque (para decisões internas como sugerir alternativa quando baixo). O cliente continua vendo apenas "tem" ou "não tem".',
 $md$**O que muda**
Quando ATIVA, a tool interna `buscar_produto` retorna ao agente o nome, preço E a quantidade em estoque do produto. O agente usa esse dado para **decisões internas**:
- Sugerir um genérico ou marca alternativa quando o estoque está baixo
- Limitar a quantidade que o cliente pode adicionar ao carrinho
- Avisar internamente o balconista que um SKU está zerando

**O cliente NUNCA vê o número.** O bot é instruído a responder ao cliente apenas com "temos sim" ou "esse não temos" — independentemente desta capability estar ON ou OFF. A diferença é só na qualidade da decisão do agente.

**Quando ativar**
- Você integra o catálogo via REST API ou SQL direto com seu PDV/ERP
- O estoque é atualizado a cada poucos minutos
- Você quer que o bot evite oferecer produtos quase esgotados

**Quando NÃO ativar (default)**
- Catálogo via Google Sheets / Excel / CSV
- Coluna de estoque inexistente, zerada ou desatualizada
- Balconista valida disponibilidade no momento de fechar
- Você quer o comportamento mais conservador: bot só sabe "tem ou não tem"
$md$,
 'Decisões mais inteligentes do agente',
 'basic', '{}', '{}',
 '{}'::jsonb,
 '{}'::jsonb,
 FALSE, 'ga', 'box', 50)
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
