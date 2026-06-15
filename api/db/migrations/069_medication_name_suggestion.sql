-- ═══════════════════════════════════════════════════════════════════════════
-- 069_medication_name_suggestion.sql
--
-- Recurso "Você quis dizer…?": quando o cliente escreve o nome de um
-- medicamento com erro de digitação forte (que o trigram do bulário, corte
-- 0.45, e a própria ANVISA não casam), o agente passa a OFERECER candidatos
-- de correção em vez de só dizer "não encontrei".
--
-- Esta migration só registra a CAPABILITY. Não cria tabela: a camada 1
-- (fuzzy determinístico) consulta AO VIVO as bases de nomes reais que já
-- existem — public.medicamentos_anvisa (bulário, ~150 princípios backfillados
-- com suas marcas) e public.medicamentos_referencia (guia de genéricos) —,
-- ambas já com índice GIN trigram. Roda só no caminho "não encontrei" (frio),
-- então o custo é irrelevante e os nomes ficam sempre frescos conforme o
-- bulário cresce. Evita ingerir o CSV de PRODUTOS/correlatos da ANVISA
-- (dados_medicamentos.csv), que é registro de dispositivos (agulhas,
-- preservativos, reagentes) e não de medicamentos — poluiria as sugestões.
--
-- Pipeline (no serviço services/medicamento_suggest.py):
--   camada 1  fuzzy nas bases reais (determinístico)
--   camada 2  Haiku normaliza a grafia torta            } verificadas contra
--   camada 3  web search nativo Anthropic (opcional)    } a ANVISA antes de sugerir
--
-- Capability ON por default (é seguro: só SUGERE, nunca substitui o nome
-- sozinho; o agente sempre pergunta e espera confirmação do cliente).
--
-- ⚠️ config_schema montado com jsonb_build_object (NUNCA jsonb_set aninhado —
-- footgun das migs 051/057, corrigido na 058). Cf. CLAUDE.md §6.
-- ═══════════════════════════════════════════════════════════════════════════

-- ── Capability ──────────────────────────────────────────────────────────────
INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('attendance.medication_name_suggestion',
 'Sugestão de nome de medicamento ("Você quis dizer…?")',
 'atendimento',
 'Quando o cliente digita o nome de um remédio com erro, o agente sugere o nome correto em vez de só dizer que não encontrou.',
 $md$**O problema**
Nome de medicamento é difícil de escrever. No WhatsApp o cliente manda "rivotrio", "buscopam", "neimosulida" — e o sistema, que casa nomes por similaridade com um corte conservador (pra não confundir um remédio com outro), simplesmente não acha. O cliente recebe "não encontrei" e a conversa trava.

**Como funciona**
Quando ATIVA e o medicamento não é localizado, o agente roda um corretor em camadas:
1. **Dicionário oficial** — busca por similaridade num dicionário com mais de 100 mil nomes de produtos registrados na ANVISA (comercial e técnico). Resolve a maioria dos erros de digitação. É determinístico, instantâneo, sem custo.
2. **Correção por IA** — para grafias muito distorcidas, um modelo leve (Haiku) propõe o nome provável.
3. **Busca na web (opcional)** — como último recurso, o agente consulta a web para identificar o medicamento que o cliente quis dizer.

Os candidatos das camadas 2 e 3 são **sempre verificados contra a base da ANVISA** antes de chegarem ao cliente — o agente nunca inventa nem confia cegamente na grafia da IA/web.

**Segurança em primeiro lugar**
O agente **nunca troca o nome sozinho**. Ele pergunta — "Você quis dizer Rivotril?" — e só segue depois que o cliente confirma. Medicamento errado é risco; por isso a confirmação é obrigatória.

**Parâmetros**
- **enable_web_search** — liga/desliga a camada 3 (busca web). Desligada, o recurso usa só dicionário + IA (mais barato e rápido).
- **max_candidates** — quantas sugestões oferecer (1 a 5).

**Quando ativar**
- Praticamente sempre — o ganho de não perder uma venda por causa de um typo é grande e o recurso é seguro por construção.

**Quando NÃO ativar**
- Operação onde todo nome já chega correto (raro no WhatsApp).

**Custo extra**
Zero quando o nome é encontrado normalmente (camadas 2/3 só rodam no "não encontrei"). No fallback: ~1 chamada Haiku (curta) por correção, mais o custo da busca web quando habilitada.
$md$,
 'Menos "não encontrei", mais pedidos concluídos',
 'basic', '{}', '{}',
 jsonb_build_object(
   'type', 'object',
   'properties', jsonb_build_object(
     'enable_web_search', jsonb_build_object(
       'type', 'boolean',
       'title', 'Usar busca na web no último recurso',
       'description', 'Quando o dicionário e a correção por IA não resolvem, consulta a web para identificar o medicamento. Mais cobertura para grafias muito erradas, com um pouco mais de latência/custo. O nome encontrado é sempre verificado na ANVISA antes de ser sugerido.',
       'default', true
     ),
     'max_candidates', jsonb_build_object(
       'type', 'integer',
       'title', 'Máximo de sugestões por vez',
       'description', 'Quantos nomes alternativos o agente pode oferecer ("Você quis dizer A, B ou C?").',
       'default', 3,
       'minimum', 1,
       'maximum', 5
     )
   )
 ),
 jsonb_build_object(
   'enable_web_search', true,
   'max_candidates', 3
 ),
 TRUE, 'beta', 'spell-check', 86)
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
