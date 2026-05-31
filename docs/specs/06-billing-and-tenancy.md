# SPEC 06 — Billing + Multi-tenancy

**Propósito**: isolar tenants no nível de schema, enforce limites mensais e gerenciar subscriptions/invoices via Stripe/Asaas.

## Onde vive

```
api/db/migrations/001_initial.sql       # tenants, plans, create_tenant_schema()
api/db/migrations/003_saas_foundation.sql # tenant_users, subscriptions, invoices, audit
api/services/billing.py                  # usage counters + provider interface + Stripe/Asaas
api/services/secrets.py                  # Fernet encryption helpers
api/routers/billing.py                   # admin + portal billing
api/routers/onboarding.py                # /signup self-service
api/routers/tenants.py                   # admin CRUD
api/routers/tenant_auth.py + auth.py     # login (admin) + tenant login
api/security.py                          # JWT, password hash, RBAC
api/middleware/usage.py                  # enforce limite mensal
api/dependencies.py                      # resolve_tenant via X-Api-Key
```

## Multi-tenancy: 3 níveis de isolamento

### Nível 1 — Schema por tenant

Cada tenant ganha um `schema_name` (ex.: `tenant_farmacia_central`). Criação via `create_tenant_schema(schema_name)` (PL/pgSQL em `001_initial.sql`).

Worker faz `SET search_path = {schema_name}, public` em toda conexão antes de tocar tabelas per-tenant. Utility: `db.postgres.tenant_conn`.

**Tabelas per-tenant**: sessions, cart, conversation_logs, skills_config, customers, products, product_relations, orders, order_items, agent_traces, etc.

### Nível 2 — Linha global com `tenant_id` FK

Tabelas que vivem em `public` mas pertencem a um tenant: `tenant_channels`, `tenant_integrations`, `tenant_capabilities`, `tenant_llm_config`, `tenant_persona`, `tenant_skill_prompts`, `tenant_skill_examples`, `tenant_sales_config`, `tenant_shipping_rules`, `tenant_order_status_messages`, `tenant_secrets`, `subscriptions`, `invoices`, `conversation_state`, `broker_raw_events`.

Todas têm FK `tenant_id REFERENCES public.tenants(id) ON DELETE CASCADE`.

### Nível 3 — Encryption de segredos

`tenant_secrets.value_enc BYTEA` cifrado com Fernet usando `ENCRYPTION_KEY`. Helpers em `api/services/secrets.py`:

```python
async def store_secret(tenant_id, key, value) -> None
async def get_secret(tenant_id, key) -> str | None
```

Chaves típicas: `zapi_token`, `zapi_client_token`, `wa_cloud_access_token`, `asaas_api_key`.

## Planos e RBAC

### Planos

```sql
'basic'      R$  97  500  msgs/mês  ['farmaceutico']
'pro'        R$ 297 2000 msgs/mês   ['farmaceutico','principio_ativo','genericos','vendedor']
'enterprise' R$ 697 unlimited       all skills
```

Limites adicionais em `plans.limits` JSONB: `tokens_month`, `products_max`, `customers_max`, `users_max`.

### RBAC (tenant_users)

```sql
role IN ('viewer','operator','manager','owner')
```

Hierarquia em `api/security.py::ROLE_HIERARCHY`. `require_role(min_role)` factory para deps de endpoint:

```python
@router.post("/...")
async def fn(user: Annotated[TenantUserContext, Depends(require_role("manager"))]):
```

Convenções:
- `viewer` — só leitura
- `operator` — pode operar (toggle skills, responder via console)
- `manager` — pode editar persona, config, capabilities
- `owner` — full access + billing + delete

## Auth

### Admin (Anthropic-side)
- Email único em `settings.admin_email`
- Senha hash bcrypt em `settings.admin_password_hash`
- Login: `POST /admin/login` → JWT
- Dep: `require_admin`

### Tenant user
- Tabela `tenant_users` (email único global + tenant_id)
- Login: `POST /portal/auth/login` → JWT com `tenant_id` + `tenant_role`
- Dep: `require_tenant_user` ou `require_role(min)`

### Webhook
- `X-Api-Key` header **OU** `webhook_token` na URL (= `tenants.api_key`)
- Dep: `resolve_tenant`

JWT config: `jwt_algorithm=HS256`, `jwt_access_token_expire_minutes=60`. Sub = email, claims = role + tenant_id + tenant_role + name.

## Subscription lifecycle

```
trialing → active → past_due → canceled
            ↓
          paused
```

| Status | Comportamento do middleware |
|---|---|
| `trialing` | Permite, sem checagem de limite (ou até `trial_ends_at`) |
| `active` | Permite, valida limite mensal |
| `past_due` | **Bloqueia** (402 Payment Required) |
| `canceled` | **Bloqueia** |
| `paused` | **Bloqueia** |

Webhooks dos providers atualizam status:
- Stripe: `POST /webhooks/stripe` (HMAC valid)
- Asaas: `POST /webhooks/asaas` 

## Usage enforcement

### Counter
- Redis: `usage:{tenant_id}:msgs:{YYYY-MM}` (INCR, TTL 40 dias)
- Lido em `check_usage_allowed(tenant_id)` → compara com `plans.limits.msgs_month`
- Reset implícito mensal (chave nova a cada mês)

### Middleware
`UsageEnforcementMiddleware` intercepta `/webhook/*`:
1. Extrai `tenant_id` da URL.
2. `check_usage_allowed` → 402 se bloqueado.
3. `increment_usage(tenant_id, "msgs")` em caso de allow.

**Não intercepta** outras rotas. Outros recursos (products_max, customers_max) enforced **no momento da criação** (endpoint que cria valida).

## Onboarding self-service

`POST /signup` (`api/routers/onboarding.py`):

1. Valida payload (pharmacy_name, owner_email, owner_password, callback_url, plan)
2. Gera `api_key = secrets.token_urlsafe(32)`
3. Gera `schema_name = "tenant_<slug>"` do nome
4. Hash bcrypt senha do owner
5. Transação:
   - INSERT em `tenants`
   - INSERT em `tenant_users` (role=owner)
   - `SELECT create_tenant_schema($schema)` (cria as tabelas core)
   - `SELECT add_agent_traces_to_schema($schema)` (migration 020)
   - `SELECT add_sales_attempts_to_cart($schema)` (migration 010)
   - `SELECT create_tenant_schema_memory_ext($schema)` (migration 023)
   - `SELECT create_tenant_schema_relations_ext($schema)` (migration 024)
6. (Async) `send_welcome` email via Resend
7. Cria JWT, retorna `{tenant_id, schema_name, api_key, access_token}`

Habilitar via `settings.allow_signup=true`. Em produção pode-se desabilitar + abrir só whitelist.

## Invariantes

1. **`tenant_id` UUID gerado pelo Postgres** (`gen_random_uuid`). Nunca aceitar id do cliente.
2. **`schema_name` único** + lowercase + prefixo `tenant_` (regex `[a-z_][a-z0-9_]*`).
3. **`api_key` único + URL-safe** (32 bytes via `secrets.token_urlsafe`).
4. **CASCADE delete** — apagar tenant remove tudo (`ON DELETE CASCADE` em todas as FK).
5. **Schema do tenant NÃO é dropado** quando tenant é deletado (precaução; rollback manual). Considere job de cleanup separado.
6. **`audit_events.actor_id`** sempre = email do usuário OU "system" pra ações automáticas.
7. **Senha bcrypt cost default** (12). Não baixar abaixo de 10.

## Pontos de extensão

### Novo provider de billing

1. Implementar `BillingProvider` ABC em `services/billing.py`.
2. Factory `get_billing_provider(name)` resolve por env/config.
3. Webhook endpoint próprio em `api/routers/payments_webhook.py`.

### Novo papel (RBAC)

1. Adicionar em `ROLE_HIERARCHY` (`api/security.py`).
2. Migration alterando CHECK em `tenant_users.role`.
3. Considerar matriz de permissões em `services/audit.py` ou similar.

### Novo limite no plano

1. Adicionar key em `plans.limits` JSONB (sem migration de schema — JSONB é flexível).
2. Enforce no endpoint que cria o recurso (ex.: criar produto valida `products_max`).
3. UI: `PortalBilling.tsx` mostra contagem atual vs limite.

## Regressões conhecidas / "Não fazer"

- **Não criar índice apenas com `tenant_id`** em tabelas globais — use composto `(tenant_id, created_at DESC)` para queries de logs/audit.
- **Não armazenar credenciais em `tenant_channels.credentials_ref` direto** — sempre via `tenant_secrets` (cifrado).
- **Não confiar no `email` do JWT** para identificar tenant em endpoints multi-tenant — sempre pelo `tenant_id` do claim.
- **Não esquecer de `SET search_path` ANTES de query per-tenant**. Sem isso vai bater em `public` (que pode não ter a tabela). Pattern correto:
  ```python
  async with get_db_conn() as conn:
      await conn.execute(f"SET search_path = {schema_name}, public")
      await conn.fetch("SELECT * FROM products WHERE ...")
  ```
- **Não usar `f-string` direto em SQL** — exceção: `SET search_path` (schema_name é validado em onboarding com regex `[a-z_][a-z0-9_]*`; outras execs do tipo são DDL controlado).
- **Não permitir `email` duplicado em `tenant_users` GLOBAL** — UNIQUE no `email`. Um usuário não pode pertencer a 2 tenants com mesmo email (limitação atual; se virar requirement, refator).

## Configuração crítica (env)

```bash
DATABASE_URL=postgresql://...@pgbouncer:5432/saas_farmacia
DATABASE_URL_DIRECT=postgresql://...@postgres:5432/saas_farmacia
REDIS_URL=redis://redis:6379/0
RABBITMQ_URL=amqp://...@rabbitmq:5672/

SECRET_KEY=<jwt signing>             # 64 chars random
ENCRYPTION_KEY=<Fernet key>          # python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

ADMIN_EMAIL=admin@farmacia.io
ADMIN_PASSWORD_HASH=<bcrypt hash>

ANTHROPIC_API_KEY=...
GOOGLE_API_KEY=...
OPENAI_API_KEY=...

STRIPE_API_KEY=...
STRIPE_WEBHOOK_SECRET=...
ASAAS_API_KEY=...
RESEND_API_KEY=...

CORS_ORIGINS=https://app.farmacia.io,https://...
PUBLIC_API_URL=https://api.farmacia.io

ALLOW_SIGNUP=false                    # true em ambiente de desenvolvimento
```
