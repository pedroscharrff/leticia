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
OLLAMA_LLAMA  = ("ollama", "llama3.2")

# caching.py
def system_message(content: str, *, provider: str, volatile: str = "") -> SystemMessage

# retry.py
def llm_retry() -> AsyncRetrying  # 3 tentativas, exponencial 2-10s
```

## Providers suportados

| Provider | Lib | Auth | Cache? |
|---|---|---|---|
| `anthropic` | `langchain_anthropic.ChatAnthropic` | `api_key` (Bearer) | Sim — explicit `cache_control` |
| `google` | `langchain_google_genai.ChatGoogleGenerativeAI` | `google_api_key` | Não wired (precisa Vertex Cached Content API) — ver nota de custo abaixo |
| `openai` | `langchain_openai.ChatOpenAI` | `api_key` | Sim — automático >=1024 tokens |
| `ollama` | `langchain_ollama.ChatOllama` | `base_url` (sem auth) | N/A (inferência local) |

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

> **Gemini para baratear (BYOK):** `gemini-2.0-flash-lite` é o piso de custo — input mais
> barato que Haiku cacheado e ~40× abaixo de Sonnet. `2.5-flash` quando precisar de mais
> raciocínio. Preços catalogados em `pricing.py` + `prometheus_rules.yml` (manter em sync).
> `get_price` usa longest-prefix: a entrada `gemini-2.0-flash-lite` PRECISA ser explícita,
> senão casaria `gemini-2.0-flash` e cobraria mais caro.

(Preços públicos em USD, sujeitos a mudança. Fonte: developers.openai.com/api/docs/models)

⚠️ **Modelos de raciocínio (o1/o3/o4)**: não aceitam o parâmetro `temperature`. O factory `_build_llm` detecta automaticamente pelo prefixo do model id e omite `temperature` nesses casos. Não passar temperatura explicitamente para esses modelos fora do factory.

Default da plataforma: Haiku para orchestrator/analyst (low cost, alto volume), Sonnet para skills (qualidade na resposta ao cliente).
