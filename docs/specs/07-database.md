# SPEC 07 — Database

**Propósito**: convenções de schema, migrations idempotentes, e mapa das tabelas.

## Onde vive

```
api/db/
├── postgres.py           # asyncpg pool, get_db_conn, tenant_conn
├── redis_client.py       # init/get/close async redis
├── migrate.py            # auto_migrate (idempotente via _migrations)
└── migrations/           # NNN_descricao.sql
```

Scripts: `scripts/run_migrations.py` (executa manualmente quando direct URL não disponível).

## Padrões obrigatórios

### Migration

- Prefixo `NNN_` (3 dígitos sequenciais, hoje em 046).
- Nome em snake_case descrevendo a mudança.
- **Idempotente**: `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`, `CREATE INDEX IF NOT EXISTS`, `INSERT ... ON CONFLICT DO UPDATE`/`DO NOTHING`.
- Para tabelas globais: `public.<nome>` explícito.
- Para schema de tenant: alterar a função `create_tenant_schema()` no `001_initial.sql` **+** criar função `add_<feature>_to_schema(p_schema TEXT)` que aplica em schema existente **+** loop em todos os tenants existentes.

### Pattern de alteração em todos os tenants existentes

Padrão usado nas migrations 020, 010, 023, 024:

```sql
CREATE OR REPLACE FUNCTION add_my_feature_to_schema(p_schema TEXT) RETURNS void AS $$
BEGIN
    EXECUTE format($t$
        ALTER TABLE %I.my_table ADD COLUMN IF NOT EXISTS new_col TEXT
    $t$, p_schema);
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE rec RECORD;
BEGIN
    FOR rec IN SELECT schema_name FROM public.tenants LOOP
        PERFORM add_my_feature_to_schema(rec.schema_name);
    END LOOP;
END $$;

-- E atualize create_tenant_schema() pra novos tenants criados depois.
```

## Conexões

### Postgres
- **Pool via PgBouncer transaction mode** (DATABASE_URL) — para queries do app (workers, API)
- **Conexão direta** (DATABASE_URL_DIRECT) — para DDL (migrations), transações longas, multi-statement
- `db.postgres.get_db_conn()` → async context manager (asyncpg)
- `db.postgres.tenant_conn(schema_name)` → conn com `SET search_path` já feito

### Redis
- `db.redis_client.get_redis()` → instância async
- Keys padronizadas:
  - `hist:{session_id}` — histórico da conversa (JSON, TTL = session_ttl)
  - `cap:{tenant_id}` — capabilities state (TTL 60s)
  - `convstate:{tenant_id}:{phone}` — pause/close (TTL 30s)
  - `usage:{tenant_id}:msgs:{YYYY-MM}` — counter mensal (TTL 40 dias)
  - `bundle:{integration_id}:{phone}` — buffer de bundling (List + `:last_seen`)
  - `ttl:tenant:{tenant_id}` — cache do session_ttl_minutes (TTL 5 min)

## Schema global (`public.*`)

### Foundation
| Tabela | Propósito |
|---|---|
| `tenants` | id, name, api_key, callback_url, plan, schema_name, active, session_ttl_minutes |
| `plans` | basic/pro/enterprise + features/limits JSONB |
| `tenant_users` | RBAC: email, password_hash, role, last_login_at, mfa_secret |
| `tenant_secrets` | Fernet-encrypted (zapi tokens, asaas_key, etc.) |
| `audit_events` | ações administrativas (tenant_id, actor, action, target, meta) |
| `_migrations` | controle de aplicação (filename, applied_at) |

### Canais + integrações
| Tabela | Propósito |
|---|---|
| `tenant_channels` | WA Cloud, Z-API, Telegram (credentials_ref, handoff_config, session_config) |
| `tenant_integrations` | Broker universal (slug, mapping, reply_template, handoff_config) |
| `broker_raw_events` | Raw events do broker (replayable) |

### Billing
| Tabela | Propósito |
|---|---|
| `subscriptions` | UNIQUE(tenant_id), status, provider (stripe/asaas/manual) |
| `invoices` | Histórico de faturas |

### Bot config
| Tabela | Propósito |
|---|---|
| `capability_catalog` | Catálogo global de features |
| `tenant_capabilities` | Override por tenant (enabled, config) |
| `tenant_llm_config` | LLM por papel + overrides |
| `tenant_persona` | agent_name, tone, language, persona_bio, playbook, custom_instructions |
| `tenant_skill_prompts` | system_prompt (override) + extra_instructions (append) por skill |
| `tenant_skill_examples` | Few-shot examples por skill (opcional) |
| `tenant_sales_config` | required_fields, max_attempts, fallback_message, checkout_flow_mode, accepted_payment_methods |
| `tenant_shipping_rules` | CEP ranges + valor + prazo (delivery.shipping_by_cep) |
| `tenant_order_status_messages` | Templates de notificação de status |
| `skill_catalog` | Catálogo global de skills (display_name, plan_min, channel_compat, tools_json) |

### Estado runtime
| Tabela | Propósito |
|---|---|
| `conversation_state` | Por (tenant, phone): ai_paused, paused_until, closed_at |
| `medicamentos_anvisa` + `bula_secoes` | Base regulatória compartilhada |

## Schema per-tenant (`tenant_<slug>.*`)

Criado em `create_tenant_schema()`:

| Tabela | Notas |
|---|---|
| `sessions` | id, phone, session_key UNIQUE, customer_profile, turn_count |
| `cart` | session_key UNIQUE, items JSONB, subtotal, stock_mode, sales_attempts |
| `skills_config` | skill_name PK, ativo, llm_model, llm_provider, prompt_version, config_json |
| `conversation_logs` | session_key, phone, role, content, skill_used, llm_model, latency_ms |
| `agent_traces` | session_key, phone, message_in, final_state, steps, latency_ms, error |
| `usage_metrics` | month PK, conversations, tokens_total, cost_usd |
| `customers` | phone UNIQUE, dados básicos + memória (allergies, continuous_meds, preferences, segment, total_orders, total_spent, ltv, last_purchase_at) |
| `products` | nome, principio_ativo, preco, estoque, prescription_required, source (manual/csv/xlsx/google_sheets) |
| `product_relations` | source_product_id, target_product_id, weight, kind (cross_sell/substitute) |
| `orders` | numero, status (pending/aguardando_balcao/confirmed/processing/shipped/delivered/cancelled), customer_phone, subtotal, total, observacoes |
| `order_items` | order_id, product_name, qty, unit_price |

## Migrations notáveis (referência rápida)

| # | O quê |
|---|---|
| 001 | Foundation: tenants, plans, create_tenant_schema |
| 002 | tenant_users + RBAC |
| 003 | SaaS foundation (tenant_secrets, audit, subscriptions, invoices, tenant_channels) |
| 004 | tenant_llm_config + upgrade tenants existentes |
| 005 | skill saudacao (adiciona ao catálogo) |
| 006 | persona_and_prompts (tenant_persona + tenant_skill_prompts) |
| 007 | inventory_sync_v2 (sources XLSX/Sheets) |
| 008 | skill_examples (few-shot) |
| 009 | customers_v2 (memória de longo prazo) |
| 010 | sales_config (required_fields, max_attempts) |
| 011 | order_status_messages (notificações) |
| 012 | conversation_playbook na persona |
| 013-018 | webhook broker (integrations, mappings, bundling, skip_rules) |
| 019 | activate_default_skills (saudacao, farmaceutico ON em todos os tenants) |
| 020 | agent_traces |
| 021 | handoff_config em tenant_channels |
| 022 | capabilities |
| 023 | customer_memory (allergies, continuous_meds, preferences) |
| 024 | relations_and_shipping (product_relations + tenant_shipping_rules) |
| 025 | recovery_and_payments (abandoned_cart + asaas) |
| 026 | preattendimento (modo balcão) |
| 027 | channel_handoff |
| 028 | conversation_state (pause/close) |
| 030 | orders_balcao_fix (status aguardando_balcao) |
| 031 | agent_traces unwrap steps |
| 032-034 | medicamentos_anvisa + bula_secoes + unaccent index |
| 035-037 | offers (com mídia) |
| 038-039 | inventory.source + track_stock capability |
| 040 | propagate cart sales_attempts |
| 041 | checkout_flow_mode (rápido vs completo) |
| 042 | accepted_payment_methods |
| 043 | session_close_keywords |
| 044 | order_summary capability + category safety |
| 045-046 | safety guards v2 (availability/price/prescription/delivery) |

## Convenções de nomes

- Tabelas: snake_case plural (`tenants`, `tenant_channels`, NÃO `tenant`)
- Colunas: snake_case singular (`tenant_id`, `created_at`)
- PK: `id UUID DEFAULT gen_random_uuid()` em tabelas globais; `serial`/`bigserial` em logs (`audit_events`, `invoices`)
- FK: `<entity>_id` (ex.: `tenant_id`)
- Timestamps: `created_at`, `updated_at` SEMPRE TIMESTAMPTZ DEFAULT NOW()
- JSONB para configs flexíveis; `_json` no nome **só** quando ambiguidade (geralmente sem suffix)
- ENUM via CHECK constraint TEXT, não tipo nativo (mais flexível para migrations)

## Performance / índices padrão

- Toda tabela com `tenant_id` tem índice `(tenant_id, created_at DESC)` para queries de logs
- `conversation_logs.session_key` indexado
- `agent_traces.session_key` indexado
- `customers.phone` UNIQUE per-schema
- `products.nome` com `unaccent`/`trgm` para busca fuzzy (ver migration 034)
- `medicamentos_anvisa` com GIN unaccent (migration 034)

## Regressões conhecidas / "Não fazer"

- **Não usar transaction pooling (PgBouncer) para DDL.** Use `DATABASE_URL_DIRECT`. Sintoma: "connection was closed in the middle of operation" durante migrations multi-statement.
- **Não esquecer de atualizar `create_tenant_schema()` E criar função `add_<feat>_to_schema()` E rodar em tenants existentes.** Se esquecer uma das três, tenants antigos ficam sem a tabela/coluna.
- **Não fazer `DROP TABLE` em migration sem rollback plan.** Estamos sem mecanismo de migration reversa hoje.
- **Não usar UUID v1** — sempre `gen_random_uuid()` (pgcrypto).
- **Não confiar em `CREATE INDEX CONCURRENTLY` em migration** — só fora de transação. Use migration separada se precisar.
- **Não fazer `SELECT *` em endpoint público.** Quebra silenciosamente quando a tabela ganha coluna nova com dado sensível.
- **Não criar coluna BIGSERIAL em tabela per-tenant** — replicar a função `create_tenant_schema` com sequences é dor; prefira UUID + `created_at` pra ordenar.
