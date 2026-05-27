-- ─────────────────────────────────────────────────────────────────────────────
-- Capability: inventory.track_stock
--
-- Controla se o agente vai informar QUANTIDADE em estoque ao cliente. Em
-- farmácias que mantêm o catálogo via Sheets/Excel/CSV, o campo `stock_qty`
-- frequentemente fica zerado (a planilha não tem coluna de estoque). Quando
-- essa capability está OFF, o `buscar_produto` apenas informa "disponível" se
-- o produto existe no catálogo, sem mencionar quantidade.
--
-- Ativar SÓ quando a fonte do estoque é confiável (integração com PDV/ERP em
-- tempo real).
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('inventory.track_stock',
 'Mostrar quantidade em estoque',
 'vendas',
 'O robô informa quantas unidades têm em estoque do produto consultado. Só ative se o estoque é atualizado em tempo real.',
 $md$**Como funciona**
Quando ATIVO, a tool `buscar_produto` informa ao cliente "X unidades em estoque" junto com o preço. Útil para farmácias com integração via REST/SQL com o PDV, onde o estoque é confiável.

Quando DESATIVADO (padrão), a tool só responde "disponível" — informa que o produto está no catálogo sem citar quantidade. Esse é o modo recomendado para quem mantém o catálogo via Sheets/Excel/CSV, onde a coluna de estoque costuma estar zerada ou desatualizada.

**Quando ativar**
- Você tem integração via REST API ou SQL com seu PDV
- Os syncs rodam várias vezes ao dia
- Não tem risco de o bot vender produto que acabou no balcão

**Quando NÃO ativar**
- Catálogo via Google Sheets / Excel / CSV (estoque não é autoritativo)
- Atualizações esporádicas
- Balconista valida disponibilidade antes de fechar
$md$,
 'Aumenta confiança no checkout',
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
