-- 046_safety_guards_v2.sql
-- Pacote de novos validators determinísticos pós-LLM (default ON), e
-- atualização do default da `safety.availability_guard` (045) pra ON
-- (operadores que NÃO querem podem desligar explicitamente).
--
-- TODOS são gated por `inventory.track_stock` ON no nó do grafo — em modo
-- pré-atendimento (track_stock OFF), o nó é passthrough total. Garante que
-- o fluxo curto de balcão (anotar + transferir) não tenha overhead.
--
-- Novas capabilities:
--   • safety.price_guard          — pega preços citados que não batem com catálogo
--   • safety.prescription_guard   — pega "não precisa receita" sobre tarja
--   • safety.delivery_guard       — pega "frete grátis" sem regra configurada

-- ── price_guard ──────────────────────────────────────────────────────────────
INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('safety.price_guard',
 'Validador de preços',
 'safety',
 'Detecta quando o agente cita um preço que não bate com o cadastrado no catálogo.',
 $md$Cruza qualquer preço "R$ X" mencionado na resposta com os preços dos produtos consultados neste turno (via buscar_produto). Se algum preço citado não existir em nenhum produto pesquisado (tolerância R$ 0,01), reescreve a resposta com aviso pra cliente: "Vou conferir o valor com o atendente — pode estar desatualizado".

**Quando NÃO ativar:** se sua operação trabalha com descontos dinâmicos negociados pelo agente — o guard vai marcar como suspeito qualquer preço diferente do tabelado.$md$,
 'Evita cobrar preço inventado pelo LLM.',
 'basic', '{}', '{}',
 '{}'::jsonb, '{}'::jsonb,
 TRUE, 'ga', 'tag', 51)
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
    default_enabled = EXCLUDED.default_enabled,
    status          = EXCLUDED.status,
    icon            = EXCLUDED.icon,
    sort_order      = EXCLUDED.sort_order,
    updated_at      = NOW();

-- ── prescription_guard ───────────────────────────────────────────────────────
INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('safety.prescription_guard',
 'Validador de tarja (receita médica)',
 'safety',
 'Detecta quando o agente diz "não precisa receita" sobre um medicamento que exige.',
 $md$Para cada produto consultado via buscar_produto neste turno, verifica se está marcado como `prescription_required=TRUE` no catálogo. Se sim, e a resposta contém frases tipo "não precisa receita", "sem receita", "venda livre" — reescreve com aviso correto: "Esse medicamento exige receita médica, posso anotar pra você apresentar no balcão na hora da retirada".

**Por que padrão ON:** consequência regulatória (ANVISA) e de segurança do paciente. Desligar só se você tem outro mecanismo de validação.$md$,
 'Proteção regulatória + segurança do paciente. Crítico em farmácia.',
 'basic', '{}', '{}',
 '{}'::jsonb, '{}'::jsonb,
 TRUE, 'ga', 'shield-check', 52)
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
    default_enabled = EXCLUDED.default_enabled,
    status          = EXCLUDED.status,
    icon            = EXCLUDED.icon,
    sort_order      = EXCLUDED.sort_order,
    updated_at      = NOW();

-- ── delivery_guard ───────────────────────────────────────────────────────────
INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('safety.delivery_guard',
 'Validador de entrega/frete',
 'safety',
 'Detecta "frete grátis" prometido sem regra configurada.',
 $md$MVP: se a resposta contém "frete grátis" / "entrega grátis" e o tenant NÃO tem nenhuma regra em `tenant_shipping_rules` com `gratis_acima` cadastrado, reescreve com aviso pra confirmar com atendente.

Versão futura validará prazo + CEP + subtotal contra regras específicas.$md$,
 'Evita prometer entrega grátis quando não tem política.',
 'basic', '{}', '{}',
 '{}'::jsonb, '{}'::jsonb,
 TRUE, 'ga', 'truck', 53)
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
    default_enabled = EXCLUDED.default_enabled,
    status          = EXCLUDED.status,
    icon            = EXCLUDED.icon,
    sort_order      = EXCLUDED.sort_order,
    updated_at      = NOW();

-- ── Flipa o default da 045 (availability_guard) pra ON ──────────────────────
-- A 045 já rodou em instalações existentes com default FALSE; UPDATE direto
-- garante que novos tenants peguem o default novo. Tenants que JÁ tinham
-- override em `tenant_capabilities` ficam intactos (suas escolhas vencem).
UPDATE public.capability_catalog
   SET default_enabled = TRUE,
       updated_at      = NOW()
 WHERE key = 'safety.availability_guard'
   AND default_enabled IS DISTINCT FROM TRUE;
