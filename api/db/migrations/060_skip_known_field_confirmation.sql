-- 060_skip_known_field_confirmation.sql
-- Capability: sales.skip_known_field_confirmation
--
-- Por padrão (modo pré-atendimento), o vendedor confirma com o cliente todo
-- campo do cadastro que já está salvo ("Posso confirmar seu nome como
-- João Silva?") antes de seguir para a coleta do pedido. Isso protege
-- tenants com base suja, mas vira atrito em farmácias com cadastro
-- confiável e clientes recorrentes.
--
-- Quando ESTA capability está ON, o vendedor CONFIA nos campos já
-- preenchidos e só pede os que estão vazios — segue direto pra coleta sem
-- repetir o que ele já sabe. Default OFF (opt-in — não muda comportamento
-- de tenants existentes).
--
-- Só tem efeito em pré-atendimento (sales.stock_check OFF). No modo
-- normal/ERP o vendedor já não força confirmação dos dados existentes.

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('sales.skip_known_field_confirmation',
 'Confiar nos dados já cadastrados (não reconfirmar)',
 'vendas',
 'O agente não confirma campos do cliente que já estão salvos — só pede o que está vazio. Reduz atrito em farmácias com cadastro confiável.',
 $md$**O problema**
No modo pré-atendimento, o vendedor confirma todo campo já salvo do cliente antes de coletar o pedido ("Posso confirmar seu nome como João Silva?"). Para farmácias com cadastro confiável e clientes recorrentes, isso vira atrito desnecessário — o cliente quer só fazer o pedido.

**Como funciona**
Quando ATIVA, o agente:
- Para campos já preenchidos no cadastro → CONFIA, não pergunta nada e segue direto pra coleta do pedido.
- Para campos vazios → pede normalmente, um por vez, e salva via `salvar_dados_cliente`.

Se o cliente quiser corrigir um dado por iniciativa própria, basta dizer ("na verdade meu nome é X") — o agente salva a correção normalmente.

**Quando ativar**
- Seu cadastro de clientes está confiável (poucos duplicados / dados sujos).
- A maioria dos seus pedidos são de clientes recorrentes e você quer agilizar o atendimento.

**Quando NÃO ativar (default)**
- Cadastro com muitos dados antigos / inconsistentes (CPF/endereço de clientes que mudaram).
- Você prefere segurança a velocidade — confirmar evita entregar pedido para o "João" errado.
$md$,
 'Atendimento mais rápido para clientes recorrentes',
 'basic', '{}', '{}',
 '{}'::jsonb,
 '{}'::jsonb,
 FALSE, 'ga', 'fast-forward', 56)
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
