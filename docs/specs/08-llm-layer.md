# SPEC 08 — LLM Layer

**Propósito**: abstrair providers de LLM, garantir prompt caching eficiente, retry exponencial para falhas transientes.

## Onde vive

```
llm/
├── providers.py   # _build_llm, get_llm (cached), get_llm_for_tenant (BYOK)
├── caching.py     # system_message (cache_control por provider)
└── retry.py       # llm_retry() — tenacity AsyncRetrying
```

## Contrato público

```python
# providers.py
def get_llm(provider: str, model: str) -> BaseChatModel  # cached, modo "credits"
def get_llm_for_tenant(provider, model, api_key, base_url=None) -> BaseChatModel  # BYOK, sem cache

# Constantes canônicas:
HAIKU         = ("anthropic", "claude-haiku-4-5-20251001")
SONNET        = ("anthropic", "claude-sonnet-4-6")
GEMINI_FLASH  = ("google", "gemini-2.0-flash")
# GPT-4o family (128K ctx)
GPT4O_MINI    = ("openai", "gpt-4o-mini")
GPT4O         = ("openai", "gpt-4o")
# GPT-4.1 family (1M ctx)
GPT41_NANO    = ("openai", "gpt-4.1-nano")
GPT41_MINI    = ("openai", "gpt-4.1-mini")
GPT41         = ("openai", "gpt-4.1")
# GPT-5 family (400K ctx)
GPT5_NANO     = ("openai", "gpt-5-nano")
GPT5_MINI     = ("openai", "gpt-5-mini")
GPT5          = ("openai", "gpt-5")
# GPT-5.4 / GPT-5.5 frontier (1M ctx)
GPT54_MINI    = ("openai", "gpt-5.4-mini")
GPT54         = ("openai", "gpt-5.4")
GPT55         = ("openai", "gpt-5.5")
# Reasoning (o-series, 200K ctx)
O3_MINI       = ("openai", "o3-mini")
O3            = ("openai", "o3")
O4_MINI       = ("openai", "o4-mini")
# DeepSeek (OpenAI-compatible, base_url override, 1M ctx)
DEEPSEEK_V4_FLASH = ("deepseek", "deepseek-v4-flash")  # default fast
DEEPSEEK_V4_PRO   = ("deepseek", "deepseek-v4-pro")
DEEPSEEK_CHAT     = ("deepseek", "deepseek-chat")      # V3 — deprecated 2026-07-24
DEEPSEEK_REASONER = ("deepseek", "deepseek-reasoner")  # R1, sem temperature — deprecated
OLLAMA_LLAMA  = ("ollama", "llama3.2")

# caching.py
def system_message(content: str, *, provider: str, volatile: str = "") -> SystemMessage

# retry.py
def llm_retry() -> AsyncRetrying  # 3 tentativas, exponencial 2-10s
```

## Orquestração híbrida — papéis da plataforma vs. papéis do tenant

**Decisão de produto (firmada jun/2026):** os papéis LEVES de classificação rodam
SEMPRE no modelo da plataforma; só as **skills** (agentes que falam com o cliente)
seguem a escolha do tenant.

| Papel | Provider/model | Quem define | Pago por |
|---|---|---|---|
| `orchestrator` | **Anthropic Haiku** | Plataforma, via `.env` | Plataforma |
| `analyst` | **Anthropic Haiku** | Plataforma, via `.env` | Plataforma |
| `sentiment` | **Anthropic Haiku** (reusa o par do orchestrator) | Plataforma, via `.env` | Plataforma |
| skills (`vendedor`, `farmaceutico`, …) | qualquer provider | **Tenant** (BYOK ou model-tier em credits) | Tenant |

A fonte da verdade é `agents/graph_builder.py::_PLATFORM_ROLES =
{"orchestrator", "analyst", "sentiment"}`. Para esses papéis, `_make_llm_factory`
chama sempre `get_llm` com a chave da plataforma (`anthropic_api_key`),
**ignorando o `provider_override` do BYOK e a coluna do tenant**. As skills passam
por `get_llm_for_tenant` (BYOK) ou `get_llm` (credits).

**Configuração** (`api/config.py`, sobrescritível por env):

```
DEFAULT_ORCHESTRATOR_PROVIDER = "anthropic"
DEFAULT_ORCHESTRATOR_MODEL    = "claude-haiku-4-5-20251001"
DEFAULT_ANALYST_PROVIDER      = "anthropic"
DEFAULT_ANALYST_MODEL         = "claude-haiku-4-5-20251001"
DEFAULT_SKILL_PROVIDER        = "anthropic"   # default em credits; BYOK sobrepõe
DEFAULT_SKILL_MODEL           = "claude-sonnet-4-6"
```

**Por quê:** o roteamento é onde o produto inteiro depende — um misroute manda a
mensagem ao agente errado (ou a um beco sem handoff). Deixar o orquestrador na
mão de uma LLM fraca do tenant (DeepSeek/Gemini/Ollama) regredia o roteamento. A
plataforma absorve o roteador (Haiku é forte e barato pra classificação) e o
cliente paga só as skills. Ver memória *Orquestração híbrida*.

**Consequência intencional:** `tenant_llm_config.orchestrator_model`/`analyst_model`
ficam **inertes** — o controle desses papéis é só via `.env`. Não reabilitar a
escolha de modelo do orquestrador/analista por tenant sem rediscutir esta decisão
(reintroduz o misroute por LLM fraca). Cf. `services/llm_config.py::load_tenant_llm_config`
(que já força os defaults da plataforma nesses dois papéis).

> ⚠️ Mesmo com o orquestrador no Haiku, a **qualidade do prompt de roteamento**
> ainda importa: descrições de skill ambíguas no `skills_registry` (ex.: um termo
> catch-all como "mensagens ambíguas") viram ímã de misroute. A blindagem do
> roteador é dupla: papel forte (esta seção) + prompt/descrições determinísticos
> (SPEC 01 §roteamento, fast-paths).

## Providers suportados

| Provider | Lib | Auth | Cache? |
|---|---|---|---|
| `anthropic` | `langchain_anthropic.ChatAnthropic` | `api_key` (Bearer) | Sim — explicit `cache_control` |
| `google` | `langchain_google_genai.ChatGoogleGenerativeAI` | `google_api_key` | Não wired (precisa Vertex Cached Content API) — ver nota de custo abaixo |
| `openai` | `langchain_openai.ChatOpenAI` | `api_key` | Sim — automático >=1024 tokens |
| `deepseek` | `langchain_openai.ChatOpenAI` + `base_url` | `api_key` (`sk-...`) | Automático (context caching no lado da DeepSeek) — sem wiring nosso |
| `ollama` | `langchain_ollama.ChatOllama` | `base_url` (sem auth) | N/A (inferência local) |

> **DeepSeek** é OpenAI-compatible: reusamos `ChatOpenAI` apontando `base_url` para
> `settings.deepseek_base_url` (default `https://api.deepseek.com`). O `deepseek-reasoner`
> (R1), como os modelos o-series, **não aceita `temperature`** → o factory detecta por
> `"reasoner" in model` e omite. A chave é `sk-...`, **indistinguível** da OpenAI no
> `_detect_provider_from_key` → DeepSeek exige `provider` explícito na config do tenant.

Todos os providers configurados com:
- `timeout = settings.llm_timeout_seconds` (default 30s)
- `temperature = settings.llm_temperature` (default 0.2 — baixa pra evitar alucinação)
- `max_retries = 0` (delegamos para `llm_retry`)

## Gemini (Google) — safety settings obrigatórios

`_build_llm` constrói o `ChatGoogleGenerativeAI` com `safety_settings=_GEMINI_SAFETY_SETTINGS`
(`providers.py`), relaxando para `BLOCK_NONE` as **4 categorias que o Gemini aceita
configurar** (`HARASSMENT`, `HATE_SPEECH`, `SEXUALLY_EXPLICIT`, `DANGEROUS_CONTENT`).

**Por quê:** os filtros default do Gemini bloqueiam conteúdo sobre medicamentos/
dosagens (cai em `DANGEROUS_CONTENT`) → o modelo devolve candidate bloqueado/resposta
vazia, que vira fallback técnico pro cliente. Num atendimento de farmácia isso derruba
o núcleo do produto. A segurança real do domínio NÃO depende do filtro do provider —
está em `persona.forbidden_topics`, nos safety_guards pós-LLM (SPEC 10) e na temperatura
baixa. **Não remover** os safety_settings sem mover essa proteção pra outro lugar.

> As categorias legadas (`MEDICAL`, `VIOLENCE`, etc.) NÃO podem ser passadas — a API
> do Gemini as rejeita. Só as 4 acima.

**Tools + Gemini:** o Gemini rejeita function declaration com `parameters` de objeto
vazio. Toda tool deve ter ≥1 campo no `args_schema` (ver SPEC 03 §Não fazer). Validado:
as 22 tools de domínio + 3 de fluxo convertem limpo via `convert_to_genai_function_declarations`.

## Tier de capacidade do modelo (`llm/model_tier.py`)

`model_tier(provider, model) -> "strong" | "weak"` é a **fonte única** de "este
modelo precisa de andaime?". Multi-tenant: cada tenant escolhe modelo livremente;
os pequenos/baratos (Gemini *-flash/-lite, GPT *-mini/nano, Claude Haiku, locais)
**chamam tools de forma pouco confiável**. Medição em prod (jun/2026): farmaceutico
em `gemini-2.5-flash-lite` ficou com **82% dos turnos sem chamar tool**.

- Classificação por **token** (split em `-`/`.`/`_`), não substring — senão `"mini"`
  casaria dentro de `"geMINI"` e marcaria todo Gemini como weak. `gemini-2.5-pro`
  → strong; `gemini-2.5-flash` → weak.
- `ollama` é sempre weak. Modelo **desconhecido → strong** (default seguro: nunca
  injeta andaime sob incerteza, nunca altera comportamento de modelo já validado).

### Como descobrir o tier no caminho do agente

`_make_llm_factory` expõe `llm_factory.resolve(role) -> (provider, model)` — resolve
o par SEM construir o LLM (mesma precedência do `_get`). `run_skill` usa
`agents/nodes/skills/_base.py::resolve_skill_tier(llm_factory, role)` e grava
`model`/`tier` no trace do skill + `state["model_tier"]`.

> **Invariante de design:** o caminho **strong** deve permanecer byte-idêntico ao
> histórico (sem andaime, sem bloco extra no prompt → cache intacto). Todo andaime
> de modelo fraco (force-call determinístico, bloco de disciplina de tool) é GATED
> por `needs_tool_scaffolding`. Não introduzir andaime que rode também no strong,
> salvo se for no-op comprovado (ex.: force-call que só dispara quando a tool não
> foi chamada).

### Gate de andaime: `needs_tool_scaffolding(provider, model)`

`tier=="weak"` **OU** `provider ∈ {google, ollama, deepseek}`. Por quê provider-aware:
medição em prod mostrou que **mesmo `gemini-2.5-pro` (tier strong) falha em tool-calling** —
dispara tools de fluxo (transferência) à toa e mistura tool de domínio + fluxo no
mesmo turno. É comportamento da FAMÍLIA Google, não do tamanho. **DeepSeek** entra na
mesma lista por decisão de produto: tool-calling mais fraco que Claude/GPT grandes (e o
`deepseek-reasoner`/R1 não suporta function calling de forma confiável) → família inteira
tratada como weak. Anthropic/OpenAI grandes → `False` → caminho histórico intacto.

Andaimes gated por esse gate (todos no-op/ausentes para strong):

1. **Guarda domínio+fluxo no runtime** (`run_tool_loop(..., defer_premature_flow=True)`):
   quando o modelo dispara tool de fluxo (handoff/escalate/end) JUNTO com tool de
   domínio no mesmo turno, o sinal de fluxo é **adiado** — executa a de domínio, dá
   ack inócuo no fluxo e CONTINUA o loop, deixando o modelo responder com o que
   buscou. Sem isso, o runtime encerrava o turno e DESCARTAVA o resultado da tool
   (sintoma real: "Quantos vem na caixa?" → buscava a bula mas respondia "posso
   ajudar em algo mais?"). Fluxo sozinho continua sendo honrado na hora.
2. **Bloco de disciplina de tool no prompt** (`run_skill`, VOLÁTIL → não toca o
   prefixo cacheado): reforça "responda usando o resultado da tool; não transfira
   no mesmo turno; não afirme produto/preço/pedido sem chamar a tool".
3. **Disciplina de venda no `vendedor`** (`_SALES_DISCIPLINE`, VOLÁTIL, gated):
   modelos fracos pulam a Etapa 2 ("Mais alguma coisa?") e anotam/fecham no
   PRIMEIRO item → o `anotar_pedido_balcao`/`finalizar_pedido` dispara o transfer
   determinístico do worker (`order_finalized`, `celery_app.py`), e o cliente é
   mandado ao atendente sem chance de comprar mais. O bloco força coletar o
   pedido completo e só fechar quando o cliente disser que não quer mais nada.
   A regra já existe no `_SYSTEM`/`_SYSTEM_PRE_ATENDIMENTO` (Etapa 2); isto só a
   REFORÇA para quem precisa. Não é bug do worker nem do END — é o modelo fraco
   ignorando o fluxo multi-step.
4. **Allowlist estrita de dados no `vendedor`** (`build_field_discipline_block`,
   `services/sales_config.py`, VOLÁTIL, gated): modelos fracos ignoram instruções
   NEGATIVAS ("NÃO pergunte endereço/entrega") — o `build_checkout_flow_block` com
   `ask_delivery=False` já diz isso, mas o Gemini pede endereço mesmo assim
   (sintoma real: bot pedindo endereço de entrega sem o tenant ter configurado).
   No pré-atendimento o problema é pior: NÃO há bloco de fechamento, então nada
   proíbe a pergunta. O reforço converte a regra negativa numa LISTA FECHADA do
   que o agente PODE pedir (required_fields + pagamento/entrega só se ativos),
   respeitando `checkout_mode`/`ask_payment`/`ask_delivery`. Pré-atendimento passa
   sempre `allow_payment=False, allow_delivery=False` (resolvido no balcão). Se
   algum campo de endereço é obrigatório OU a entrega está ON, a allowlist permite
   o endereço (não cria falso bloqueio). Strong path intacto (gated + volátil).
5. **Force-recall de estoque no runtime** (`StockRecall` + `_maybe_force_stock_search`,
   `runtime.py`, gated): modelo fraco em modo ERP afirma "temos esse remédio" SEM
   ter chamado `buscar_produto` no turno. O prompt (`stock_check_block`) é ignorado
   e o `availability_guard` (SPEC 10) curto-circuita porque sem busca não há o que
   cruzar. O runtime detecta a afirmação não-verificada (`has_unverified_affirmation`,
   reusa o regex do guard) e FORÇA `buscar_produto`, regenerando a resposta a partir
   do resultado real. Suprime quando carrinho/pedido já mexeu no turno (item
   validado). Fornecido por `farmaceutico` (via `run_skill(verify_stock_affirmation=
   has_catalog)`) e `vendedor` (modo normal). Detalhe em SPEC 10 §força-busca de estoque.
6. **Gate de quantidade no `vendedor`** (`domain_tool_gate`/`_order_gate`, gated weak):
   modelo fraco pula a etapa de quantidade e chama `adicionar_ao_carrinho` com o
   default `qty=1` quando o cliente só confirmou o PRODUTO ("Sim"). O gate veta o add
   de item novo quando `_stated_quantity(current_message)` é False e `qty<=1` (item
   já no carrinho ou `qty>1` passam livres), devolvendo correção que manda perguntar
   "Quantas unidades?". `_stated_quantity` é determinístico (dígito não-dosagem OU
   numeral por extenso). Reforçado no playbook + `_SALES_DISCIPLINE`. Cf. SPEC 02 §vendedor.

> **Por que NÃO há force-call amplo de "consultou? então responde" no farmaceutico:**
> a métrica `pct_sem_tool` é contaminada por turnos de condução legítimos (perguntas
> "é cólica ou diarreia?", confirmações). Forçar tool nesses turnos regrediria o
> fluxo. O force-call determinístico fica restrito a sinais inequívocos (ex.:
> vendedor "fechou pedido" sem `anotar_pedido_balcao`; ou afirmação de disponibilidade
> "temos" sem `buscar_produto` — item 5 acima). A condição de gatilho é uma FRASE
> inequívoca de afirmação, não a métrica bruta de "não chamou tool".

## Invariantes

1. **`get_llm` retorna instância cacheada** (`lru_cache(maxsize=32)`). Para BYOK, `get_llm_for_tenant` SEMPRE cria nova (não cachear chave do tenant).
2. **Cache LLM cliente envelhece** — instância idle gera `APIConnectionError` na próxima chamada. `llm_retry` reabre conexão.
3. **Temperatura sempre baixa em produção** (0.2). Pra debugging/criatividade, override via env, não via código.
4. **`cache_control` só funciona em prefixo estável**. Qualquer mudança antes do marker invalida.
5. **System message Anthropic content como list[dict]** quando usa cache_control. LangChain aceita esse formato.

## Prompt caching — regra de ouro

O `system_prompt` de um skill é grande (~3-8K tokens) e estável entre turnos. Cache hit economiza ~90% no custo de input e 50% de latência primeiro-token.

**Pattern correto** (já implementado em `_base._build_messages`):

```python
parts = [persona, customer_memory_block, base_system, extra_instructions, capability_blocks]
volatile_parts = [cart_block, sales_status_block, handoff_continuation]

system_prompt = "\n\n".join(parts)               # ESTÁVEL → cacheado
volatile_prompt = "\n\n".join(volatile_parts)    # POR-TURNO → após marker

system_message(system_prompt, provider="anthropic", volatile=volatile_prompt)
```

Resultado em `llm/caching.py::system_message`:

```python
SystemMessage(content=[
    {"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}},
    {"type": "text", "text": volatile_prompt}  # se non-empty, após marker
])
```

Cache hit = exact match do prefixo até o marker, incluindo tools (Anthropic cacheia tools junto).

**Anti-pattern**:

```python
# RUIM — concatena estado volátil dentro do system estável
system = f"{persona}\n\n{base_system}\n\n[Carrinho: {cart}]"  # cache miss em cada add
```

### Bugs históricos de cache (2026-05-31)

1. **`run_skill` chamava `_build_messages(state, system_prompt)` sem `volatile_prompt`**, e o bloco `[CONTINUAÇÃO INTERNA — handoff]` era concatenado dentro de `parts`. Resultado: TODO handoff farmaceutico→vendedor invalidava o cache do prefixo (~3-5K tokens reenviados full price). Fix: separação correta `parts` vs `volatile_parts` em [`_base.py::run_skill`](../../agents/nodes/skills/_base.py).
2. **Estado por-turno no system_prompt do vendedor**: cart_block, sales_status_block, address_hint, customer_memory já estavam corretos como volátil em `vendedor.py` — não regredir.

Lista do que DEVE estar em volatile_parts (não exaustiva):
- Carrinho do cliente
- Status de campos obrigatórios (✓/✗ tem/falta)
- Endereço já cadastrado (address_hint)
- Memória do cliente (alergias, contínuos, preferências, segmento)
- Bloco de continuação de handoff (`[CONTINUAÇÃO INTERNA]`)
- Qualquer "[CONTEXTO DE HANDOFF]" do pré-atendimento
- Bloco de contexto temporal (`time_aware_greeting`)
- **Diretiva de sentimento** (`sentiment_directive`, capability `intelligence.sentiment_analysis`) — muda a cada turno conforme o humor do cliente. Injetada em `run_skill` via `state["sentiment_directive"]`. NUNCA no prefixo estável.

> Persona (incluindo os campos novos `vocabulary_level`/`explanation_depth`) é **estável** → vai no prefixo cacheado via `_persona_prefix`. Editar persona = 1 cache miss e re-cache (esperado). Sentimento é **volátil** → não invalida o prefixo.

## Retry layer

```python
from llm.retry import llm_retry
async for attempt in llm_retry():
    with attempt:
        response = await llm.ainvoke(messages)
```

Wraps com `tenacity.AsyncRetrying`:
- `retry=retry_if_exception_type(Exception)` (genérico — todo erro é retentado)
- `stop=stop_after_attempt(3)`
- `wait=wait_exponential(multiplier=1, min=2, max=10)`
- `before_sleep` loga warning
- `reraise=True` (estoura erro original)

**Onde usar**:
- ✅ Nodes idle entre turnos (orchestrator, analyst, skills sem tools)
- ❌ Loops com tool-calling (cada iter já é nova chamada — retry interno do loop é suficiente)

Por que orchestrator/analyst ESPECIFICAMENTE precisam: instâncias `ChatAnthropic` são cacheadas via `lru_cache` (`get_llm`). Em prod elas ficam idle entre turnos (orquestrador roda 1x por mensagem). O pool httpx interno envelhece e a primeira chamada após idle dá `APIConnectionError`. Sem `llm_retry`, o node cai em fallback toda chamada.

## Pontos de extensão

### Adicionar novo provider

1. Em `_build_llm`: branch `if provider == "<novo>"` retornando o chat model LangChain correspondente.
2. Adicionar import lazy (dentro do branch) pra não custar boot.
3. Constante no fim do arquivo (opcional, se vamos usar muito).
4. Verificar se precisa de cache wiring específico em `caching.py::system_message`.

### Adicionar suporte de cache pra Google/outro

Em `system_message`:
```python
if provider == "google":
    # Google Vertex usa "cached_content" via API. LangChain ainda não expõe.
    # Implementar via cliente direto + cache key explícito.
    ...
```

Por enquanto Google cai no fallback "concat tudo".

> **Decisão (2026-06-16): NÃO implementar Vertex Cached Content agora.** O objetivo
> do BYOK Gemini é baratear o atendimento, e o Gemini 2.0 Flash já é mais barato SEM
> cache ($0.075/M input) do que Haiku CACHEADO ($0.08/M) e ~40× mais barato que Sonnet.
> Além disso o explicit cache do Gemini tem mínimo de tokens alto (dezenas de milhares);
> nossos prompts (~5-8K) ficam abaixo do mínimo cacheável → esforço alto, ganho ~zero.
> A economia vem de TROCAR de modelo, não de cachear. Reavaliar só se os prompts
> crescerem muito ou se o volume por tenant justificar.

### Mudar temperatura ou timeout por papel

- Global: `settings.llm_temperature`, `settings.llm_timeout_seconds`.
- Por skill: requereria refator (hoje `_build_llm` não recebe override). Caminho: passar `**kwargs` no factory e propagar.

## Regressões conhecidas / "Não fazer"

- **Não cachear cliente BYOK.** API key no `lru_cache` vaza entre tenants (mesmo modelo, chaves diferentes).
- **Não passar `max_retries > 0` no constructor do ChatAnthropic** — duplica retries com `llm_retry` e detona quota.
- **Não usar `temperature > 0.3` em prod** — modelo inventa preço/medicamento. Já tomamos esse golpe.
- **Não jogar `current_message` ou estado de turno no prefixo cacheado** — cache miss garantido.
- **Não esquecer de chamar `_build_messages` com `volatile_prompt=...`** quando o skill tem estado por-turno. Default vazio → tudo cai no prefixo estável; **MAS** isso só ajuda se o caller manteve esse estado fora do `system_prompt`. A trava real está em separar `parts` vs `volatile_parts` ANTES de juntar.
- **Não esquecer de incluir `tools` no `llm.bind_tools(tools)` ANTES do prefixo cacheado** — Anthropic cacheia tools junto com o prefixo. Mudar tools = cache miss.
- **Não usar `langchain.ChatModel` genérico** — passe sempre por `get_llm` pro factory wirar provider/cache certo.
- **Não passar `temperature` diretamente a modelos o1/o3/o4** — eles rejeitam o parâmetro e explodem em runtime. O factory já cuida disso; só é problema se alguém instanciar `ChatOpenAI` fora do factory.

## Métricas LLM (de olho)

Disponíveis em Prometheus via `prometheus_fastapi_instrumentator` + counters manuais em `workers/celery_app.py`:

- `llm_errors_total{tenant_id, skill, llm_model}` — counter de falhas
- `conversation_latency_seconds{tenant_id, skill}` — histogram end-to-end
- `conversations_total{tenant_id, skill, status}` — counter (ok/error)

Métrica de cache hit não está exposta. Caminho: tap em response Anthropic (`response.response_metadata["usage"]["cache_read_input_tokens"]`).

## Custo aproximado (referência interna)

### OpenAI — GPT-4.1 family (1M ctx, knowledge cutoff Jun/2024)

| Modelo | $/1M input (cached) | $/1M input | $/1M output |
|---|---|---|---|
| gpt-4.1-nano | $0.025 | $0.10 | $0.40 |
| gpt-4.1-mini | $0.10 | $0.40 | $1.60 |
| gpt-4.1 | $0.50 | $2.00 | $8.00 |

### OpenAI — GPT-4o family (128K ctx)

| Modelo | $/1M input (cached) | $/1M input | $/1M output |
|---|---|---|---|
| gpt-4o-mini | $0.075 | $0.15 | $0.60 |
| gpt-4o | $1.25 | $2.50 | $10.00 |

### OpenAI — GPT-5 family (400K ctx, knowledge cutoff Set/2024)

| Modelo | $/1M input (cached) | $/1M input | $/1M output |
|---|---|---|---|
| gpt-5-nano | — | $0.05 | — |
| gpt-5-mini | — | $0.25 | — |
| gpt-5 | $0.125 | $1.25 | $10.00 |

### OpenAI — GPT-5.4 / GPT-5.5 frontier (1M ctx)

| Modelo | $/1M input | $/1M output |
|---|---|---|
| gpt-5.4-mini | $0.75 | $4.50 |
| gpt-5.4 | $2.50 | $15.00 |
| gpt-5.5 | $5.00 | $30.00 |

### OpenAI — Reasoning / o-series (200K ctx, sem temperature)

| Modelo | $/1M input (cached) | $/1M input | $/1M output |
|---|---|---|---|
| o3-mini | $0.55 | $1.10 | $4.40 |
| o4-mini | $0.275 | $1.10 | $4.40 |
| o3 | $0.50 | $2.00 | $8.00 |

### Outros providers

| Modelo | $/1M input (cached) | $/1M input | $/1M output |
|---|---|---|---|
| Claude Haiku 4.5 | $0.08 | $0.80 | $4.00 |
| Claude Sonnet 4.6 | $0.30 | $3.00 | $15.00 |
| Gemini 2.0 Flash-Lite | — | $0.075 | $0.30 |
| Gemini 2.0 Flash | — | $0.10 | $0.40 |
| Gemini 2.5 Flash | — | $0.30 | $2.50 |
| Gemini 2.5 Pro | — | $1.25 | $10.00 |
| DeepSeek V4 Flash | $0.0028 | $0.14 | $0.28 |
| DeepSeek V4 Pro | $0.003625 | $0.435 | $0.87 |
| DeepSeek V3 (chat) — *deprecated 2026-07-24* | — | $0.27 | $1.10 |
| DeepSeek R1 (reasoner) — *deprecated 2026-07-24* | — | $0.55 | $2.19 |

> **DeepSeek V4** (1M ctx, max output 384K, Tool Calls ✓): `deepseek-v4-flash` é o
> piso de custo e o novo **default fast** do provider; `deepseek-v4-pro` para mais
> capacidade. Ambos suportam thinking/non-thinking mode (thinking é default) e
> aceitam `temperature` (só o `deepseek-reasoner` legado é que não). Os ids
> `deepseek-chat`/`deepseek-reasoner` **descontinuam em 2026-07-24** (viram
> non-thinking/thinking do v4-flash) — mantidos no catálogo só pela janela de compat.

> **Gemini para baratear (BYOK):** `gemini-2.0-flash-lite` é o piso de custo — input mais
> barato que Haiku cacheado e ~40× abaixo de Sonnet. `2.5-flash` quando precisar de mais
> raciocínio. Preços catalogados em `pricing.py` + `prometheus_rules.yml` (manter em sync).
> `get_price` usa longest-prefix: a entrada `gemini-2.0-flash-lite` PRECISA ser explícita,
> senão casaria `gemini-2.0-flash` e cobraria mais caro.

(Preços públicos em USD, sujeitos a mudança. Fonte: developers.openai.com/api/docs/models)

⚠️ **Modelos de raciocínio (o1/o3/o4)**: não aceitam o parâmetro `temperature`. O factory `_build_llm` detecta automaticamente pelo prefixo do model id e omite `temperature` nesses casos. Não passar temperatura explicitamente para esses modelos fora do factory.

Default da plataforma: Haiku para orchestrator/analyst (low cost, alto volume), Sonnet para skills (qualidade na resposta ao cliente). A **divisão de papéis** (orchestrator/analyst/sentiment SEMPRE na plataforma via `.env`; skills definidas pelo tenant) está detalhada em §"Orquestração híbrida — papéis da plataforma vs. papéis do tenant".
