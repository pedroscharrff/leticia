-- ─────────────────────────────────────────────────────────────────────────────
-- Capabilities: feature flags plugáveis por tenant.
--
-- Cada "capability" é uma capacidade do bot (cross-sell, frete por CEP, PIX,
-- recuperação de carrinho, etc.) que o tenant pode ligar/desligar de forma
-- INDEPENDENTE no portal. Cada capability tem:
--   • metadata (nome, descrição curta/longa, impacto esperado, status)
--   • restrições (plano mínimo, capabilities/secrets dependentes)
--   • schema de configuração (JSON Schema) p/ o portal gerar o form
--
-- Tabelas:
--   • public.capability_catalog   — catálogo global, gerenciado pela B4B
--   • public.tenant_capabilities  — flags + config por tenant
--
-- Convenção de keys (namespaced):
--   attendance.*  — qualidade do atendimento
--   sales.*       — alavancas de venda (upsell, recuperação)
--   delivery.*    — entrega/frete
--   payments.*    — pagamentos
--   analytics.*   — métricas avançadas
--   intelligence.* — guardrails específicos
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS public.capability_catalog (
    key             TEXT        PRIMARY KEY,
    name            TEXT        NOT NULL,
    category        TEXT        NOT NULL CHECK (category IN (
                        'atendimento', 'vendas', 'pagamentos_entrega',
                        'analise', 'inteligencia'
                    )),
    short_desc      TEXT        NOT NULL,
    long_desc       TEXT        NOT NULL DEFAULT '',
    impact_label    TEXT        NOT NULL DEFAULT '',
    min_plan        TEXT        NOT NULL DEFAULT 'basic' CHECK (min_plan IN (
                        'basic', 'pro', 'enterprise'
                    )),
    depends_on      TEXT[]      NOT NULL DEFAULT '{}',
    requires_secret TEXT[]      NOT NULL DEFAULT '{}',
    config_schema   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    default_config  JSONB       NOT NULL DEFAULT '{}'::jsonb,
    default_enabled BOOLEAN     NOT NULL DEFAULT FALSE,
    status          TEXT        NOT NULL DEFAULT 'ga' CHECK (status IN (
                        'ga', 'beta', 'experimental'
                    )),
    icon            TEXT        NOT NULL DEFAULT 'sparkles',
    sort_order      INTEGER     NOT NULL DEFAULT 100,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cap_catalog_category
    ON public.capability_catalog (category, sort_order);


CREATE TABLE IF NOT EXISTS public.tenant_capabilities (
    tenant_id      UUID        NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    capability_key TEXT        NOT NULL REFERENCES public.capability_catalog(key) ON DELETE CASCADE,
    enabled        BOOLEAN     NOT NULL DEFAULT FALSE,
    config         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by     TEXT,
    PRIMARY KEY (tenant_id, capability_key)
);

CREATE INDEX IF NOT EXISTS idx_tenant_caps_enabled
    ON public.tenant_capabilities (tenant_id) WHERE enabled = TRUE;


-- ─────────────────────────────────────────────────────────────────────────────
-- SEED — 11 capabilities do Sprint Vendas Inteligentes
--
-- Idempotente: ON CONFLICT atualiza name/desc/etc., mas preserva flags por
-- tenant. Para forçar reset por tenant, faça TRUNCATE tenant_capabilities.
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES

-- ── ATENDIMENTO ─────────────────────────────────────────────────────────────
('attendance.customer_memory',
 'Memória de Clientes',
 'atendimento',
 'O robô lembra alergias, medicamentos contínuos e preferências de cada cliente.',
 $md$**Como funciona**
Sempre que um cliente menciona uma alergia, um medicamento de uso contínuo, ou uma preferência (ex.: "prefiro genérico"), o robô registra automaticamente no perfil. Em conversas futuras, essa memória é injetada no prompt e influencia o atendimento.

**Quando ativar**
Sempre. É a base para personalização real — sem isso, cada conversa começa do zero.

**Quando NÃO ativar**
Quase nunca. Só desligue se a sua operação for puramente transacional (ex.: balcão de troca).

**Exemplo**
> Cliente: "Você tem dipirona?"
> Robô (com memória ON, sabendo que cliente é alérgico): "Vi aqui que você relatou alergia à dipirona da última vez. Quer que eu sugira um paracetamol equivalente?"$md$,
 'Personalização real do atendimento',
 'basic', '{}', '{}',
 '{"type":"object","properties":{
    "track_allergies":{"type":"boolean","title":"Registrar alergias","default":true},
    "track_continuous_meds":{"type":"boolean","title":"Registrar medicamentos contínuos","default":true},
    "track_preferences":{"type":"boolean","title":"Registrar preferências de marca/genérico","default":true}
 }}'::jsonb,
 '{"track_allergies":true,"track_continuous_meds":true,"track_preferences":true}'::jsonb,
 TRUE, 'ga', 'brain', 10),

('attendance.interactive_buttons',
 'Botões Interativos no WhatsApp',
 'atendimento',
 'Em vez de só texto, o robô envia botões clicáveis (Comprar / Tirar dúvida / Atendente).',
 $md$**Como funciona**
Aproveita o recurso nativo de **interactive messages** do WhatsApp Cloud API: até 3 botões de resposta rápida ou listas com até 10 opções. O cliente clica e o robô recebe o texto correspondente — fluxo mais limpo, menos digitação, menos erros de interpretação.

**Quando ativar**
Sempre que o seu canal for WhatsApp Cloud (não funciona em Z-API legado de forma confiável).

**Quando NÃO ativar**
Se a maioria dos seus clientes usa WhatsApp Web/Desktop antigo (botões só renderizam bem no app).

**Exemplo**
Saudação inicial vira: "Olá! Como posso ajudar?" + botões [🛒 Comprar] [❓ Tirar dúvida] [👤 Atendente]$md$,
 'Mais cliques, menos atrito',
 'basic', '{}', '{}',
 '{"type":"object","properties":{
    "use_quick_replies":{"type":"boolean","title":"Usar botões de resposta rápida (até 3)","default":true},
    "use_lists":{"type":"boolean","title":"Usar listas (até 10 opções)","default":true}
 }}'::jsonb,
 '{"use_quick_replies":true,"use_lists":true}'::jsonb,
 FALSE, 'ga', 'mouse-pointer-click', 20),


-- ── VENDAS ──────────────────────────────────────────────────────────────────
('sales.cross_sell',
 'Cross-sell Inteligente',
 'vendas',
 'Após adicionar item ao carrinho, o robô sugere 1 complemento relevante.',
 $md$**Como funciona**
Quando o cliente adiciona um produto ao carrinho, o robô consulta a tabela de relações (`product_relations`) e oferece o complemento mais relevante (ex.: dipirona → soro fisiológico; ibuprofeno → omeprazol; vitamina C → zinco). Respeita alergias do cliente (se memória estiver ON).

**Quando ativar**
Quando você quer aumentar o ticket médio. Cross-sell bem feito sobe ticket médio em 15-25% em farmácia.

**Quando NÃO ativar**
Se seu estoque é muito enxuto (poucos produtos cadastrados) — sem catálogo, não há o que sugerir.

**Exemplo**
> Cliente: "Quero dipirona 500mg"
> Robô: "Adicionei Dipirona 500mg R$ 8,90 no seu carrinho. 💡 *Quem leva dipirona costuma levar também soro fisiológico para hidratação — temos por R$ 4,50. Quer adicionar?*"$md$,
 '↑ ticket médio +15-25%',
 'pro', '{}', '{}',
 '{"type":"object","properties":{
    "max_suggestions_per_turn":{"type":"integer","title":"Máximo de sugestões por turno","default":1,"minimum":1,"maximum":3},
    "min_relation_weight":{"type":"number","title":"Confiança mínima da sugestão (0-1)","default":0.5,"minimum":0,"maximum":1}
 }}'::jsonb,
 '{"max_suggestions_per_turn":1,"min_relation_weight":0.5}'::jsonb,
 FALSE, 'ga', 'shopping-cart', 30),

('sales.combos',
 'Combos com Desconto',
 'vendas',
 'O robô oferece combos pré-definidos (kit gripe, kit dor, etc.) com desconto automático.',
 $md$**Como funciona**
Você define combos no portal (ex.: "Kit Gripe" = paracetamol + xarope + soro). Quando o cliente pede 2 ou mais itens do combo, o robô oferece automaticamente o pacote completo com X% de desconto.

**Quando ativar**
Forte para datas sazonais (gripe, alergia, dor de cabeça). Aumenta ticket médio e gira estoque.

**Quando NÃO ativar**
Se você não tem margem para desconto. Combos sem desconto não convertem.

**Exemplo**
> Cliente: "Quero paracetamol e xarope"
> Robô: "Posso te oferecer o nosso *Kit Gripe Completo* (paracetamol + xarope + soro fisiológico) com 12% de desconto. Sai R$ 28,90 em vez de R$ 32,80. Quer?"$md$,
 'Sobe ticket em datas sazonais',
 'pro', '{}', '{}',
 '{"type":"object","properties":{
    "auto_discount_pct":{"type":"number","title":"Desconto padrão sugerido (%)","default":10,"minimum":0,"maximum":50}
 }}'::jsonb,
 '{"auto_discount_pct":10}'::jsonb,
 FALSE, 'beta', 'package', 40),

('sales.abandoned_cart',
 'Recuperação de Carrinho Abandonado',
 'vendas',
 'Carrinhos parados por 4h+ recebem mensagem automática lembrando o cliente.',
 $md$**Como funciona**
Um job rodando a cada 1 hora procura carrinhos com itens que não foram convertidos em pedido nas últimas 4 horas (configurável). Envia uma mensagem amigável via o canal do cliente. **Respeita o horário comercial da persona** (não dispara de madrugada).

**Quando ativar**
Quase sempre. Recuperação de carrinho é a maior alavanca de receita "grátis" que existe — 20-30% dos carrinhos podem ser recuperados.

**Quando NÃO ativar**
Se seus clientes reclamam de mensagens "spam" — comece com `max_attempts=1`.

**Exemplo (após 4h)**
> Robô: "Oi {nome}! Vi que você deixou Dipirona e Soro no carrinho mais cedo. Quer que eu finalize o pedido pra você?"$md$,
 '↓ abandono em 25-30%',
 'pro', '{}', '{}',
 '{"type":"object","properties":{
    "delay_hours":{"type":"integer","title":"Disparar após (horas de inatividade)","default":4,"minimum":1,"maximum":48},
    "max_attempts":{"type":"integer","title":"Máximo de tentativas por carrinho","default":1,"minimum":1,"maximum":3},
    "quiet_start":{"type":"string","title":"Início do silêncio (HH:MM)","default":"21:00"},
    "quiet_end":{"type":"string","title":"Fim do silêncio (HH:MM)","default":"08:00"}
 }}'::jsonb,
 '{"delay_hours":4,"max_attempts":1,"quiet_start":"21:00","quiet_end":"08:00"}'::jsonb,
 FALSE, 'ga', 'shopping-bag', 50),

('sales.continuous_refill_nudge',
 'Lembrete de Recompra (Medicamentos Contínuos)',
 'vendas',
 'Cliente que toma medicamento contínuo recebe lembrete D-3 do fim da cartela.',
 $md$**Como funciona**
Depende de **Memória de Clientes** ativa. Quando o robô registra que o cliente toma um medicamento contínuo (ex.: Losartana 30 dias), um job diário verifica quando a cartela está terminando e envia lembrete proativo.

**Quando ativar**
Para farmácias que vendem MUITO medicamento de uso contínuo (hipertensão, diabetes, anticoncepcional, colesterol). Maior alavanca de recompra.

**Quando NÃO ativar**
Se sua farmácia é mais de balcão/conveniência (vitamínicos, beleza, OTC).

**Exemplo (D-3)**
> Robô: "Oi {nome}, vi que sua Losartana 50mg deve estar terminando esta semana. Quer que eu já separe uma cartela para você buscar?"$md$,
 '↑ recompra +30%',
 'pro', '{"attendance.customer_memory"}', '{}',
 '{"type":"object","properties":{
    "days_before_refill":{"type":"integer","title":"Avisar quantos dias antes","default":3,"minimum":1,"maximum":15},
    "time_of_day":{"type":"string","title":"Horário do envio (HH:MM)","default":"09:00"}
 }}'::jsonb,
 '{"days_before_refill":3,"time_of_day":"09:00"}'::jsonb,
 FALSE, 'ga', 'pill', 60),


-- ── PAGAMENTOS & ENTREGA ────────────────────────────────────────────────────
('delivery.shipping_by_cep',
 'Cálculo de Frete por CEP',
 'pagamentos_entrega',
 'O robô calcula automaticamente o frete a partir do CEP do cliente.',
 $md$**Como funciona**
Você cadastra suas regras de frete no portal (faixas de CEP → valor + prazo). Quando o cliente fornece o CEP, o robô consulta a tabela e adiciona o frete ao subtotal. Pode oferecer **frete grátis acima de um valor**.

**Quando ativar**
Se você faz entregas. Hoje o robô fecha pedido sem calcular frete e o cliente fica surpreso.

**Quando NÃO ativar**
Se a farmácia é só retirada no balcão.

**Exemplo**
> Cliente: "Meu CEP é 01310-100"
> Robô: "Entregamos no seu endereço por R$ 8,00 em até 2 dias úteis. Seu total fica R$ 67,80. Confirma?"$md$,
 'Fecha venda sem surpresa',
 'basic', '{}', '{}',
 '{"type":"object","properties":{
    "default_eta_days":{"type":"integer","title":"Prazo padrão quando CEP não bater (dias)","default":3,"minimum":1,"maximum":30},
    "free_above":{"type":"number","title":"Frete grátis acima de (R$)","default":0,"minimum":0}
 }}'::jsonb,
 '{"default_eta_days":3,"free_above":0}'::jsonb,
 FALSE, 'ga', 'truck', 70),

('payments.pix_asaas',
 'PIX no Chat (via Asaas)',
 'pagamentos_entrega',
 'O robô gera link/QR PIX direto na conversa. Pagamento confirmado automaticamente.',
 $md$**Como funciona**
Depois que o pedido é confirmado, o robô gera uma cobrança PIX via Asaas e envia o **QR code** + **código copia-cola** direto no WhatsApp. Quando o Asaas confirma o pagamento (via webhook), o robô avisa o cliente e marca o pedido como `paid`.

**Quando ativar**
Se você quer fechar 100% da venda dentro do WhatsApp, sem precisar repassar para atendente humano confirmar pagamento.

**Quando NÃO ativar**
Se você não usa Asaas. Roadmap: PagSeguro, Mercado Pago, Stripe.

**Exemplo**
> Robô: "Pedido confirmado! Aqui está seu PIX: [QR code]. Código copia-cola: 00020126...
> Assim que recebermos o pagamento eu te aviso."$md$,
 '100% dentro do WhatsApp',
 'pro', '{}', '{ASAAS_API_KEY}',
 '{"type":"object","properties":{
    "auto_send_after_confirm":{"type":"boolean","title":"Enviar PIX automaticamente ao fechar pedido","default":true},
    "expires_minutes":{"type":"integer","title":"Validade do PIX (minutos)","default":60,"minimum":10,"maximum":1440}
 }}'::jsonb,
 '{"auto_send_after_confirm":true,"expires_minutes":60}'::jsonb,
 FALSE, 'ga', 'qr-code', 80),


-- ── ANÁLISE ─────────────────────────────────────────────────────────────────
('analytics.sales_kpis',
 'KPIs de Vendas',
 'analise',
 'Dashboard com conversion rate, abandono, ticket médio, LTV e funil de vendas.',
 $md$**Como funciona**
Materializa uma view SQL com os KPIs principais e expõe na tela **Análise › Vendas & KPIs**: taxa de conversão, abandono de carrinho, ticket médio, upsell rate, recuperação de carrinho, LTV por cliente.

**Quando ativar**
Sempre. Sem KPI você não sabe se o bot está vendendo ou só conversando.

**Quando NÃO ativar**
Nunca.$md$,
 'Visibilidade total',
 'basic', '{}', '{}',
 '{"type":"object","properties":{
    "refresh_minutes":{"type":"integer","title":"Atualizar dashboard a cada (min)","default":60,"minimum":5,"maximum":1440}
 }}'::jsonb,
 '{"refresh_minutes":60}'::jsonb,
 TRUE, 'ga', 'bar-chart-3', 90),

('analytics.ltv_segmentation',
 'Segmentação por LTV',
 'analise',
 'Identifica clientes VIP, recorrentes e em risco. Permite ações dirigidas.',
 $md$**Como funciona**
Calcula o **LTV (Lifetime Value)** de cada cliente e segmenta automaticamente: VIP (acima do threshold), Recorrente (3+ compras), Esporádico (1-2 compras), Em risco (sem compra há 60+ dias).

**Quando ativar**
Quando você já tem volume (50+ clientes recorrentes). Antes disso a segmentação é ruído.

**Exemplo**
> Tela Clientes destaca: "Maria Silva — VIP — LTV R$ 1.230 — última compra há 12 dias"$md$,
 'Foco em quem importa',
 'pro', '{"analytics.sales_kpis"}', '{}',
 '{"type":"object","properties":{
    "vip_threshold":{"type":"number","title":"LTV mínimo para VIP (R$)","default":500,"minimum":0}
 }}'::jsonb,
 '{"vip_threshold":500}'::jsonb,
 FALSE, 'ga', 'crown', 100),


-- ── INTELIGÊNCIA ────────────────────────────────────────────────────────────
('intelligence.allergy_guard',
 'Guarda de Alergias',
 'inteligencia',
 'Bloqueia ou avisa quando cliente pede medicamento ao qual declarou alergia.',
 $md$**Como funciona**
Depende de **Memória de Clientes** ativa. Quando o cliente pede um produto que contém ingrediente ao qual ele declarou alergia, o robô bloqueia (modo `block`) ou apenas alerta (modo `warn`) antes de adicionar ao carrinho.

**Quando ativar**
Sempre. É segurança farmacêutica básica.

**Modo bloquear vs avisar**
- `block`: robô recusa e sugere alternativa.
- `warn`: robô avisa o cliente, pergunta se quer continuar mesmo assim, registra na conversa.

**Exemplo (block)**
> Cliente: "Pode mandar dipirona"
> Robô: "Vi no seu perfil que você tem alergia à dipirona. Vou sugerir um paracetamol equivalente — tem efeito similar e é seguro pra você."$md$,
 'Segurança farmacêutica',
 'basic', '{"attendance.customer_memory"}', '{}',
 '{"type":"object","properties":{
    "mode":{"type":"string","title":"Modo de ação","enum":["block","warn"],"default":"warn"}
 }}'::jsonb,
 '{"mode":"warn"}'::jsonb,
 TRUE, 'ga', 'shield-alert', 110)

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
