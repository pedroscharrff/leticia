# SPEC 01 — Agent Graph (LangGraph)

**Propósito**: orquestrar a conversa em nodes especializados com handoff entre skills e validação pós-LLM.

## Onde vive

```
agents/
├── graph_builder.py     # build_graph_for_tenant() + TenantConfig + SkillOverride
├── router.py            # route_to_skill, handoff_router, analyst_router
├── state.py             # AgentState (TypedDict)
└── nodes/
    ├── context.py       # load_context / save_context
    ├── ingest_media.py
    ├── orchestrator.py
    ├── analyst.py
    ├── safety_guard.py
    └── skills/          # ver SPEC 02
```

## Contrato público

```python
# graph_builder.py
@dataclass
class TenantConfig:
    tenant_id: str
    schema_name: str
    callback_url: str
    skills_active: list[str]
    plan: str = "basic"
    skill_overrides: dict[str, SkillOverride] = {}
    llm_mode: str = "credits"    # "credits" | "byok"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    orchestrator_provider/model: str
    analyst_provider/model: str
    default_skill_provider/model: str

def build_graph_for_tenant(cfg: TenantConfig, redis) -> CompiledGraph
```

`graph.ainvoke(initial_state, config={"configurable": {"thread_id": session_id}})` é o ponto de entrada.

### initial_state (chaves obrigatórias)

`tenant_id, session_id, phone, schema_name, current_message, messages=[], available_skills, customer_profile, cart, callback_url`. Opcionalmente: chaves de mídia (`media_type`, etc).

## Invariantes

1. **Skills no grafo == skills_active intersect _KNOWN_SKILLS**. Tentar rotear para skill não compilado quebra o LangGraph. `_KNOWN_SKILLS`/`all_skill_nodes` agora DERIVAM de `agents/skills_registry.py::SKILLS` (fonte única) — ver SPEC 02.
2. **`guardrails` sempre tem node**. É safety net global, não depende do plano.
3. **`safety_guard` sempre tem node**. Faz passthrough quando `inventory.track_stock=OFF`.
4. **Tenant com 1 skill → fast-path** no orchestrator (pula LLM).
5. **`_MAX_HANDOFFS_PER_TURN = 2`** — anti-loop estrito. Não relaxar sem entender o impacto de custo.
6. **`analyst_max_retries`** vem de `settings.analyst_max_retries`. Estourou? "Deixa passar" (`final_approved=True`, `forced_through=True` no trace).
7. **Trace step por node**: todo node adiciona `{node, ts_ms, data}` em `state.trace_steps`. Tools agregam em `data.tool_calls`.

## Fluxos críticos

### Fluxo principal (caminho feliz)

```
START → load_context → ingest_media → sentiment_analyzer → orchestrator
      → <skill>       (pode emitir [[HANDOFF:X]] → vai pro skill X)
      → safety_guard  (passthrough se SEM catálogo / sales.stock_check=OFF, senão valida)
      → analyst       (aprova → save_context → END; reprova → volta pro skill)
      → save_context → END
```

### Roteamento mode-aware do orchestrator (validação farmacêutica)

> **Provider do orquestrador:** orchestrator/analyst/sentiment rodam SEMPRE no
> modelo da plataforma (Anthropic Haiku via `.env`), nunca no BYOK do tenant — só
> as skills seguem a escolha do tenant. Decisão e configuração em SPEC 08
> §"Orquestração híbrida". Tirar a LLM fraca do roteamento é metade da blindagem; a
> outra metade é manter as descrições de skill (`skills_registry`) e os fast-paths
> determinísticos — descrição catch-all ("mensagens ambíguas") vira ímã de misroute.

O orchestrator classifica por intenção (regras no `_SYSTEM`). A regra 2 manda **produto nomeado** ("tem Dipirona?", "quanto custa Tylenol?") → **vendedor**. Há UM override: quando `sales.pharmacist_validation` ON, medicamento nomeado vai ao **farmaceutico** (validar na bula antes de anotar).

⚠️ **Esse override SÓ vale em pré-atendimento** (`sales.stock_check` OFF, sem catálogo). Com catálogo (Sheets/ERP) o override é **suprimido** → "tem X?" segue pro vendedor, que consulta o catálogo (fonte da verdade do que a loja carrega). Sem essa condição, o farmaceutico respondia disponibilidade enumerando apresentações **da bula** e o vendedor desmentia depois (regressão real, jun/2026). Em modo catálogo, o farmaceutico fica só para **sintoma** (regra 1) e **dúvida clínica** (regra 5); a validação de apresentação vem do `buscar_produto` do vendedor (que pode fazer o single-hop ao farmaceutico via `sales.pharmacist_validation`). Cf. SPEC 04 §"Os três modos".

`sentiment_analyzer` (`agents/nodes/sentiment_analyzer.py`) é **passthrough quando a capability `intelligence.sentiment_analysis` está OFF** (early-return após 1 leitura cacheada de `is_enabled` — zero custo/latência). Quando ON: classifica o sentimento (Haiku), grava `sentiment`/`sentiment_score`/`sentiment_directive` (este último é injetado como bloco volátil nos skills) e, se `escalate_on_frustration` estiver ligado e o score passar do limiar, seta `escalate=True` (reusa o fluxo de escalação abaixo — NÃO cria caminho novo).

### Fluxo de handoff entre skills (HÍBRIDO: tool primária + marcador fallback)

Caminho PRIMÁRIO: o skill chama a tool `transferir_para_especialidade(target_skill, context)`.
O `AgentRuntime` (`agents/runtime.py`) detecta a tool em `tool_calls`, seta
`state.handoff_to`/`handoff_context` e encerra o turno. O `target_skill` é um
`Literal` derivado de `allowed_handoffs` (registry) → destino inválido é
impossível no schema.

Caminho FALLBACK (rede de segurança): se o skill emitir `[[HANDOFF:vendedor:...]]`
em texto, `_parse_handoff` captura e limpa o marcador. Tool vence; marcador é
fallback. **Em ambos** o `handoff_router` valida: target em
`_VALID_HANDOFF_TARGETS`, em `available_skills`, não é o último skill (anti-loop),
`handoff_count <= 2`. O próximo skill recebe `received_handoff=True` via
`prev_response` e **complementa** (resposta concatenada). NUNCA falha estrito.

### Fluxo de escalation humana (HÍBRIDO)

Caminho PRIMÁRIO: skill chama `transferir_para_atendente(reason)` → runtime seta
`escalate=True`. FALLBACK: `[[ESCALATE]]` em texto → `_parse_escalate`. Depois:
1. `analyst_router` retorna `"escalate"` (prioridade máxima).
2. `save_context` salva normalmente (e LIMPA `current_owner`).
3. **Worker** (fora do grafo) combina com keyword/order_finalized e dispara `transfer_to_human` + `auto_pause_after_handoff`.

Fim de atendimento idem: tool `encerrar_atendimento` OU `[[END]]` → `end_conversation=True`.

### Sticky ownership (orchestrator não roda a cada turno)

Gated por `settings.sticky_ownership_enabled`. Quando ON, o
orchestrator reusa `state.current_owner` (persistido em Redis por `context.py`)
e PULA o LLM de classificação, salvo se: owner ausente/inválido, ou a mensagem
casa com `_should_bypass_sticky` (emergência / pedido de humano). Owner é limpo
em fim/escalation/pedido finalizado. Reduz custo/latência/misroute mid-conversa.

⚠️ **O owner é o LÍDER do turno (`skill_history[0]`), não o `selected_skill`.**
Num handoff (vendedor→genericos), `_base.py` sobrescreve `selected_skill` com o
DESTINO. Um handoff é delegação de UM turno — quem conduz segue sendo o líder que
o orchestrator escolheu. `save_context` persiste `skill_history[0]` (por-turno;
[0] = líder). **Regressão real (jun/2026, ao ligar sticky por default):** persistir
o destino fazia o sticky SEQUESTRAR a conversa pro sub-especialista — `genericos`
(sem catálogo/`buscar_produto`) virava owner após um handoff e em TODA mensagem
seguinte só se desculpava e escalava ("roteando tudo pro genérico"). Não reverter
para `selected_skill` aqui.

### Fluxo de retry do analyst

1. Analyst reprovou + `retry_count < max_retries` → `final_response=""`, `retry_count++`, route volta pro skill.
2. Skill regera do zero (skill_history mantém histórico).
3. Estourou retries? `forced_through=True`, deixa a última resposta passar.

## Pontos de extensão

### Adicionar skill ao grafo

1. Adicionar `SkillDefinition` em `agents/skills_registry.py::SKILLS` (fonte única).
2. `graph_builder.py` resolve nodes via `load_skill_nodes(PLAN_GATED_SKILLS)` e binda `llm_factory` automaticamente — **nenhuma edição de imports/`all_skill_nodes`/`_KNOWN_SKILLS` necessária**.

Os mapas `routing_map`, `handoff_map`, `retry_map` derivam automaticamente de `active_skills`.

### Mudar política de retry / fallback

- `analyst_max_retries` em `config.Settings`.
- `_HARD_FALLBACK = "farmaceutico"` em `router.py` — fallback global quando nada mais funciona.
- `_resolve_fallback_skill` em `orchestrator.py` — fallback de classificação, prefere continuidade de skill_history.

### Mudar timeout / temperatura LLM

`config.Settings`: `llm_timeout_seconds`, `llm_temperature`. Aplicado em `llm/providers.py::_build_llm`.

## Regressões conhecidas / "Não fazer"

- **Não rotear para skill que não está em `available_skills`** — `_resolve_fallback` existe pra isso. Se rotear cego, LangGraph quebra com `KeyError`.
- **Não confiar em `analyst_approved=False` quando o agent_escalate ativo** — checar `escalate` primeiro em `analyst_router` (prioridade máxima). Se inverter, conversa de emergência médica vira retry.
- **Não fazer `retry=True` quando `skill_history` está vazio** — usar `_HARD_FALLBACK`. Skill_history vazio significa que algo deu erro grave antes do skill rodar.
- **Não remover o `llm_retry()` do orchestrator e analyst.** APIConnectionError do Anthropic é silencioso e regular: nodes idle entre turnos + httpx pool envelhece. Sem retry, TODOS os turnos viram fallback.
- **Não jogar estado por-turno no `system_prompt` estável.** Carrinho, status de campos, contexto de handoff → vão em `volatile_prompt` no `_build_messages`. Senão cache miss em TODA mensagem.
- **Não usar `SystemMessage` consecutivo depois de `HumanMessage`/`AIMessage` no Anthropic** (loops force-call no vendedor). Use `HumanMessage` com prefixo "[INSTRUÇÃO INTERNA]".
- **Não persistir o sticky owner a partir de `selected_skill`** — ele reflete o DESTINO do handoff, não o líder. Use `skill_history[0]` (líder do turno). Senão um handoff transitório (vendedor→genericos) sequestra a conversa pro sub-especialista em todos os turnos seguintes (regressão jun/2026). Ver §"Sticky ownership".

## Trace step shape

Tudo que vai em `state.trace_steps`:

```python
{
    "node": "skill:vendedor",         # ou "orchestrator", "analyst", "guardrails"...
    "ts_ms": 1717185730912,
    "data": {
        # comum
        "chars": 128,
        # orchestrator
        "skill": "vendedor",
        "confidence": 0.92,
        "intent": "...",
        "fallback": "exception"?,       # quando entrou via except
        "fast_path": True?,              # single_skill ou greeting
        # analyst
        "approved": True,
        "final_approved": True,
        "forced_through": False,
        "retry_count": 0,
        "max_retries": 2,
        "reason": "...",
        # skills com tools
        "iters": 3,
        "tool_calls": [{"iter":1, "name":"buscar_produto", "args":{...}, "result_preview":"...", "error":"..."}],
        # handoff
        "handoff_to": "vendedor"?,
        # safety
        "guards_fired": ["price", "availability"],
        # erro do node (qualquer node)
        "error": {"type":"APIConnectionError", "msg":"...", "stack":"..."},
    }
}
```

Persistido por `services.agent_traces.persist_trace` em `{schema}.agent_traces`.
