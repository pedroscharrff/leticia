# SPEC 04 — Capabilities (feature flags)

**Propósito**: ligar/desligar comportamentos do bot por tenant sem deploy, com config override sobre default.

## Onde vive

```
api/services/capabilities.py    # is_enabled, get_config, set_enabled, with_capability decorator
api/routers/capabilities.py     # admin (catalog CRUD) + portal (toggle/config)
api/db/migrations/022_capabilities.sql + variações por capability
```

Tabelas: `public.capability_catalog`, `public.tenant_capabilities`.

## Contrato público

```python
async def is_enabled(tenant_id: str | None, capability_key: str) -> bool
async def get_config(tenant_id: str | None, capability_key: str) -> dict
async def set_enabled(tenant_id, key, enabled, config_override=None) -> dict

# decorator (raro mas útil):
@with_capability("sales.cross_sell", default=None)
async def fn(tenant_id, ...): ...
```

`tenant_id=None` → sempre `False` / `{}` (fail closed).

## Modelo

### `capability_catalog` (global)

```sql
key             TEXT PRIMARY KEY     -- "sales.cross_sell"
name            TEXT                  -- "Cross-sell"
category        TEXT                  -- "sales" | "safety" | "inventory" | ...
short_desc      TEXT
long_desc       TEXT                  -- markdown rendered no portal
impact_label    TEXT                  -- "Aumenta ticket médio."
min_plan        TEXT                  -- "basic" | "pro" | "enterprise"
depends_on      JSONB                 -- ["inventory.track_stock", ...]
requires_secret JSONB                 -- chaves obrigatórias em tenant_secrets
config_schema   JSONB                 -- shape esperado (informational)
default_config  JSONB                 -- valores default
default_enabled BOOLEAN
status          TEXT                  -- "ga" | "beta" | "deprecated"
icon            TEXT                  -- lucide icon name
sort_order      INT
```

### `tenant_capabilities` (override)

```sql
tenant_id        UUID
capability_key   TEXT
enabled          BOOLEAN              -- override do default_enabled
config           JSONB                -- merge sobre default_config
PRIMARY KEY (tenant_id, capability_key)
```

`tenant_capabilities` é "esparso" — só linhas que o tenant **alterou** existem. Tudo o resto herda do catálogo.

## Invariantes

1. **Fail closed**: erro no service de capabilities → comportamento default seguro (geralmente OFF, exceto safety/* que são ON).
2. **Cache em Redis 60s** por tenant (`cap:{tenant_id}`). Invalidado em `set_enabled`. NÃO bypassar — bot consulta capability em todo turno.
3. **Merge de config**: `final = {**default_config, **tenant_config}` — shallow merge. Configs aninhadas precisam vir inteiras no override.
4. **Min plan respeitado**: API/portal bloqueiam ativação de capability cujo `min_plan` é superior ao plano do tenant. Service não enforça em runtime (assume input correto).
5. **`depends_on` informacional**: portal alerta, mas não bloqueia ativação (operador pode forçar).

## Fluxos críticos

### Bot consulta capability (hot path)

```python
# Em qualquer skill/tool/node:
from services import capabilities as cap_svc
if await cap_svc.is_enabled(tenant_id, "sales.cross_sell"):
    cfg = await cap_svc.get_config(tenant_id, "sales.cross_sell")
    max_sug = int(cfg.get("max_suggestions_per_turn", 1))
    # ...
```

Sequência:
1. `_load_tenant_state(tenant_id)` → tenta Redis (`cap:{tenant_id}`).
2. Cache miss → JOIN `capability_catalog` ⟕ `tenant_capabilities`.
3. Monta dict `{key: {enabled, config}}` mesclando tenant override sobre default.
4. Best-effort write em Redis com TTL 60s.
5. `is_enabled` retorna `state[key]["enabled"]`.

### Operador altera no portal

```
POST /portal/capabilities/{key}/toggle  → set_enabled(tenant, key, enabled, config_override)
                                       → invalidate_cache(tenant)
                                       → audit_event(action="capability.toggle")
```

## Catálogo atual (referência)

| Key | Default | Plano min | O que faz |
|---|---|---|---|
| `inventory.track_stock` | OFF | basic | Liga modo ERP (vs pré-atendimento) |
| `sales.stock_check` | ON | basic | Vendedor consulta estoque real |
| `sales.cross_sell` | OFF | pro | Sugere complementos via product_relations |
| `sales.pre_handoff_offers` | OFF | basic | Manda ofertas vigentes ANTES de transferir |
| `sales.pharmacist_validation` | OFF | basic | Pré-atendimento: medicamento nomeado vai ao farmacêutico p/ validar na bula antes de anotar (requer farmacêutico ativo). Config `not_found_message` (editável): frase enviada ao cliente quando o remédio não está no bulário da ANVISA — pede dosagem/apresentação em vez de inventar (mig 056) |
| `safety.availability_guard` | ON | basic | Detecta produto inventado pelo LLM |
| `safety.price_guard` | ON | basic | Cruza preço citado com catálogo |
| `safety.prescription_guard` | ON | basic | Bloqueia "não precisa receita" sobre tarja |
| `safety.delivery_guard` | ON | basic | Pega "frete grátis" sem regra configurada |
| `delivery.shipping_by_cep` | OFF | pro | Cálculo de frete via `tenant_shipping_rules` |
| `payments.pix_asaas` | OFF | pro | Link PIX no chat via Asaas |
| `attendance.customer_memory` | OFF | pro | Memória de longo prazo (allergies, continuous_meds, preferences) |
| `attendance.interactive_buttons` | OFF | pro | Botões interativos (WhatsApp Cloud) |
| `attendance.time_aware_greeting` | OFF | basic | Injeta hora/período (bloco volátil) p/ saudação correta (mig 061) |
| `attendance.medication_name_suggestion` | **ON** | basic | "Você quis dizer…?": quando `consultar_bula` não acha o medicamento (provável typo), binda `sugerir_nome_medicamento` em `farmaceutico`/`principio_ativo` para OFERECER correções. Pipeline fuzzy nas bases reais → Haiku → web search nativo, sempre verificado contra a ANVISA. Config: `enable_web_search` (bool), `max_candidates` (1–5). Nunca auto-corrige — confirmação do cliente obrigatória (mig 069) |
| `intelligence.sentiment_analysis` | OFF | pro | Nó `sentiment_analyzer` classifica o sentimento antes do orchestrator; injeta diretiva volátil de adaptação nos skills e, opcionalmente, escala para humano (reusa `escalate`). Config: **`provider_model`** (dropdown único `"provider\|model"` — enum de pares válidos garante que provider+model NUNCA fiquem incompatíveis; catálogo espelha `llm/providers.py`), `labels`, `analyst_instructions`, `escalate_on_frustration`, `escalation_threshold`, `escalation_labels`, `transfer_message` (texto opcional usado SÓ quando a transferência foi disparada por sentimento — outros gatilhos seguem o `transfer_message` do `handoff_config`), `history_turns` (mig 063). O par escolhido passa pelo `llm_factory` → respeita BYOK do tenant. A origem da escalação é marcada via `state["escalate_reason"]="sentiment"` e o worker (`_resolve_sentiment_transfer_message`) escolhe a mensagem certa. |

## Pontos de extensão

### Nova capability

1. Migration `NNN_my_capability.sql`:
   ```sql
   INSERT INTO public.capability_catalog (key, name, category, short_desc, long_desc,
     impact_label, min_plan, depends_on, requires_secret, config_schema,
     default_config, default_enabled, status, icon, sort_order)
   VALUES ('namespace.my_key', ..., 'safety', ...)
   ON CONFLICT (key) DO UPDATE SET ...
   ```
2. No código: `await capabilities.is_enabled(tenant_id, "namespace.my_key")` onde precisa.
3. Frontend `PortalRecursos.tsx` renderiza automaticamente do catálogo.

### Mudar default

Migration que faz `UPDATE capability_catalog SET default_enabled = TRUE WHERE key = '...'`. Tenants sem override imediatamente herdam.

### Adicionar campo de config editável (aparece no modal de Recursos)

O modal "Saber mais e configurar" (`PortalRecursos.tsx` → `SchemaForm`) renderiza `config_schema.properties`. Cada chave vira um campo (boolean→toggle, integer/number→input numérico, enum→select, string→input de texto; `"format":"textarea"`→textarea). O valor inicial vem de `default_config` (mesclado com override do tenant via `get_config`).

⚠️ **Use `jsonb_build_object` para setar `config_schema`, NUNCA `jsonb_set` com path aninhado.** A coluna nasce `NOT NULL DEFAULT '{}'` — `jsonb_set(config_schema, '{properties,x}', ...)` é NO-OP silencioso quando `config_schema` é `{}` (o pai `properties` não existe e o `jsonb_set` só cria o elemento final). `COALESCE(config_schema, '{...}')` também não salva (nunca é NULL). Mordeu nas migs 051 e 057. Padrão correto:

```sql
UPDATE public.capability_catalog
   SET config_schema = jsonb_build_object(
         'type', 'object',
         'properties', jsonb_build_object(
           'meu_campo', jsonb_build_object(
             'type', 'string', 'title', '...', 'description', '...',
             'format', 'textarea', 'default', '...'
           )
         )
       )
 WHERE key = 'namespace.my_key';

-- default_config pode usar || (funciona em '{}'):
UPDATE public.capability_catalog
   SET default_config = COALESCE(default_config, '{}'::jsonb)
                        || jsonb_build_object('meu_campo', '...')
 WHERE key = 'namespace.my_key' AND NOT (default_config ? 'meu_campo');
```

No código, leia com `cfg = await cap_svc.get_config(tenant_id, "namespace.my_key")` e use `cfg.get("meu_campo")`.

### Forçar gate em endpoint REST

```python
from services.capabilities import require_capability  # ver código

@router.post("/...")
async def fn(user: TenantUserContext = Depends(require_tenant_user)):
    await require_capability(user.tenant_id, "minha.key")  # 403 se OFF
    ...
```

## Regressões conhecidas / "Não fazer"

- **Não esquecer `await cap_svc.invalidate_cache(tenant_id)` em writes diretos** (admin override, scripts batch). Sem isso, mudança não aparece até TTL expirar.
- **Não usar `is_enabled` em loop apertado sem cache local por turno** — embora seja cacheado em Redis, ainda há roundtrip. Para múltiplas checagens no mesmo turno, leia uma vez e armazene local (pattern usado em `vendedor.py`).
- **Não confundir `enabled=False` (operador desligou) com "capability não existe"** (chave fora do catálogo). Ambos retornam False, mas tratamento de erro/UI deve diferenciar via `_load_tenant_state` retornar `None` vs `{enabled:False}`.
- **Não jogar config sensível em `default_config`** — ele aparece pra todos os tenants. Use `requires_secret` + `tenant_secrets`.
- **Não setar `config_schema` aninhado com `jsonb_set`** (vira NO-OP em `{}`). Use `jsonb_build_object` montando o objeto inteiro — ver "Adicionar campo de config editável". Sintoma do bug: campo não aparece no modal de Recursos mesmo com a migration "aplicada".
- **Não puxar `category` do código** — pegue do catálogo. Categorias hoje: `inventory`, `sales`, `safety`, `delivery`, `payments`, `attendance`. Nova categoria? Migration + atualizar frontend.

## Testes manuais úteis

```bash
# Toggle via curl
curl -X POST $API/portal/capabilities/sales.cross_sell/toggle \
  -H "Authorization: Bearer $TOKEN" \
  -d '{"enabled": true, "config_override": {"max_suggestions_per_turn": 2}}'

# Listar tudo
curl $API/portal/capabilities -H "Authorization: Bearer $TOKEN"
```
