# ARCHITECTURE — SaaS Farmácia

> Mapa técnico do sistema. Leitura obrigatória antes de qualquer mudança em backend.
> Foco: **onde as coisas vivem**, **como conversam**, **onde costumam quebrar**.

---

## 1. Visão de alto nível

SaaS multi-tenant que automatiza atendimento de farmácias via WhatsApp/Telegram usando um grafo multi-agente (LangGraph) sobre Claude/GPT/Gemini. Cada mensagem do cliente entra por um webhook, vira uma task Celery, executa um grafo de agentes específico do tenant e devolve a resposta pelo callback configurado.

```
Cliente WhatsApp/Telegram
        │
        ▼
[Gateway externo: Z-API, WA Cloud, ClickMassa, Uazapi...]
        │  POST webhook
        ▼
┌──────────────────────────────────────────────────────────┐
│ FastAPI (api/)                                           │
│  • /webhook/{token}            ← canal nativo            │
│  • /hooks/{tenant}/{slug}      ← broker universal        │
│  • UsageEnforcementMiddleware  ← limite mensal           │
│  • is_ai_paused?               ← curto-circuito          │
└──────────────────────────────────────────────────────────┘
        │ Celery task (RabbitMQ)
        ▼
┌──────────────────────────────────────────────────────────┐
│ workers/celery_app.py  ::  process_message               │
│  1. Carrega skills ativas + llm_config do tenant         │
│  2. Aplica ciclo de vida da sessão (close keyword, reset)│
│  3. build_graph_for_tenant(TenantConfig)                 │
│  4. graph.ainvoke(initial_state)                         │
│  5. Decide handoff humano (agente OR keyword OR pedido)  │
│  6. POST callback / forward reply_url                    │
│  7. Persiste trace + ofertas pré-handoff                 │
└──────────────────────────────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────────────────────────────┐
│ LangGraph (agents/)                                      │
│  START → load_context → ingest_media → orchestrator      │
│        → [skill node] → handoff_router                   │
│        → safety_guard → analyst → save_context → END     │
└──────────────────────────────────────────────────────────┘
        │
        ▼
   PostgreSQL (schema do tenant) + Redis (histórico, cache)
```

---

## 2. Componentes e onde vivem

| Componente | Path | Responsabilidade |
|---|---|---|
| **API FastAPI** | `api/main.py`, `api/routers/*` | Endpoints HTTP (webhook, admin, portal do tenant) |
| **Agentes (grafo)** | `agents/graph_builder.py`, `agents/router.py`, `agents/state.py` | Monta e roteia o LangGraph por tenant |
| **Nodes do grafo** | `agents/nodes/*` | `load_context`, `ingest_media`, `orchestrator`, `analyst`, `safety_guard`, `save_context` |
| **Skills** | `agents/nodes/skills/*` | `saudacao`, `farmaceutico`, `principio_ativo`, `genericos`, `vendedor`, `recuperador`, `guardrails` |
| **Tools (agente)** | `agents/tools/*` | `inventory`, `customer`, `balcao`, `bulario`, `sales_extras` |
| **LLM providers** | `llm/providers.py`, `llm/caching.py`, `llm/retry.py` | Factory de chat models, prompt cache, retry exponencial |
| **Workers** | `api/workers/celery_app.py`, `api/workers/jobs/*` | Task de mensagem + jobs proativos (cart recovery, refill nudge) |
| **Channels** | `api/channels/*` | Adapters de WhatsApp Cloud, Z-API, Telegram |
| **Broker universal** | `api/routers/broker.py`, `api/services/broker.py` | Ingest genérico de webhooks com mapping + reply template |
| **Capabilities** | `api/services/capabilities.py` + `capability_catalog` table | Feature flags por tenant (default + override) |
| **Safety guards** | `api/services/{availability,price,prescription,delivery}_guard.py` | Validadores determinísticos pós-LLM |
| **Billing** | `api/services/billing.py`, `api/routers/billing.py` | Stripe + Asaas, usage counters em Redis |
| **Persona/Prompts** | `api/services/persona.py`, `api/routers/persona.py` | Persona do bot + prompts customizados por skill (tenant override) |
| **Bula ANVISA** | `api/services/{anvisa_client,bula_extractor,bulario_repo}.py` | Base regulatória compartilhada |
| **Frontend** | `frontend/src/pages/Portal*.tsx` | Portal admin da farmácia (capabilities, skills, persona, broker, etc) |

---

## 3. Stack e infraestrutura

**Runtime**
- Python 3.11+ (FastAPI 0.115+, LangGraph, LangChain, Celery)
- Node 20 + React + Vite (frontend)
- Docker Compose orquestra tudo em prod

**Persistência**
- **PostgreSQL 16** (multi-schema; 1 schema por tenant; `public` para metadata global)
- **PgBouncer** em transaction mode na frente do Postgres (DATABASE_URL); conexão direta (DATABASE_URL_DIRECT) só para migrations/DDL
- **Redis 7** — histórico de conversa (TTL 30min default), cache de capabilities/TTL, usage counters, debounce de bundling
- **RabbitMQ** — broker do Celery
- **MinIO** (S3-compat) — mídia de ofertas

**LLM providers** (`llm/providers.py`)
- Anthropic Claude (Haiku 4.5 default para orchestrator/analyst; Sonnet 4.6 para skills)
- Google Gemini
- OpenAI GPT
- Ollama (self-hosted)
- Modo `credits` (chave da plataforma, cacheado via `lru_cache`) ou `byok` (chave do tenant, sem cache)
- **Prompt caching**: explicit `cache_control` em Anthropic via `llm/caching.py` — prefixo estável + bloco volátil separado para não invalidar o cache

**Observabilidade**
- `structlog` JSON em stdout
- Prometheus via `prometheus_fastapi_instrumentator` + counters/histograms manuais no worker
- `agent_traces` persistido por turno em `{schema}.agent_traces` (debug visual no portal)

---

## 4. Fluxo de uma mensagem (passo a passo)

### 4.1. Ingest

Dois caminhos possíveis dependendo de como o tenant integrou:

| Path | Quando usar | Endpoint |
|---|---|---|
| **Webhook nativo** | Gateway simples que entrega no formato canonical | `POST /webhook/{webhook_token}` |
| **Broker universal** | Qualquer gateway (Z-API, WA Cloud, ClickMassa…); mapeia payload bruto via field_map | `POST /hooks/{tenant_token}/{integration_slug}` |

Ambos:
1. Resolvem o tenant pelo token na URL
2. Checam `UsageEnforcementMiddleware` (limite mensal + status da subscription → 402)
3. Checam `services.conversation_state.is_ai_paused()` (curto-circuito se atendente humano assumiu)
4. Disparam Celery task (`process_message` ou `process_broker_message`) e respondem 202

Mensagens picadas em sequência rápida são agrupadas via **debounce/bundling** (Redis list + `process_bundled_message`) quando `bundle_enabled=true` na integração.

### 4.2. Worker (Celery)

`process_message` em `api/workers/celery_app.py`:

1. Lazy-init de pools (Postgres, Redis) no event loop do worker
2. Carrega `skills_config` ativas do schema do tenant
3. Carrega `tenant_llm_config` (provider/model por papel + overrides por skill)
4. **Ciclo de vida da sessão** (`_maybe_close_or_reset_session`):
   - Se `closed_at` marcado E pausa expirou → reseta (cliente voltou)
   - Se mensagem casa com `close_keywords` → encerra + envia `close_message` + RETORNA (skip do agente)
5. Monta `TenantConfig` + `build_graph_for_tenant`
6. `graph.ainvoke(initial_state)` → roda o LangGraph (ver §5)
7. **Decisão de handoff humano** (depois do grafo):
   - `agent_escalate` (skill marcou `escalate=True`) OR
   - `order_just_finalized` (tool `finalizar_pedido` deixou marker no `cart.last_order`) OR
   - palavra-chave do `handoff_config.trigger_keywords`
   - Se sim → `transfer_to_human` (POST no provider, ex. ClickMassa) + `auto_pause_after_handoff` (default 4h)
8. POST callback / forward `reply_url` com `reply_body_template` aplicado
9. **Pós-handoff** (se executou): `_send_post_handoff_messages` — resumo + ofertas em mensagens separadas; ordem controlada por `handoff_config.post_handoff_order` (`"summary_first"` default, `"offers_first"` para inverter)
10. `persist_trace` em `{schema}.agent_traces` com latência, error, steps

### 4.3. Grafo (LangGraph)

```
START
  ↓
load_context     → carrega histórico (Redis), persona/prompts/sales_config (Postgres),
                   customer, cart persistido
  ↓
ingest_media     → se media_type setado: transcreve áudio (Groq Whisper) ou
                   descreve imagem (Anthropic vision). Substitui current_message.
  ↓
orchestrator     → fast-paths: skill único OR saudação pura. Senão LLM Haiku classifica
                   intent → selected_skill. Fallback respeitando available_skills.
  ↓  route_to_skill()
[saudacao | farmaceutico | principio_ativo | genericos | vendedor | recuperador | guardrails]
  ↓  handoff_router()
     • [[HANDOFF:X:ctx]] no texto → vai para skill X (limite: 2 handoffs/turno)
     • senão → "analyst"
  ↓ (caminho analyst)
safety_guard     → curto-circuito se inventory.track_stock OFF. Senão roda em ordem:
                   prescription_guard → price_guard → availability_guard → delivery_guard
                   (cada um gated pela sua capability). Compõe correções.
  ↓
analyst          → LLM Haiku valida qualidade. Retorna approved/retry/escalate.
                   retry volta pro último skill (até analyst_max_retries).
  ↓
save_context     → persiste mensagens no Redis, upsert sessions+cart+conversation_logs
  ↓
END
```

Detalhes importantes:

- **Handoff entre skills**: marker `[[HANDOFF:skill:contexto]]` no fim da resposta. O `handoff_router` roteia para o skill destino que **complementa** (concatena) a resposta anterior. Limite: `_MAX_HANDOFFS_PER_TURN = 2`. Anti-loop via `skill_history`.
- **Escalation humana**: marker `[[ESCALATE]]` ou `escalate=True` no state. Decisão final ocorre **fora** do grafo, no worker (combina com keyword e order_finalized).
- **safety_guard é passthrough** em modo pré-atendimento (`inventory.track_stock` OFF). Importante: balcão não precisa dessas validações (fluxo curto).
- **Skill fallback dinâmico**: tenants com 1 só skill caem direto via `_resolve_fallback` (não pode rotear para skill que não existe no grafo compilado).

---

## 5. Multi-tenancy: como o tenant fica isolado

**Metadata global** (schema `public`):
- `tenants` (id, schema_name, api_key, callback_url, plan)
- `tenant_users` (RBAC: owner/manager/operator/viewer)
- `tenant_channels` (config por canal: WA Cloud, Z-API, Telegram; credenciais cifradas em `tenant_secrets`)
- `tenant_integrations` (broker; uma integração = um endpoint `/hooks/...` com mapping + reply template)
- `tenant_capabilities`, `capability_catalog` — feature flags
- `tenant_llm_config`, `tenant_persona`, `tenant_skill_prompts`, `tenant_skill_examples`, `tenant_sales_config`, `tenant_shipping_rules`, `tenant_order_status_messages`
- `subscriptions`, `plans`, `invoices` — billing
- `audit_events`, `_migrations`

**Schema por tenant** (`tenant_<slug>`): criado pela função `create_tenant_schema()` no onboarding. Contém:
- `sessions`, `cart`, `conversation_logs`, `skills_config`
- `customers` (cadastro + memória de longo prazo: allergies, continuous_meds, preferences, segment, LTV)
- `products`, `product_relations` (cross-sell), `orders`, `order_items`
- `agent_traces` (turno-a-turno para debug)

Toda query do worker faz `SET search_path = {schema_name}, public` antes de tocar tabelas per-tenant. Função utilitária: `db.postgres.tenant_conn`.

**Encryption**: secrets do tenant cifrados via Fernet (`api/services/secrets.py`). Chave em `ENCRYPTION_KEY`.

---

## 6. Sistema de Capabilities (feature flags)

`api/services/capabilities.py` + tabelas `capability_catalog` (catálogo global) + `tenant_capabilities` (override do tenant).

Mecânica:
- Catálogo define `default_enabled`, `default_config`, `min_plan`, `depends_on`, `category`, `requires_secret`
- Tenant pode habilitar/desabilitar e dar `config` override (merge sobre default)
- Cache em Redis 60s, invalidado em writes (`set_enabled`)
- API: `is_enabled(tenant_id, key) -> bool`, `get_config(tenant_id, key) -> dict`
- Decorator `@with_capability("key", default=None)` para gating funcional

**Capabilities atuais** (categoria → key):
- **inventory** — `inventory.track_stock` (default OFF; ON = modo ERP completo)
- **sales** — `sales.stock_check`, `sales.cross_sell`, `sales.pre_handoff_offers`
- **safety** — `availability_guard`, `price_guard`, `prescription_guard`, `delivery_guard` (default ON)
- **delivery** — `delivery.shipping_by_cep`
- **payments** — `payments.pix_asaas`
- **attendance** — `customer_memory`, `interactive_buttons`

Modo de operação do bot bifurca em **dois caminhos** controlados por `inventory.track_stock`:
- **ON** = modo ERP: catálogo, preço real, finaliza pedido, todas as validações
- **OFF** = pré-atendimento: anota itens, confirma dados, transfere para balcão humano (sem catálogo autoritativo)

---

## 7. Canais e Broker

### Channel adapters (`api/channels/*`)

Interface comum em `base.py`:
- `verify_signature(body, headers) -> bool`
- `parse_inbound(payload) -> InboundMessage | None`
- `send_outbound(msg, credentials) -> None`

Adapters concretos:
- `whatsapp_cloud.py` — WhatsApp Cloud API (Meta)
- `whatsapp_zapi.py` — Z-API (provider BR popular)
- `telegram.py` — Telegram Bot API

Registry em `registry.py`. Adicionar canal novo = adapter + entrada no `CHANNEL_REGISTRY`.

### Broker universal (`api/routers/broker.py`)

Permite plugar **qualquer** gateway sem escrever adapter Python. Tenant define no portal:
- `inbound_field_map` — JSONPath-ish para extrair `phone`, `message`, `media_url`, etc. do payload bruto
- `reply_mode` — `response` (síncrono) ou `forward` (POST out no `reply_url`)
- `reply_body_template` — template Mustache-like aplicado sobre `{input, reply, phone, ...}`
- `handoff_config` — provider + credenciais para transferência humana
- `session_config` — `close_keywords`, `close_message`, TTL
- `bundle_enabled` + `bundle_window_seconds` — debounce de mensagens picadas

Eventos brutos persistidos em `broker_raw_events` com `status`, `attempts`, `canonical_payload`, `forward_status_code`, `forward_response` (replayable do portal).

---

## 8. LLM, prompt caching e custo

### Roteamento por papel (`graph_builder._make_llm_factory`)

| Role | Provider/Model default | Override |
|---|---|---|
| `orchestrator` | anthropic / claude-haiku-4-5 | `tenant_llm_config.orchestrator_*` |
| `analyst` | anthropic / claude-haiku-4-5 | `tenant_llm_config.analyst_*` |
| `skill` (default) | anthropic / claude-sonnet-4-6 | `tenant_llm_config.default_skill_*` |
| `<skill_name>` | herda do default | `SkillOverride` por skill em `tenant_skill_overrides` |

Modo `byok`: usa chave do tenant (não cacheia o cliente LangChain). Modo `credits`: usa chave da plataforma (cliente cacheado via `lru_cache(maxsize=32)`).

### Prompt caching

Anthropic exige marker explícito `cache_control: ephemeral`. Implementação em `llm/caching.py::system_message`:

- `content` (estável) → vai antes do marker, é cacheado
- `volatile` (carrinho, status de campos, contexto de handoff) → vai depois do marker, **não invalida** o cache

**Regra de ouro**: qualquer estado por-turno (carrinho, dados do cliente, handoff context) PRECISA ir no `volatile_parts` no skill. Senão o cache miss explode os custos.

### Retry

`llm/retry.py` — tenacity `AsyncRetrying`, 3 tentativas, exponencial 2-10s. Crítico para nodes que ficam idle entre turnos (orchestrator/analyst) — a conexão httpx do `ChatAnthropic` cacheado envelhece e dá `APIConnectionError` na primeira chamada após idle.

---

## 9. Banco de dados

### Migrations

`api/db/migrations/*.sql`, aplicadas por `auto_migrate` na startup (idempotente via `public._migrations`).

**ATENÇÃO**: usa `DATABASE_URL_DIRECT` (Postgres direto, porta 5432). DDL não passa por PgBouncer transaction pooling. Se `DATABASE_URL_DIRECT` não estiver configurado, migrations são **puladas** e precisam ser rodadas manualmente via `scripts/run_migrations.py`.

Ordem importa — arquivos rodam em ordem alfabética. Use prefixo numérico de 3 dígitos. Hoje estamos em `046_*`.

### Tabelas críticas (global)

- `tenants` — id, schema_name, api_key, callback_url, plan, active
- `tenant_users` — RBAC com role hierarchy: viewer < operator < manager < owner
- `tenant_channels` — canais ativos (whatsapp_cloud, whatsapp_zapi, telegram) com `handoff_config`, `session_config`, `handoff_pause_minutes`
- `tenant_integrations` — broker integrations (multi-gateway)
- `tenant_capabilities` + `capability_catalog` — flags
- `subscriptions` + `plans` — billing
- `conversation_state` — pause/close por (tenant, phone)
- `broker_raw_events` — eventos do broker (replayable)

### Tabelas críticas (per-tenant)

- `sessions` — chave `session_key = "{tenant_id}:{phone}"` ou custom
- `cart` — items JSONB, subtotal, stock_mode, sales_attempts
- `conversation_logs` — role+content+skill_used+latency+tokens
- `agent_traces` — debug detalhado por turno (steps, latência, erro)
- `customers` — phone+name+doc+endereço + memória (allergies, continuous_meds, preferences, segment, LTV)
- `products` — catálogo (nome, princípio ativo, preço, estoque, prescription_required)
- `product_relations` — cross-sell (produto A → produto B com weight)
- `orders` + `order_items` — pedidos finalizados

---

## 10. Onde costuma quebrar (lições do código)

Notas extraídas dos comentários no código — essencial para evitar regressões:

1. **`DATABASE_URL_DIRECT` ausente** → migrations não rodam. Sintoma: tabela nova não existe, código quebra silenciosamente.
2. **Connection idle no Anthropic** → `APIConnectionError` na primeira chamada após idle. **Solução já implementada**: `llm_retry()` em orchestrator e analyst. NÃO remova.
3. **System message não-consecutivo no Anthropic** → "Received multiple non-consecutive system messages". Em loops force-call (`vendedor.py`), use `HumanMessage` e não `SystemMessage`.
4. **Cache miss por estado volátil** → carrinho, status de campos, customer memory PRECISAM ir em `volatile_prompt` do `_build_messages`. Caso contrário, cada turno é cache miss.
5. **Pedido alucinado** → modelo afirma "pedido confirmado" sem chamar a tool. Trava em `farmaceutico.py` (handoff obrigatório p/ vendedor) e em `vendedor.py` modo pré-atendimento (force-call de `anotar_pedido_balcao`).
6. **Tenant com 1 skill** → orchestrator pode escolher skill que não existe no grafo. **Solução**: fast-path `single_skill` no `orchestrator.py` + `_resolve_fallback` no `router.py`.
7. **PgBouncer transaction pool** → não dá pra rodar DDL nem multi-statement. Use `DATABASE_URL_DIRECT`.
8. **`asyncpg` JSONB pode vir como str** — sempre normalize (`_ensure_dict`, `json.loads` defensivo).
9. **Windows event loop** — `main.py` força `WindowsSelectorEventLoopPolicy` pra asyncpg funcionar.
10. **Bundling debounce** — task agendada `desiste` se chegou outra mensagem depois dela (compara `last_seen` em Redis com `scheduled_for_ts`).

---

## 11. Diagrama de dependências (módulos)

```
              ┌──────────────┐
              │  api/main.py │
              └──────┬───────┘
                     │
        ┌────────────┴─────────────┐
        ▼                          ▼
  ┌──────────┐              ┌────────────┐
  │ routers/ │              │ middleware/│
  └────┬─────┘              └──────┬─────┘
       │                           │
       ▼                           ▼
  ┌──────────┐              ┌────────────┐
  │ services/│◄─────────────┤ db/        │
  └────┬─────┘              └────────────┘
       │                           ▲
       ▼                           │
  ┌──────────┐              ┌────────────┐
  │ workers/ │─────────────►│ db/redis_* │
  └────┬─────┘              └────────────┘
       │
       ▼
  ┌──────────────────────────────────────┐
  │  agents/   (graph + nodes + tools)   │◄──── llm/ (providers, caching, retry)
  └──────────────────────────────────────┘
                  │
                  ▼
              services/ (capabilities, persona, sales_config, guards, ...)
```

Regra: **tudo flui em direção ao db/llm**. Skills/nodes podem importar `services/`, mas `services/` não importa de `agents/`.

---

## 12. Como adicionar coisas (checklist)

### Novo skill

1. `agents/nodes/skills/<nome>.py` — implementa `async def <nome>_node(state, llm_factory)`. Use `run_skill` do `_base.py` se for stateless ou copie o padrão `vendedor.py` se precisar de tools/estado complexo.
2. `agents/router.py` — adicionar em `_KNOWN_SKILLS`.
3. `agents/graph_builder.py` — adicionar no `all_skill_nodes` (e em `routing_map`/`handoff_map` herdam automaticamente).
4. Migration SQL para `skill_catalog` (display_name, plan_min, channel_compat, tools_json).
5. (Opcional) Prompt customizado: `tenant_skill_prompts` permite override por tenant via portal.
6. `frontend/src/pages/PortalSkills.tsx` mostra automaticamente (lê do catálogo).

### Nova capability

1. Migration: INSERT em `capability_catalog` (key, category, default_enabled, default_config, etc).
2. No código que precisa do gating: `await capabilities.is_enabled(tenant_id, "minha.key")`.
3. Frontend mostra em `PortalRecursos.tsx` automaticamente.

### Novo canal

1. Adapter em `api/channels/<canal>.py` implementando `ChannelAdapter`.
2. Registrar em `api/channels/registry.py::CHANNEL_REGISTRY`.
3. Migration para adicionar valor ao `CHECK (channel_type IN (...))` em `tenant_channels`.

### Nova tool de agente

1. `agents/tools/<arquivo>.py` — função factory `make_<tool>(...)` retornando `@tool` decorated.
2. Bind no skill que vai usar (`tools=[make_minha_tool(...)]` no `run_skill` ou no array de tools do `vendedor.py`).
3. Documentar no docstring da tool (LLM lê isso pra decidir quando chamar).

### Nova migration

1. `api/db/migrations/<NNN>_descricao.sql` (NNN sequencial, 3 dígitos).
2. Idempotente: `CREATE TABLE IF NOT EXISTS`, `ALTER ... ADD COLUMN IF NOT EXISTS`, `INSERT ... ON CONFLICT DO UPDATE`.
3. Se afeta schema de tenant, atualize a função `create_tenant_schema()` em `001_initial.sql` E rode loop em todos os tenants existentes (padrão das migrations 004+).

---

## 13. Pontos de atenção para refatorar/dividir

O projeto cresceu rápido. Hotspots para considerar quebrar:

- **`workers/celery_app.py` (1217 linhas)** — mistura task definitions, callback delivery, handoff dispatcher, pre-handoff offers, order summary. **Sugestão**: extrair `worker_pipeline.py` com etapas explícitas (pré-grafo, pós-grafo, handoff, finalize).
- **`agents/nodes/skills/vendedor.py` (925 linhas)** — bifurca em 2 modos (normal/pré-atendimento) inline. **Sugestão**: dividir em `vendedor_normal.py` + `vendedor_preattendimento.py`, compartilhar via mixin/helpers.
- **`api/routers/broker.py` (1178 linhas)** — concentra integrations, mappings, outbound targets, replay. **Sugestão**: separar em sub-routers (`broker/integrations.py`, `broker/mappings.py`, `broker/raw_events.py`).
- **`api/services/inventory.py` (667 linhas)** — buscas, sync XLSX/Sheets, cross-sell helpers.

---

## 14. Referências

- Ver `docs/PRD.md` — visão de produto, personas, features
- Ver `docs/specs/00-overview.md` — índice das specs por módulo
- Ver `CLAUDE.md` — instruções operacionais para Claude Code
