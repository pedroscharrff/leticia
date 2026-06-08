-- 063_sentiment_analysis.sql
-- Capability: intelligence.sentiment_analysis
--
-- Liga um nó dedicado (sentiment_analyzer) que roda ANTES do orquestrador e
-- classifica o sentimento do cliente (positivo / neutro / negativo / frustrado
-- / irritado) usando o modelo leve (Haiku). O rótulo vira um bloco VOLÁTIL de
-- adaptação injetado no system_prompt de TODOS os skills (após o marker de
-- cache → nunca invalida o prefixo cacheado). Opcionalmente, frustração acima
-- de um limiar dispara o fluxo de transferência humana JÁ EXISTENTE (reusa o
-- flag `escalate` — não cria caminho de handoff novo).
--
-- Default OFF (opt-in): o tenant decide se quer o custo extra. Quando OFF, o nó
-- faz early-return e o turno é idêntico ao comportamento atual (zero custo, zero
-- latência). Quando ON, custo de ~1 chamada Haiku/turno (~100-200 tokens).
--
-- ⚠️ config_schema montado com jsonb_build_object (NUNCA jsonb_set aninhado —
-- footgun das migs 051/057, corrigido na 058). Cf. CLAUDE.md §6.

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('intelligence.sentiment_analysis',
 'Análise de sentimento do cliente',
 'inteligencia',
 'O agente percebe frustração/irritação na conversa e adapta o tom — e pode transferir para um humano quando o cliente está muito insatisfeito.',
 $md$**O problema**
Um cliente irritado tratado com o mesmo tom de sempre fica mais irritado. Sem perceber o sentimento, o agente não sabe quando suavizar, ser mais direto na solução ou chamar um humano.

**Como funciona**
Quando ATIVA, antes de cada resposta um classificador leve (Haiku) lê a mensagem do cliente + os últimos turnos e estima o sentimento (ex.: positivo, neutro, negativo, frustrado, irritado). Esse sinal é injetado no contexto do agente como uma orientação de adaptação — **o próprio agente** responde já no tom certo (mais empatia, mais concisão, foco em resolver). Não é um passo de reescrita: a resposta nasce adaptada, sem risco de alterar fatos clínicos.

**Escalonamento opcional**
Se você ligar `escalate_on_frustration`, quando o sentimento for de frustração/irritação acima do limiar de confiança, o turno entra no **mesmo fluxo de transferência humana** que você já usa (respeitando suas regras de handoff). O cliente ainda recebe uma resposta empática no mesmo turno.

**Parâmetros**
- **model** — modelo do classificador (custo).
- **labels** — rótulos que o classificador pode emitir.
- **analyst_instructions** — instruções extras suas para o classificador.
- **escalate_on_frustration / escalation_threshold / escalation_labels** — controle do escalonamento automático.
- **history_turns** — quantos turnos de histórico o classificador considera.

**Quando ativar**
- Atendimento com volume e clientes sensíveis (reclamações, atrasos de entrega, recompra).
- Você quer reduzir escalada de conflito e priorizar humanos nos casos quentes.

**Quando NÃO ativar**
- Operação simples/baixo volume onde o custo extra por turno não compensa.

**Custo extra**
~1 chamada Haiku por turno (~100-200 tokens). Bloco volátil — não afeta o prefixo cacheado do Anthropic. Zero custo quando a capability está OFF.
$md$,
 'Menos conflito, transferência inteligente nos casos quentes',
 'pro', '{}', '{}',
 jsonb_build_object(
   'type', 'object',
   'properties', jsonb_build_object(
     -- Campo composto provider|model: um único dropdown com pares VÁLIDOS
     -- garante que o operador nunca escolha um modelo de um provider em outro
     -- (ex.: gpt-4o-mini sob anthropic). Catálogo espelha llm/providers.py
     -- (canonical identifiers). Para adicionar modelo: edite o enum aqui E a
     -- constante em llm/providers.py.
     'provider_model', jsonb_build_object(
       'type', 'string',
       'title', 'Modelo do classificador',
       'description', 'Modelo de IA usado para classificar o sentimento. Cada opção já inclui o provedor — você não precisa escolher provider e modelo separadamente. Modelos menores (Haiku, mini, nano, flash) são mais baratos/rápidos e suficientes para classificação.',
       'enum', jsonb_build_array(
         'anthropic|claude-haiku-4-5-20251001',
         'anthropic|claude-sonnet-4-6',
         'openai|gpt-4o-mini',
         'openai|gpt-4.1-mini',
         'openai|gpt-4.1-nano',
         'openai|gpt-5-mini',
         'openai|gpt-5-nano',
         'google|gemini-2.0-flash',
         'ollama|llama3.2'
       ),
       'enumLabels', jsonb_build_object(
         'anthropic|claude-haiku-4-5-20251001', 'Anthropic — Claude Haiku 4.5 (recomendado)',
         'anthropic|claude-sonnet-4-6',         'Anthropic — Claude Sonnet 4.6 (mais caro)',
         'openai|gpt-4o-mini',                  'OpenAI — GPT-4o mini',
         'openai|gpt-4.1-mini',                 'OpenAI — GPT-4.1 mini',
         'openai|gpt-4.1-nano',                 'OpenAI — GPT-4.1 nano',
         'openai|gpt-5-mini',                   'OpenAI — GPT-5 mini',
         'openai|gpt-5-nano',                   'OpenAI — GPT-5 nano',
         'google|gemini-2.0-flash',             'Google — Gemini 2.0 Flash',
         'ollama|llama3.2',                     'Ollama (local) — Llama 3.2'
       ),
       'default', 'anthropic|claude-haiku-4-5-20251001'
     ),
     'labels', jsonb_build_object(
       'type', 'string',
       'format', 'textarea',
       'title', 'Rótulos de sentimento',
       'description', 'Lista separada por vírgula dos rótulos que o classificador pode emitir.',
       'default', 'positivo, neutro, negativo, frustrado, irritado'
     ),
     'analyst_instructions', jsonb_build_object(
       'type', 'string',
       'format', 'textarea',
       'title', 'Instruções extras para o classificador',
       'description', 'Orientações específicas da sua farmácia para o classificador (opcional).',
       'default', ''
     ),
     'escalate_on_frustration', jsonb_build_object(
       'type', 'boolean',
       'title', 'Transferir para humano quando frustrado',
       'description', 'Liga o escalonamento automático no fluxo de transferência existente.',
       'default', false
     ),
     'escalation_threshold', jsonb_build_object(
       'type', 'number',
       'title', 'Confiança mínima para escalar (0-1)',
       'default', 0.7,
       'minimum', 0,
       'maximum', 1
     ),
     'transfer_message', jsonb_build_object(
       'type', 'string',
       'format', 'textarea',
       'title', 'Mensagem de transferência quando o gatilho for sentimento',
       'description', 'Texto enviado ao cliente quando a transferência for disparada pela detecção de frustração/irritação. Deixe em branco para usar a mensagem padrão do tenant (`transfer_message` do handoff). Não afeta os outros gatilhos (escalonamento explícito do agente, palavra-chave, fechamento de pedido).',
       'default', ''
     ),
     'escalation_labels', jsonb_build_object(
       'type', 'string',
       'title', 'Rótulos que disparam escalonamento',
       'description', 'Lista separada por vírgula. Ex.: frustrado, irritado',
       'default', 'frustrado, irritado'
     ),
     'history_turns', jsonb_build_object(
       'type', 'integer',
       'title', 'Turnos de histórico considerados',
       'default', 3,
       'minimum', 1,
       'maximum', 6
     )
   )
 ),
 jsonb_build_object(
   'provider_model', 'anthropic|claude-haiku-4-5-20251001',
   'labels', 'positivo, neutro, negativo, frustrado, irritado',
   'analyst_instructions', '',
   'escalate_on_frustration', false,
   'escalation_threshold', 0.7,
   'escalation_labels', 'frustrado, irritado',
   'transfer_message', '',
   'history_turns', 3
 ),
 FALSE, 'beta', 'heart-pulse', 80)
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
