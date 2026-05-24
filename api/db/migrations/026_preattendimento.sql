-- ─────────────────────────────────────────────────────────────────────────────
-- Pré-atendimento sem consulta de estoque (sales.stock_check capability).
--
-- Mudanças:
--   1) Seed da capability 'sales.stock_check' no catálogo global.
--      • default_enabled = TRUE  → comportamento atual preservado para todos.
--      • Desligar = modo pré-atendimento: agente coleta pedidos livremente e
--        transfere para o atendente humano (balcão) via handoff configurado.
--
--   2) Nenhuma mudança de schema nas tabelas de pedidos: o status
--      'aguardando_balcao' é apenas um valor TEXT na coluna status — a coluna
--      não tem CHECK constraint que liste valores válidos.
--      (Validação fica na camada da API em routers/orders.py)
-- ─────────────────────────────────────────────────────────────────────────────

INSERT INTO public.capability_catalog (
    key,
    name,
    category,
    short_desc,
    long_desc,
    impact_label,
    min_plan,
    depends_on,
    requires_secret,
    config_schema,
    default_config,
    default_enabled,
    status,
    icon,
    sort_order
) VALUES (
    'sales.stock_check',
    'Consulta de Estoque no Chat',
    'vendas',
    'Quando ativo, o agente consulta estoque e preços em tempo real. Desativado, coleta pedidos livremente e passa para o atendente finalizar no balcão.',

    $LONGDESC$## Como funciona

### Com consulta de estoque (padrão)
O agente verifica disponibilidade e preço de cada produto antes de adicionar
ao carrinho. O pedido é criado e pago diretamente pelo WhatsApp.

**Ideal para:** farmácias com estoque sincronizado (ERP/integração ativa).

### Sem consulta de estoque — Pré-atendimento
O agente anota os pedidos do cliente **sem verificar estoque ou preços**,
depois transfere automaticamente para um atendente humano finalizar no balcão.

**Fluxo:**
1. Cliente envia mensagem → agente coleta os itens desejados.
2. Agente confirma (ou solicita) os dados cadastrais exigidos.
3. Agente repete a lista ao cliente e pede confirmação.
4. Pedido é salvo com status "Aguardando balcão" no portal.
5. Atendente humano recebe o pedido pelo WhatsApp via transferência.

**Ideal para:** farmácias de bairro que querem agilizar o primeiro contato
sem precisar sincronizar o estoque — o bot "desafoga" a fila enquanto o
atendente finaliza com calma.

## Quando NÃO usar em modo pré-atendimento
- Você já tem integração de estoque funcionando.
- Quer que o cliente pague pelo WhatsApp (PIX no chat requer estoque).
- Tem catálogo muito grande e precisa mostrar preços ao cliente.

## Pré-requisitos para o pré-atendimento
- Configure a **transferência ao atendente** em *Configuração › Canais & Integrações*
  no canal correspondente (base_url, token e queue_id).
- Sem essa configuração o pedido ainda é salvo, mas a transferência
  automática não ocorre.$LONGDESC$,

    'Atende farmácias sem integração de estoque',
    'basic',
    '{}',
    '{}',
    '{"type": "object", "properties": {}, "additionalProperties": false}',
    '{}',
    TRUE,
    'ga',
    'package-search',
    15
)
ON CONFLICT (key) DO UPDATE SET
    name        = EXCLUDED.name,
    short_desc  = EXCLUDED.short_desc,
    long_desc   = EXCLUDED.long_desc,
    updated_at  = NOW();