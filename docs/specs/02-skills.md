# SPEC 02 — Skills

**Propósito**: cada skill é uma especialidade do bot que responde a uma classe de intenções, com prompt próprio e (opcionalmente) tools.

## Onde vive

```
agents/
├── skills_registry.py  # FONTE ÚNICA: SkillDefinition + SKILLS (name, plan, allowed_handoffs, node_path)
├── runtime.py          # AgentRuntime: run_tool_loop compartilhado (tool-loop + flow signals + fallback)
├── prompts/
│   ├── builder.py      # PromptBuilder (montagem declarativa estável/volátil)
│   ├── flow.py         # instruções de handoff/escalate/end GERADAS do contrato das tools de fluxo
│   ├── commerce.py     # blocos de capability do vendedor (cross-sell, frete, PIX, memória)
│   └── clinical.py     # blocos do farmaceutico (stock_check em modo ERP)
├── tools/
│   └── flow_control.py # HandoffTool/EscalateTool/EndTool (SINAIS, não efeitos)
└── nodes/skills/
    ├── _base.py        # run_skill (wrapper do runtime), _persona_prefix, _build_messages, _parse_*
    ├── saudacao.py
    ├── farmaceutico.py # prompt modular + flow tools (handoff/end)
    ├── principio_ativo.py
    ├── genericos.py
    ├── vendedor.py     # bifurca normal/pré-atendimento; PromptBuilder + run_tool_loop + flow tools (force-call/draft no post_loop_hook)
    ├── recuperador.py
    └── guardrails.py
```

### Arquitetura de prompt + fluxo (2026-06, modularização)

**Metadados de skill (`skills_registry.py`)** — fonte ÚNICA. `_KNOWN_SKILLS`
(router), `_VALID_HANDOFF_TARGETS` (_base), `all_skill_nodes` (graph_builder) e
as descrições do orchestrator agora DERIVAM de `SKILLS`. Adicionar skill = editar
o registry (+ node file + migration de catálogo). `node_path` é resolvido
preguiçosamente (evita import circular registry→skill→_base→registry).

**Montagem de prompt (`prompts/PromptBuilder`)** — acaba com os `parts`/
`volatile_parts` à mão. `.core/.section/.flow/.extra_instructions` = ESTÁVEL
(prefixo cacheado); `.volatile` = por-turno (após o marker de cache). Blocos de
capability viraram funções puras em `commerce.py`/`clinical.py`. `_persona_prefix`
segue como porta ÚNICA de persona (chamada dentro de `.core`).

**Controle de fluxo HÍBRIDO (`tools/flow_control.py` + `prompts/flow.py`)** —
handoff/escalate/end são TOOLS (caminho primário): `target_skill` é `Literal`
derivado de `allowed_handoffs` → o LLM não roteia para destino inválido
(determinismo no schema). O `AgentRuntime` detecta a tool em `tool_calls`, seta
o sinal (`handoff_to`/`escalate`/`end_conversation`) e NÃO a executa. O parser de
marcadores (`_parse_*`) continua como REDE DE SEGURANÇA: se o LLM emitir `[[...]]`
em texto (ou prompt custom de tenant), captura. Tool vence; marcador é fallback;
**nunca falha estrito** (princípio 10.1). `flow.py` gera a instrução do prompt do
MESMO contrato das tools (fonte única — fim da divergência prompt↔parser).

**Runtime compartilhado (`runtime.run_tool_loop`)** — o tool-loop comum (antes
duplicado em `_base._invoke_with_tools` e no `vendedor.py` inline). `run_skill` é
um wrapper fino sobre ele. Hook `post_loop_hook` para lógica específica do skill
(force-call, draft-fallback). `_invoke_with_tools` foi REMOVIDO.

**Sticky ownership (determinístico)** — `state.current_owner` (persistido em
Redis, TTL da sessão, por `context.py`) faz o orchestrator pular a classificação
por LLM enquanto a conversa tem dono. Gated por `settings.sticky_ownership_enabled`
(default False). Emergência / pedido de humano nunca é interceptado
(`orchestrator._should_bypass_sticky`). Limpa em fim/escalation/pedido finalizado.

## Contrato do skill

Toda skill exporta uma função assíncrona:

```python
async def <skill_name>_node(state: AgentState, llm_factory) -> AgentState
```

Para skills **stateless sem tools complexas**, basta delegar pra `run_skill`:

```python
async def saudacao_node(state, llm_factory):
    return await run_skill(
        state=state,
        llm_factory=llm_factory,
        skill_name="saudacao",
        base_system=_SYSTEM,
        tools=None,  # ou lista de tools LangChain
    )
```

Para skills **complexos** (vendedor), implementar inline replicando o padrão de `_base.run_skill` mas com lógica extra (bifurcação, force-call, etc.).

## `run_skill` — o que ele faz

1. Monta `system_prompt` (estável):
   - `_persona_prefix(persona)` — bloco de identidade + regras de conversação
   - `build_customer_memory_block` (se capability `attendance.customer_memory` ON)
   - `skill_prompts[skill_name]` (override do tenant) OU `base_system` do skill
   - `skill_instructions[skill_name]` (extra instructions do tenant — acrescenta)
   - Bloco de "[CONTINUAÇÃO INTERNA]" se este skill está recebendo handoff
2. Constrói `messages` via `_build_messages` (system com cache_control + histórico + current_message)
3. Invoca LLM:
   - **Sem tools**: `llm.ainvoke(messages)` com `llm_retry`
   - **Com tools**: loop `_invoke_with_tools` até `max_iters` (`settings.skill_max_tool_iterations`, default 5)
4. `_parse_handoff` — extrai `[[HANDOFF:X:ctx]]`, limpa do texto
5. Se recebendo handoff: concatena `prev_response + final_response`
6. Trace step + retorna state com `final_response, handoff_to, handoff_context, handoff_count, skill_history, selected_skill`

## Invariantes globais (todos os skills)

1. **Persona única externa**: bot nunca diz "vou te passar pro farmacêutico/vendedor". Cliente vê uma pessoa só. Garantido em `_persona_prefix`.
2. **Resposta curta**: máx 3-4 frases, 1 pergunta por vez. Garantido em prompt + `analyst`.
3. **Marker invisível**: `[[HANDOFF:X]]`, `[[ESCALATE]]`, `[[END]]` SEMPRE removidos do texto antes do cliente ver (regex em `_parse_handoff` / `_parse_escalate` / `_parse_end`). `[[END]]` sinaliza fim de atendimento (cliente se despediu sem pedido pendente) → propaga `end_conversation=True` no state; o worker chama `end_session` (closed_at, sem pausar, limpa histórico). Não propaga quando há handoff/escalate/balcão (essas têm prioridade e já finalizam).
4. **Nunca alucinar tool result**: skill não pode afirmar "pedido confirmado" / "anotado" / "no estoque" sem chamar a tool no mesmo turno. Trava no prompt + (no vendedor) force-call.
5. **Retry-safe**: se LLM falhar, gera fallback amigável em vez de quebrar o grafo.
6. **Tools via factory**: tools recebem `schema_name`, `tenant_id`, `cart`, `customer` via closure. Mutações em `cart` precisam refletir no `AgentState` (passar a mesma ref).

## Skills atuais

### `saudacao` — Recepção
Plano: basic.
Tools: nenhuma.
Quando: primeiro contato, "oi", mensagem ambígua.
Característica: skip de LLM via fast-path em `orchestrator._is_pure_greeting` (saudação pura + sem histórico).

### `farmaceutico` — Orientação clínica
Plano: basic.
Tools: `consultar_bula`, `consultar_bula_secao`. **Em modo ERP** (capability `inventory.track_stock` ON) também recebe `buscar_produto` para conferir o catálogo ANTES de citar nome comercial — bloco extra `_STOCK_CHECK_BLOCK` é anexado ao `_SYSTEM` nesse modo. Em pré-atendimento, comportamento histórico (sem catálogo) é preservado. **Guard-rail "não achou na bula":** quando `sales.pharmacist_validation` está ON, `make_consultar_bula_tool` recebe `not_found_message` (do config da capability, editável por tenant — mig 056); aí o "nenhum registro" da bula retorna uma instrução determinística mandando o agente NÃO inventar dosagem/apresentação e perguntar ao cliente com aquela frase. Default em `_DEFAULT_NOT_FOUND_MESSAGE`. (O safety_guard da SPEC 10 NÃO cobre isso — é passthrough em pré-atendimento; por isso o guard vive na própria tool.)
Quando: sintoma, dúvida farmacêutica, posologia, interações.
Característica: **handoff obrigatório para vendedor** quando cliente sinaliza finalização (`pode finalizar`, `pode mandar`...). Skill não pode "confirmar pedido" — não tem tool para isso. Bônus do `buscar_produto`: popula `cart._search_results_this_turn`, então `availability_guard` (SPEC 10) passa a cobrir alucinação de produto em recomendação clínica também.
Característica (pré-atendimento): também recebe **handoff de validação** do vendedor quando o cliente cita medicamento por nome (com ou sem dosagem) — **somente se a capability `sales.pharmacist_validation` estiver ON e o farmacêutico estiver ativo no tenant**. Roteiro fixo do bloco "RECEBENDO HANDOFF DE VALIDAÇÃO DO VENDEDOR" no `_SYSTEM`: é **single-hop** — chama `consultar_bula(nome base)` e RESPONDE DIRETO AO CLIENTE (não volta pro vendedor; o anti-loop de `run_skill` descartaria o handoff de retorno): (a) apresentação confere → confirma e segue a coleta, (b) dosagem/forma não existe → oferece as apresentações reais e pergunta qual prefere, (c) nome não existe na bula → oferece alternativa por princípio ativo. O registro real só acontece no fechamento (`anotar_pedido_balcao`), lendo o nome já validado do histórico. Evita anotar dosagens/marcas inventadas sem inflar o prompt do vendedor.

### `vendedor` — Vendas
Plano: pro.
Tools (modo normal): `buscar_produto`, `adicionar_ao_carrinho`, `remover_do_carrinho`, `atualizar_qtd_carrinho`, `finalizar_pedido`, `salvar_dados_cliente`, `consultar_pedido`, `cancelar_pedido`, `editar_pedido`. Tools extras condicionadas: `recomendar_complementos`, `calcular_frete`, `gerar_link_pix`, `registrar_alergia`, `registrar_medicamento_continuo`, `registrar_preferencia`.
Tools (modo pré-atendimento): `salvar_dados_cliente`, `consultar_pedido`, `registrar_itens_interesse`, `anotar_pedido_balcao`.
Quando: cliente quer comprar, preço, montar carrinho.
Característica: **bifurca em modo normal vs pré-atendimento** baseado em capability `sales.stock_check`. Tem force-call no pré-atendimento (se LLM "fechou" sem chamar `anotar_pedido_balcao`, força a chamada). **Desde a modularização (2026-06):** vendedor monta prompt via `PromptBuilder` (+`prompts/commerce.py`), roda o loop via `runtime.run_tool_loop`, e o force-call + draft-fallback vivem no `post_loop_hook` passado ao runtime — NÃO foram removidos, só mudaram de lugar. Controle de fluxo é por tools (handoff/escalate/end), com o parser de marcadores como fallback. Handoff no pré-atendimento: a tool só é bindada (destino `farmaceutico`) quando `sales.pharmacist_validation` ON E farmacêutico ativo.
Característica (pré-atendimento): a regra 4 do `_SYSTEM_PRE_ATENDIMENTO` exige `[[HANDOFF:farmaceutico:nome]]` quando o cliente cita medicamento por nome — farmacêutico valida na bula antes de anotar. Itens claramente não-medicamento (fralda/xampu/álcool/etc.) seguem direto pra coleta. **Esse handoff só roteia quando a capability `sales.pharmacist_validation` está ON E o farmacêutico está em `available_skills`** — caso contrário o marcador é só limpo do texto (comportamento histórico, sem validação). O gate fica no fim do `vendedor_node` (parse de handoff do pré-atendimento). **Trigger principal:** quando a cap está ON, o próprio `orchestrator` roteia medicamento nomeado → `farmaceutico` (override da regra 2), porque o orquestrador já classifica "dipirona" como medicamento de forma confiável e o farmacêutico tem `consultar_bula` (cobre ANVISA inteira, não só o catálogo local). O handoff `vendedor→farmaceutico` permanece como backstop. Isso evita inchar o prompt do vendedor com lógica de validação.
Característica: `anotar_pedido_balcao` **popula o cart in-place** (`items`, `subtotal=0`, `last_order`, `just_finalized=True`) — assim o `send_order_summary` do worker, disparado pelo `escalate=True`, consegue montar o resumo do pedido (capability `sales.order_summary_after_handoff`, mig 044). Sem essa mutação, o resumo sai vazio em pré-atendimento. **Ela também INSERE em `orders`** — por isso não se "desfaz" um registro prematuro; o controle é PRÉ-execução (ver gate abaixo).
Característica (anti-esquecimento de item — só `needs_tool_scaffolding`): (1) `registrar_itens_interesse` recebe `merge=True` → **acumula por nome** (upsert) em vez de SUBSTITUIR, porque o modelo fraco chama a tool com só o item NOVO e apagaria os anteriores (`qty<=0` remove). Modelo forte mantém REPLACE (suporta remover por omissão). (2) o `vendedor_node` renderiza o **rascunho atual do pedido** como bloco VOLÁTIL no pré-atendimento (antes o cart NÃO era mostrado ao modelo, diferente do modo normal — ele dependia só do histórico e esquecia itens). `anotar_pedido_balcao` continua registrando a lista que o modelo passa (= a confirmada no gate); NÃO unimos com o cart pra não registrar item que o cliente não viu na confirmação.
Característica (gate determinístico de confirmação — só `needs_tool_scaffolding`, ex. Gemini): modelo fraco lê um "Sim" de confirmação de dado (dosagem) como "fechar" e anota o pedido cedo, disparando o transfer do worker. O `domain_tool_gate` do `run_tool_loop` (closure `_order_gate` no vendedor) faz **two-phase commit** em `anotar_pedido_balcao`: a 1ª chamada com uma lista de itens é VETADA (vira instrução "liste o pedido completo e pergunte 'Posso fechar?'") e a lista é gravada num snapshot em `order_confirm:{tenant}:{phone}` (Redis, TTL 15min); só a 2ª chamada num turno POSTERIOR com a MESMA lista executa (cliente confirmou). Lista diferente → re-veta (auto-corretivo). Um set local por-turno impede "confirmar" no mesmo turno. O **force-call** do `post_loop_hook` também passa pelo gate (não pode furá-lo). Falha de Redis = falha aberta. Claude/forte: gate desligado → caminho histórico intacto. Cf. SPEC 08 §gate.
Característica (recuperação de carrinho no pré-atendimento): `registrar_itens_interesse` (Etapa 2 do `_SYSTEM_PRE_ATENDIMENTO`) grava a lista de interesse no `cart` in-place **sem** `just_finalized` — o `save_context` então persiste uma linha em `{schema}.cart` com `items>0` e `stock_mode='balcao'`. **Esse é o único caminho que torna o carrinho de pré-atendimento recuperável**: sem ele, o cliente que some antes de confirmar nunca gera linha de cart (a única outra tool que escreve itens, `anotar_pedido_balcao`, é terminal e limpa via `just_finalized`). **Invariante a não quebrar:** `registrar_itens_interesse` NÃO finaliza/transfere e NÃO seta `just_finalized`; `anotar_pedido_balcao` continua terminal e limpa o cart. O job `recover_abandoned_carts` e a página de Recuperação funcionam sem alteração (filtram por `items>0`, sem filtro de modo). O envio só ocorre se a capability `sales.abandoned_cart` estiver ON.
Característica (**fallback determinístico de rascunho**, 2026-06-10): o LLM frequentemente ignora `registrar_itens_interesse` e lista itens só em texto — o cart fica `items=[]` até `anotar_pedido_balcao`, e se o cliente some antes, a recuperação falha. **Fix em 3 camadas:** (1) **Regra 6** no `_SYSTEM_PRE_ATENDIMENTO` promove a instrução a regra absoluta; (2) **extração determinística** — no fim do turno, se `registrar_itens_interesse` e `anotar_pedido_balcao` não foram chamados mas a resposta enumera itens (heurística `_detect_item_listing`), uma chamada Haiku com schema forçado extrai `[{name,qty}]` do diálogo e grava em `cart["items"]` diretamente (sem `just_finalized`, sem `orders`) — o `save_context` persiste normalmente; (3) **métrica `preattend_draft_fallback_total`** (Counter Prometheus, label `tenant_id`) + log `vendedor.draft.extracted_by_fallback`. **Invariantes:** a extração NÃO seta `just_finalized`, NÃO cria order, NÃO altera a resposta ao cliente — é rascunho puro equivalente a `registrar_itens_interesse`. Custo: ~1 chamada Haiku só nos turnos em que o LLM falhou, só em pré-atendimento.

### `principio_ativo` — Substância ativa
Plano: pro.
Tools: nenhuma (responde com base em conhecimento + bula via prompt).
Quando: "qual o princípio ativo de X?", "X contém Y?".

### `genericos` — Alternativas genéricas
Plano: pro.
Tools: nenhuma (orientação genérica).
Quando: "tem genérico de X?", "mais barato similar?".

### `recuperador` — Reengajamento
Plano: enterprise.
Tools: nenhuma direta.
Quando: cliente inativo voltou; também chamado por jobs proativos (abandoned_cart, refill_nudge) com contexto pré-montado.

### `guardrails` — Segurança
Plano: sempre disponível.
Tools: nenhuma.
Quando: off-topic, emergência médica, conteúdo impróprio.
Característica: **fast-path keyword** (palavras como `infarto`, `overdose`, `samu`...) → resposta hardcoded com SAMU/Bombeiros + `escalate=True` SEM chamar LLM (latência crítica).

## Persona — campos suportados

A persona é carregada em `agents/nodes/context.py::load_context` via `SELECT * FROM public.tenant_persona WHERE tenant_id = $1` (não listamos colunas — adicionou coluna na tabela, ela chega ao agente automaticamente).

A renderização no prompt acontece em **uma única função**: `agents/nodes/skills/_base.py::_persona_prefix`. Todos os skills (incluindo `vendedor.py`, que tem fluxo próprio) passam por ela.

**Regra de ouro**: campo salvo em `tenant_persona` que não é lido em `_persona_prefix` = config **fantasma** — operador edita no portal e nada muda no comportamento. Toda coluna nova precisa ter render correspondente.

Campos atualmente renderizados (estável → vão no prefixo cacheado):

| Campo | Onde aparece no prompt |
|---|---|
| `agent_name`, `pharmacy_name` | Linha de abertura ("Você é X, atendente da Y.") |
| `pharmacy_tagline` | "Slogan da farmácia: ..." |
| `persona_bio` | Bloco livre logo após identidade |
| `tone`, `language` | Linha de estilo |
| `agent_gender` | "Use concordância de gênero ... ao se referir a si" |
| `formality` (`tu`/`voce`/`senhor`) | "Trate o cliente por ..." |
| `emoji_usage` (`none`/`light`/`moderate`/`heavy`) | Regra de uso de emoji |
| `response_length` (`short`/`medium`/`long`) | Tamanho preferido |
| `vocabulary_level` (`leigo`/`intermediario`/`tecnico`) | Registro de vocabulário (técnico↔leigo) — linha de estilo (mig 064) |
| `explanation_depth` (`minima`/`equilibrada`/`detalhada`) | Profundidade da explicação — linha de estilo (mig 064) |
| `catchphrases` (list[str]) | "Bordões da marca (use com moderação): ..." |
| `greeting_template` | "Saudação preferida (use no PRIMEIRO contato): ..." |
| `signature` | **NÃO renderizado no prompt** — anexado deterministicamente à resposta em `save_context` (ver §Assinatura determinística). |
| `business_hours`, `location`, `delivery_info`, `payment_methods`, `website`, `instagram` | Bloco "Contexto da farmácia" (lista) |
| `forbidden_topics` | Bloco "TÓPICOS PROIBIDOS — NÃO comente..." |
| `conversation_playbook` | Bloco "PLAYBOOK DE ATENDIMENTO" |
| `custom_instructions` | "Instruções extras do dono da farmácia: ..." |

Para adicionar um campo novo de persona: migration adiciona a coluna em `public.tenant_persona` + entrada no `PERSONA_DEFAULTS` (`services/persona.py`) + render em `_persona_prefix`. Sem o render no `_persona_prefix`, o campo é só decoração no portal.

### Assinatura determinística

A `signature` da persona (`/portal/persona`) **não vai no prompt da LLM**. Historicamente era injetada como instrução *"Assinatura opcional (no fim de respostas longas)"* — mole por construção: a LLM decidia se assinava e a regra de brevidade (respostas de 3-4 frases) fazia quase nenhuma resposta qualificar como "longa", então a assinatura praticamente nunca saía (pior ainda em LLM fraca, ver SPEC 08 §tier).

Agora ela é colada **deterministicamente** no fim da resposta, em `save_context` (`agents/nodes/context.py::_apply_signature`), depois da LLM:
- **Escopo:** SÓ respostas normais. Pula `escalate` (transferência humana), `end_conversation` (encerramento), `handoff_to` e respostas vazias — não se assina "vou te transferir" nem resumo pós-handoff.
- **Idempotente:** se o texto já termina com a assinatura (mímica do histórico), não duplica.
- **Histórico do Redis fica SEM assinatura** (grava `final_response` puro), para a LLM não ver exemplos assinados e passar a assiná-los sozinha (geraria duplicata). O `outbound` assinado vai só para `conversation_logs` (auditoria do que foi enviado) e para o `final_response` retornado (o worker envia esse).

### Precedência de voz (persona × prompt do skill)

`_persona_prefix` emite, ao final do bloco de estilo, uma linha de **PRECEDÊNCIA DE ESTILO**: tom, registro, vocabulário, emojis e tamanho definidos na persona são prioritários sobre qualquer instrução de estilo nos `_SYSTEM` dos skills. Os prompts de skill tratam de **conteúdo/conduta** (regras clínicas, tools, handoff), não de COMO soar. Isso evita que o estilo hardcoded de um skill (ex.: `farmaceutico`) dilua os ajustes do dono. Não remova essa linha em refactors.

### Bloco volátil de sentimento

Quando a capability `intelligence.sentiment_analysis` está ON, o nó `sentiment_analyzer` (roda antes do orchestrator) grava `state["sentiment_directive"]`. `run_skill` injeta esse texto em `volatile_parts` (após o marker de cache → **não invalida o prefixo**), de modo que a resposta do próprio skill já nasce adaptada ao humor do cliente. Vazio quando a capability está OFF. Ver SPEC 04 e SPEC 01.

## Pontos de extensão

### Adicionar novo skill

1. `agents/nodes/skills/<nome>.py` — implementa `<nome>_node`. Use `run_skill` se possível (passe `enable_handoff/escalate/end` se o skill faz controle de fluxo).
2. Constante `_SYSTEM` no topo com o prompt base (sem instruções de marcador — o `.flow()` do PromptBuilder gera isso a partir das tools).
3. **`agents/skills_registry.py`: adicionar uma `SkillDefinition` em `SKILLS`** (name, plan_min, description, node_path, allowed_handoffs, capabilities). É a ÚNICA edição de "registro" — `_KNOWN_SKILLS`, `_VALID_HANDOFF_TARGETS`, `all_skill_nodes` e as descrições do orchestrator derivam daqui automaticamente.
4. Migration: INSERT em `public.skill_catalog` (skill_name, display_name, plan_min, channel_compat, tools_json, default_provider/llm).
5. Frontend `PortalSkills.tsx` mostra automaticamente (lê do catálogo).

### Prompt customizado por tenant

Via portal `PortalPersona.tsx` → `tenant_skill_prompts`:
- `system_prompt` SUBSTITUI o `_SYSTEM` do skill (`skill_prompts[skill_name]` em `run_skill`)
- `extra_instructions` ACRESCENTA ao prompt em uso (`skill_instructions[skill_name]`)

### Override de LLM por skill

Via portal `PortalLLMConfig.tsx` → `SkillOverride` (campos `llm_model`, `llm_provider`, `prompt_version`, `config_json`). Aplicado em `_make_llm_factory` quando `llm_factory(<skill_name>)` é chamado.

### Adicionar tool a um skill

1. Criar/atualizar tool em `agents/tools/<arquivo>.py` (ver SPEC 03).
2. No skill: `tools=[..., make_minha_tool(schema_name, ...)]`.
3. Atualizar prompt do skill explicando quando usar a tool (LLM lê o docstring + sua descrição no prompt).

## Regressões conhecidas / "Não fazer"

- **Não jogar bloco de continuação de handoff (`[CONTINUAÇÃO INTERNA]`) dentro do `system_prompt` estável** em `run_skill`. Ele depende do skill anterior + texto da resposta dele → muda a cada handoff e invalida o cache. Vai em `volatile_parts` → `_build_messages(..., volatile_prompt=...)`. Já foi bug em prod: farmaceutico→vendedor (caminho mais comum) pagava input full toda vez. Mesma regra vale pro `vendedor.py` (cart, sales_config_block, address_hint, customer_memory → volátil).
- **Não adicionar coluna em `public.tenant_persona` sem renderizar em `_persona_prefix`** (OU tratar deterministicamente fora do prompt, como a `signature`). Os usuários editam no portal e acham que está aplicado, mas o agente nunca lê. Já tomamos esse golpe: `formality`, `emoji_usage`, `greeting_template`, `business_hours`, `location`, `delivery_info`, `payment_methods`, `website`, `instagram`, `catchphrases`, `forbidden_topics`, `agent_gender`, `pharmacy_tagline` eram salvos no DB mas IGNORADOS no prompt (corrigido em 2026-05-31). **Exceção: `signature` — de propósito NÃO vai no prompt; é anexada deterministicamente em `save_context` (ver §Assinatura determinística). Não a "conserte" de volta pro prompt.**
- **Não emitir `[[HANDOFF:...]]` SEM ter respondido nada antes** (no farmaceutico, na situação de "cliente confirmou pedido"). Quebra a regra de "um único output por turno". Excepção: pedido fechado, onde só o marker está OK (vendedor concatena depois).
- **Não chamar tools fora do `for i in range(max_iters)`**. Ultrapassa o limite e o agente fica preso em loop infinito.
- **Não esquecer de limpar `[[HANDOFF]]` em modo pré-atendimento do vendedor**. Mesmo sem rotear, o marker aparece pro cliente se não rodar `_parse_handoff`.
- **Não retornar `cart` diferente do recebido por referência**. Tools mutam `cart` in-place; se você criar um dict novo, `save_context` salva o antigo e a mutação some.
- **Não esquecer de incluir `messages.append(response)` antes da iteração com tool_calls**. O LLM precisa ver suas próprias tool_calls no histórico para encadear.
- **Não adicionar `selected_skill` errado no return**. Convenção: `handoff_target or skill_name` (skill que efetivamente respondeu).
- **Não remover o fallback determinístico de rascunho** (`_detect_item_listing` + `_extract_items_from_dialog`). O LLM ignora `registrar_itens_interesse` na maioria dos turnos de pré-atendimento — sem o fallback, carrinhos ficam `items=[]` e a recuperação fica vazia. Se mexer na heurística, verificar métrica `preattend_draft_fallback_total` para taxa de acionamento.
- **Não setar `just_finalized` na extração do fallback.** Ela é rascunho puro — só `anotar_pedido_balcao` fecha.
- **Não jogar bloco volátil no `.section()` do PromptBuilder** (é estável/cacheado). Carrinho, memória, handoff, sentimento → `.volatile()`. Mesma regra de ouro do caching de sempre, agora explícita na API.
- **Não remover o parser de marcadores (`_parse_*`) nem teimar em "tool-only estrito".** É a rede de segurança do controle de fluxo híbrido. Se o LLM não chamar a tool de fluxo, o marcador (ou simplesmente continuar o atendimento) é o fallback — nunca abandonar o cliente (princípio 10.1). O `gemini.md` pedia "falhar estrito"; foi deliberadamente rejeitado.
- **Não ensinar marcador `[[...]]` E tool de fluxo no mesmo prompt.** Confunde o LLM. O prompt do skill ensina a TOOL (via `.flow()`); o parser de marcador existe só para capturar stragglers/prompts custom de tenant.
- **Não duplicar metadados de skill fora do `skills_registry.py`.** `_KNOWN_SKILLS`/`_VALID_HANDOFF_TARGETS`/`all_skill_nodes`/descrições do orchestrator DERIVAM do registry. Editar um set hardcoded = drift de volta ao problema que o registry resolveu.
- **Não setar `current_owner` para skill fora de `available_skills`** nem deixar de limpá-lo em fim/escalation/pedido finalizado (senão o sticky prende a conversa). A limpeza vive em `context.save_context`.
