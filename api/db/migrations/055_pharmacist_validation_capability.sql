-- 055_pharmacist_validation_capability.sql
-- Capability: sales.pharmacist_validation
--
-- Em PRÉ-ATENDIMENTO (sales.stock_check OFF), o vendedor não tem catálogo
-- autoritativo e tende a "inventar" dosagem/apresentação de medicamento ao
-- coletar o pedido. Quando ESTA capability está ON, sempre que o cliente cita
-- um medicamento por nome o vendedor passa o item ao agente farmacêutico, que
-- confirma na bula da ANVISA (tools `consultar_bula` / `consultar_bula_secao`)
-- ANTES de o item entrar na coleta — evitando anotar dosagens/marcas que não
-- existem.
--
-- IMPORTANTE (dependência operacional): o roteamento só acontece se o agente
-- `farmaceutico` estiver ATIVO para o tenant (em available_skills). Se a
-- farmácia tiver apenas o `vendedor` ativo, o handoff degrada silenciosamente
-- para o comportamento atual (sem validação). `depends_on` não expressa
-- dependência de SKILL (só de outra capability), então o gate fica no código
-- (`vendedor.py`) + esta nota.
--
-- Só tem efeito em pré-atendimento: no modo normal/ERP a validação de catálogo
-- já é feita por `buscar_produto` + availability_guard. Default OFF (opt-in —
-- não altera o comportamento de tenants existentes).

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('sales.pharmacist_validation',
 'Validação farmacêutica na coleta (pré-atendimento)',
 'vendas',
 'No pré-atendimento, o agente confere o medicamento na bula da ANVISA antes de anotá-lo — evita registrar dosagem/apresentação inexistente.',
 $md$**O problema**
No modo pré-atendimento (sem catálogo autoritativo) o agente só "anota" o que o cliente pede. Sem uma fonte da verdade, ele às vezes assume dosagens ou apresentações que não existem (ex.: "Dipirona 1g comprimido" quando só há 500mg comprimido / 1g em gotas).

**Como funciona**
Quando ATIVA, sempre que o cliente cita um medicamento por nome (com ou sem dosagem), antes de o item entrar na coleta o agente consulta a **bula oficial da ANVISA** para confirmar as apresentações reais. Aí:
- A apresentação que o cliente pediu existe → confirma e segue a coleta.
- A dosagem/forma não existe, mas o medicamento sim → oferece as apresentações reais e pergunta qual o cliente prefere.
- O nome não existe na bula → oferece uma alternativa pelo princípio ativo.

Itens claramente não-medicamento (fralda, soro, xampu, álcool, etc.) seguem direto pra coleta, sem consulta.

**Quando ativar**
- Você opera em pré-atendimento (sem integração de estoque) e quer evitar pedidos com dosagem/marca inventada.
- O agente `farmaceutico` está ATIVO no seu plano (necessário — é ele quem consulta a bula).

**Quando NÃO ativar (default)**
- Você já opera em modo ERP/normal (a validação de catálogo via `buscar_produto` já cobre isso).
- Você só tem o agente vendedor ativo (sem farmacêutico, o handoff não tem destino e a capability não faz efeito).
$md$,
 'Pedidos coletados com dosagem/apresentação reais da bula',
 'basic', '{}', '{}',
 '{}'::jsonb,
 '{}'::jsonb,
 FALSE, 'ga', 'pill', 55)
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
