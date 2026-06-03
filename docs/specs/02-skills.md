# SPEC 02 — Skills

**Propósito**: cada skill é uma especialidade do bot que responde a uma classe de intenções, com prompt próprio e (opcionalmente) tools.

## Onde vive

```
agents/nodes/skills/
├── _base.py            # run_skill, _persona_prefix, _build_messages, _parse_handoff, _parse_escalate
├── saudacao.py
├── farmaceutico.py
├── principio_ativo.py
├── genericos.py
├── vendedor.py         # bifurca em normal vs pré-atendimento
├── recuperador.py
└── guardrails.py
```

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
Tools: `consultar_bula`, `consultar_bula_secao`. **Em modo ERP** (capability `inventory.track_stock` ON) também recebe `buscar_produto` para conferir o catálogo ANTES de citar nome comercial — bloco extra `_STOCK_CHECK_BLOCK` é anexado ao `_SYSTEM` nesse modo. Em pré-atendimento, comportamento histórico (sem catálogo) é preservado.
Quando: sintoma, dúvida farmacêutica, posologia, interações.
Característica: **handoff obrigatório para vendedor** quando cliente sinaliza finalização (`pode finalizar`, `pode mandar`...). Skill não pode "confirmar pedido" — não tem tool para isso. Bônus do `buscar_produto`: popula `cart._search_results_this_turn`, então `availability_guard` (SPEC 10) passa a cobrir alucinação de produto em recomendação clínica também.
Característica (pré-atendimento): também recebe **handoff de validação** do vendedor quando o cliente cita medicamento por nome (com ou sem dosagem) — **somente se a capability `sales.pharmacist_validation` estiver ON e o farmacêutico estiver ativo no tenant**. Roteiro fixo do bloco "RECEBENDO HANDOFF DE VALIDAÇÃO DO VENDEDOR" no `_SYSTEM`: é **single-hop** — chama `consultar_bula(nome base)` e RESPONDE DIRETO AO CLIENTE (não volta pro vendedor; o anti-loop de `run_skill` descartaria o handoff de retorno): (a) apresentação confere → confirma e segue a coleta, (b) dosagem/forma não existe → oferece as apresentações reais e pergunta qual prefere, (c) nome não existe na bula → oferece alternativa por princípio ativo. O registro real só acontece no fechamento (`anotar_pedido_balcao`), lendo o nome já validado do histórico. Evita anotar dosagens/marcas inventadas sem inflar o prompt do vendedor.

### `vendedor` — Vendas
Plano: pro.
Tools (modo normal): `buscar_produto`, `adicionar_ao_carrinho`, `remover_do_carrinho`, `atualizar_qtd_carrinho`, `finalizar_pedido`, `salvar_dados_cliente`, `consultar_pedido`, `cancelar_pedido`, `editar_pedido`. Tools extras condicionadas: `recomendar_complementos`, `calcular_frete`, `gerar_link_pix`, `registrar_alergia`, `registrar_medicamento_continuo`, `registrar_preferencia`.
Tools (modo pré-atendimento): `salvar_dados_cliente`, `consultar_pedido`, `anotar_pedido_balcao`.
Quando: cliente quer comprar, preço, montar carrinho.
Característica: **bifurca em modo normal vs pré-atendimento** baseado em capability `sales.stock_check`. Tem force-call no pré-atendimento (se LLM "fechou" sem chamar `anotar_pedido_balcao`, força a chamada).
Característica (pré-atendimento): a regra 4 do `_SYSTEM_PRE_ATENDIMENTO` exige `[[HANDOFF:farmaceutico:nome]]` quando o cliente cita medicamento por nome — farmacêutico valida na bula antes de anotar. Itens claramente não-medicamento (fralda/xampu/álcool/etc.) seguem direto pra coleta. **Esse handoff só roteia quando a capability `sales.pharmacist_validation` está ON E o farmacêutico está em `available_skills`** — caso contrário o marcador é só limpo do texto (comportamento histórico, sem validação). O gate fica no fim do `vendedor_node` (parse de handoff do pré-atendimento). Isso evita inchar o prompt do vendedor com lógica de validação.
Característica: `anotar_pedido_balcao` **popula o cart in-place** (`items`, `subtotal=0`, `last_order`, `just_finalized=True`) — assim o `send_order_summary` do worker, disparado pelo `escalate=True`, consegue montar o resumo do pedido (capability `sales.order_summary_after_handoff`, mig 044). Sem essa mutação, o resumo sai vazio em pré-atendimento.

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
| `catchphrases` (list[str]) | "Bordões da marca (use com moderação): ..." |
| `greeting_template` | "Saudação preferida (use no PRIMEIRO contato): ..." |
| `signature` | "Assinatura opcional (no fim de respostas longas): ..." |
| `business_hours`, `location`, `delivery_info`, `payment_methods`, `website`, `instagram` | Bloco "Contexto da farmácia" (lista) |
| `forbidden_topics` | Bloco "TÓPICOS PROIBIDOS — NÃO comente..." |
| `conversation_playbook` | Bloco "PLAYBOOK DE ATENDIMENTO" |
| `custom_instructions` | "Instruções extras do dono da farmácia: ..." |

Para adicionar um campo novo de persona: migration adiciona a coluna em `public.tenant_persona` + entrada no `PERSONA_DEFAULTS` (`services/persona.py`) + render em `_persona_prefix`. Sem o render no `_persona_prefix`, o campo é só decoração no portal.

## Pontos de extensão

### Adicionar novo skill

1. `agents/nodes/skills/<nome>.py` — implementa `<nome>_node`. Use `run_skill` se possível.
2. Constante `_SYSTEM` no topo com o prompt base.
3. Em `agents/router.py`: adicionar em `_KNOWN_SKILLS` e em `_VALID_HANDOFF_TARGETS` no `_base.py`.
4. Em `agents/graph_builder.py`: import + bind via partial + entrada em `all_skill_nodes`.
5. Migration: INSERT em `public.skill_catalog` (skill_name, display_name, plan_min, channel_compat, tools_json, default_provider/llm).
6. Frontend `PortalSkills.tsx` mostra automaticamente (lê do catálogo).

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
- **Não adicionar coluna em `public.tenant_persona` sem renderizar em `_persona_prefix`.** Os usuários editam no portal e acham que está aplicado, mas o agente nunca lê. Já tomamos esse golpe: `formality`, `emoji_usage`, `greeting_template`, `signature`, `business_hours`, `location`, `delivery_info`, `payment_methods`, `website`, `instagram`, `catchphrases`, `forbidden_topics`, `agent_gender`, `pharmacy_tagline` eram salvos no DB mas IGNORADOS no prompt (corrigido em 2026-05-31).
- **Não emitir `[[HANDOFF:...]]` SEM ter respondido nada antes** (no farmaceutico, na situação de "cliente confirmou pedido"). Quebra a regra de "um único output por turno". Excepção: pedido fechado, onde só o marker está OK (vendedor concatena depois).
- **Não chamar tools fora do `for i in range(max_iters)`**. Ultrapassa o limite e o agente fica preso em loop infinito.
- **Não esquecer de limpar `[[HANDOFF]]` em modo pré-atendimento do vendedor**. Mesmo sem rotear, o marker aparece pro cliente se não rodar `_parse_handoff`.
- **Não retornar `cart` diferente do recebido por referência**. Tools mutam `cart` in-place; se você criar um dict novo, `save_context` salva o antigo e a mutação some.
- **Não esquecer de incluir `messages.append(response)` antes da iteração com tool_calls**. O LLM precisa ver suas próprias tool_calls no histórico para encadear.
- **Não adicionar `selected_skill` errado no return**. Convenção: `handoff_target or skill_name` (skill que efetivamente respondeu).
