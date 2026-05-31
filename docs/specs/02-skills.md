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
3. **Marker invisível**: `[[HANDOFF:X]]`, `[[ESCALATE]]` SEMPRE removidos do texto antes do cliente ver (regex em `_parse_handoff` / `_parse_escalate`).
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
Tools: `consultar_bula`, `consultar_bula_secao`.
Quando: sintoma, dúvida farmacêutica, posologia, interações.
Característica: **handoff obrigatório para vendedor** quando cliente sinaliza finalização (`pode finalizar`, `pode mandar`...). Skill não pode "confirmar pedido" — não tem tool para isso.

### `vendedor` — Vendas
Plano: pro.
Tools (modo normal): `buscar_produto`, `adicionar_ao_carrinho`, `remover_do_carrinho`, `atualizar_qtd_carrinho`, `finalizar_pedido`, `salvar_dados_cliente`, `consultar_pedido`, `cancelar_pedido`, `editar_pedido`. Tools extras condicionadas: `recomendar_complementos`, `calcular_frete`, `gerar_link_pix`, `registrar_alergia`, `registrar_medicamento_continuo`, `registrar_preferencia`.
Tools (modo pré-atendimento): `salvar_dados_cliente`, `consultar_pedido`, `anotar_pedido_balcao`.
Quando: cliente quer comprar, preço, montar carrinho.
Característica: **bifurca em modo normal vs pré-atendimento** baseado em capability `sales.stock_check`. Tem force-call no pré-atendimento (se LLM "fechou" sem chamar `anotar_pedido_balcao`, força a chamada).

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

- **Não emitir `[[HANDOFF:...]]` SEM ter respondido nada antes** (no farmaceutico, na situação de "cliente confirmou pedido"). Quebra a regra de "um único output por turno". Excepção: pedido fechado, onde só o marker está OK (vendedor concatena depois).
- **Não chamar tools fora do `for i in range(max_iters)`**. Ultrapassa o limite e o agente fica preso em loop infinito.
- **Não esquecer de limpar `[[HANDOFF]]` em modo pré-atendimento do vendedor**. Mesmo sem rotear, o marker aparece pro cliente se não rodar `_parse_handoff`.
- **Não retornar `cart` diferente do recebido por referência**. Tools mutam `cart` in-place; se você criar um dict novo, `save_context` salva o antigo e a mutação some.
- **Não esquecer de incluir `messages.append(response)` antes da iteração com tool_calls**. O LLM precisa ver suas próprias tool_calls no histórico para encadear.
- **Não adicionar `selected_skill` errado no return**. Convenção: `handoff_target or skill_name` (skill que efetivamente respondeu).
