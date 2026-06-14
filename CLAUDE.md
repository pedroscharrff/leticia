# CLAUDE.md — instruções operacionais para Claude Code

> Este arquivo é lido automaticamente quando Claude Code abre o projeto.
> **Leitura obrigatória antes de qualquer mudança.**

---

## 1. O que é este projeto

SaaS multi-tenant que automatiza atendimento de farmácias no WhatsApp/Telegram com agentes de IA. Backend FastAPI + LangGraph multi-agente sobre Claude/GPT/Gemini, Postgres multi-schema, Celery workers, broker universal de webhooks.

Documentação completa:

- **`docs/PRD.md`** — o que o produto faz, personas, limites
- **`docs/ARCHITECTURE.md`** — mapa técnico do sistema (LEIA PRIMEIRO ao mexer em backend)
- **`docs/specs/00-overview.md`** — índice de specs por módulo (LEIA a spec relevante antes de mexer no módulo)

---

## 2. Antes de mexer em qualquer coisa

### Workflow obrigatório

1. **Leia a spec** do módulo que vai tocar (`docs/specs/0X-*.md`).
2. **Procure regressões conhecidas** na seção "Não fazer" da spec. Sério, vai lá.
3. **Releia `docs/ARCHITECTURE.md` §10** ("Onde costuma quebrar") — lista de armadilhas históricas.
4. Faça a mudança.
5. **Atualize a spec** se mudou contrato/invariante/extensão.

### Mapeamento intent → spec

| Vou mexer em… | Leia… |
|---|---|
| `agents/graph_builder.py`, `router.py`, `state.py`, nodes fixos | SPEC 01 |
| `agents/nodes/skills/*` | SPEC 02 |
| `agents/tools/*` | SPEC 03 |
| `api/services/capabilities.py`, capability nova | SPEC 04 |
| `api/channels/*`, `routers/broker.py`, `routers/webhook.py` | SPEC 05 |
| billing, onboarding, RBAC, JWT | SPEC 06 |
| schema, migration nova | SPEC 07 |
| `llm/*`, prompt caching, retry | SPEC 08 |
| `workers/celery_app.py`, jobs/* | SPEC 09 |
| `services/*_guard.py`, `agents/nodes/safety_guard.py` | SPEC 10 |

---

## 3. Convenções de código

### Python
- **Async-first**: tudo é `async def` exceto utilitários puros. `asyncio.run` só dentro de Celery task.
- **structlog em vez de `print`/`logging` direto**. `log.info("event.name", chave=valor)` — formato structured JSON.
- **Type hints obrigatórios** em assinaturas públicas. `from __future__ import annotations` no topo dos arquivos novos.
- **dataclasses para configs**, **TypedDict para state do grafo**, **Pydantic para schemas de API**.
- **f-strings em SQL é proibido**, exceto `SET search_path = {schema_name}` (schema_name validado por regex em onboarding).
- **`async with get_db_conn() as conn:`** — nunca abra/feche conexão manualmente.
- **try/except defensivo em paths críticos** (webhook, worker, save_context) com log estruturado do erro.
- **Erros captados viram trace_step** — pattern: `_node_error = {"type": ..., "msg": ..., "stack": tb[-1500:]}`.

### Frontend
- TypeScript estrito (`tsconfig.json` já configurado).
- Páginas em `frontend/src/pages/`, componentes em `frontend/src/components/`, API clients em `frontend/src/api/`.
- Tailwind utility classes core; sem libs adicionais sem aprovação.
- Estado: `AuthContext` + hooks locais. Sem Redux/Zustand.

### SQL
- Migrations idempotentes (`IF NOT EXISTS`, `ON CONFLICT DO UPDATE`).
- Numeração `NNN_descricao.sql` (3 dígitos, atual em 046).
- DDL multi-statement → roda via `DATABASE_URL_DIRECT` (PgBouncer transaction pool não suporta).
- Toda tabela com `tenant_id` → FK CASCADE + índice composto `(tenant_id, created_at DESC)`.

---

## 4. Arquivos onde **não se escreve direto** — passe pelo factory/helper

| Em vez de… | Use… |
|---|---|
| `ChatAnthropic(...)` direto | `llm.providers.get_llm(provider, model)` |
| Concatenar prompt no `SystemMessage` | `llm.caching.system_message(stable, provider, volatile=...)` |
| Loop `for _ in range(3): try: await llm.ainvoke()` | `async for attempt in llm_retry(): with attempt: await llm.ainvoke()` |
| Bind manual de tool ao LLM | Factory `make_<tool>(schema_name, ...)` + `tools=[...]` no `run_skill` |
| Verificar capability em código | `await capabilities.is_enabled(tenant_id, "key")` (cacheado) |
| Salvar segredo do tenant em texto puro | `services.secrets.store_secret(tenant_id, key, value)` (Fernet) |
| Acessar tabela per-tenant sem `search_path` | `tenant_conn(schema_name)` helper OU `await conn.execute(f"SET search_path = {schema_name}, public")` |

---

## 5. Comandos úteis

### Local dev
```bash
# Backend
cd api && uvicorn main:app --reload

# Worker
cd api && celery -A workers.celery_app worker -l info

# Beat (cron)
cd api && celery -A workers.celery_app beat -l info

# Frontend
cd frontend && npm run dev

# Migrations (manual, quando direct URL não disponível em runtime)
python scripts/run_migrations.py

# Tenant novo (CLI)
python scripts/create_tenant.py

# Tests
pytest tests/
```

### Docker (prod-like)
```bash
docker compose up -d           # tudo
docker compose logs -f api     # logs do API
docker compose logs -f worker  # logs do worker
./deploy.sh                    # script de deploy completo (revisar antes!)
./update.sh                    # update incremental
```

### Debug de turno do agente
1. Portal → Logs / Traces → encontre a conversa
2. `PortalTraces.tsx` mostra timeline (nodes, latência, tool_calls, erros)
3. Se precisar olhar SQL direto: `SELECT * FROM <schema>.agent_traces ORDER BY created_at DESC LIMIT 10;`

---

## 6. Adicionando coisas — checklists rápidos

### Novo skill (ver SPEC 02 §pontos de extensão)
- [ ] `agents/nodes/skills/<nome>.py` com `<nome>_node(state, llm_factory)`
- [ ] Constante `_SYSTEM` no topo (prompt base, SEM instruções de marcador — o `.flow()` do PromptBuilder gera isso das tools)
- [ ] **Adicionar `SkillDefinition` em `agents/skills_registry.py::SKILLS`** — fonte ÚNICA. `_KNOWN_SKILLS` (router), `_VALID_HANDOFF_TARGETS` (_base), `all_skill_nodes` (graph_builder) e descrições do orchestrator DERIVAM daqui. Não edite mais esses 4 lugares à mão.
- [ ] Migration: INSERT em `public.skill_catalog`
- [ ] Frontend renderiza automaticamente

### Nova tool (ver SPEC 03)
- [ ] Factory em `agents/tools/<arquivo>.py`
- [ ] Docstring **completa** (LLM lê pra decidir invocação)
- [ ] Bind no array de tools do skill que vai usar
- [ ] Mencionar a tool no prompt do skill (quando usar)
- [ ] Se capability-gated: check `is_enabled` dentro da tool E no skill

### Nova capability (ver SPEC 04)
- [ ] Migration: INSERT em `public.capability_catalog` com `default_enabled`, `category`, `default_config`, `min_plan`, `depends_on`
- [ ] No código: `await capabilities.is_enabled(tenant_id, "namespace.key")`
- [ ] `long_desc` em markdown explicando trade-offs (UI mostra ao operador)

### Nova migration (ver SPEC 07)
- [ ] Arquivo `api/db/migrations/<NNN>_descricao.sql` (NNN sequencial)
- [ ] Idempotente (`IF NOT EXISTS`, `ON CONFLICT`)
- [ ] Se afeta schema per-tenant:
    - criar função `public.create_tenant_schema_<feat>_ext(p_schema)` (ou `add_<feat>_to_schema`) idempotente
    - **adicionar a chamada em `public.create_tenant_schema_full` (migration 048)** — é o único ponto que call sites (`onboarding.py`, `tenants.py`, `scripts/create_tenant.py`) usam, então esquecer aqui = drift em tenants novos
    - loop em tenants existentes dentro da própria migration (com `RAISE WARNING ... SQLERRM`, nunca `RAISE NOTICE` mudo — vê regressão histórica em 023/025)
- [ ] Testar em ambiente local antes de PR

### Novo canal (ver SPEC 05)
- [ ] Adapter em `api/channels/<canal>.py` implementando `ChannelAdapter`
- [ ] Registrar em `api/channels/registry.py`
- [ ] Migration: ampliar CHECK em `tenant_channels.channel_type`
- [ ] Frontend `PortalCanais.tsx`: form de credenciais

### Nova migration de capability/skill (atalho)
Padrão: usa `ON CONFLICT (key) DO UPDATE SET ...` para idempotência total. Veja `046_safety_guards_v2.sql` como template.

⚠️ **Para setar `config_schema` aninhado (campo editável no modal de Recursos): use `jsonb_build_object` montando o objeto inteiro, NUNCA `jsonb_set(col, '{properties,x}', ...)`.** A coluna nasce `NOT NULL DEFAULT '{}'`, e `jsonb_set` não cria o pai `properties` quando ele não existe → NO-OP silencioso (campo não aparece no portal mesmo com a migration aplicada). Mordeu nas migs 051 e 057; corrigido na 058. Detalhes em SPEC 04 §"Adicionar campo de config editável" e SPEC 07 §"Não fazer".

---

## 7. Hotspots conhecidos (refatorar com cuidado)

| Arquivo | Linhas | Por que cuidado |
|---|---|---|
| `api/workers/celery_app.py` | 1217 | Mistura task + callback + handoff + offers. Conhecimento crítico. Quebrar em sub-módulos requer cuidado com asyncio.run + lazy init pools. |
| `agents/nodes/skills/vendedor.py` | 925 | Bifurca em 2 modos (normal/pré-atendimento). Lógica force-call é regressão histórica importante — não remova. |
| `api/routers/broker.py` | 1178 | CRUD pesado. Sub-rotas múltiplas. Mudar URL pública quebra integrations de tenants em prod. |
| `api/services/inventory.py` | 667 | Sync de catálogo (CSV/XLSX/Sheets). Validações estritas. |

Refatorações sugeridas estão em `docs/ARCHITECTURE.md` §13.

---

## 8. Estilo de PR

- **Título no imperativo**: "fix: bot inventa preço em vendedor normal", não "fixing prices".
- **Corpo** descreve: (1) o que mudou, (2) por que, (3) como testar.
- **Atualize a spec** se mudou contrato/invariante. PR sem spec atualizada quando a mudança exige = não merge.
- **Mencione regressão histórica** se mudou algo "marcado para não tocar" — explique por que agora pode.
- **Tests não-obrigatórios** ainda (cobertura baixa hoje). Mas se você adicionou tests novos, **rode `pytest tests/`** antes do PR.

---

## 9. Onde NÃO criar coisa nova sem aprovação

- Outro framework de DI (já temos FastAPI Depends + closures)
- Outro task queue (já temos Celery)
- Outro ORM (estamos em asyncpg raw + Pydantic — ORM seria refator gigante)
- Outro framework de prompts (LangChain já é a única dependência LLM)
- Lib nova no frontend além das listadas em `frontend/package.json` sem discutir

---

## 10. Princípios

1. **O cliente final NUNCA vê erro técnico** — fallback amigável em PT-BR, log estruturado pro dev.
2. **Atendente humano > bot** — em qualquer dúvida, transfere (não compita).
3. **Defaults seguros** — capabilities safety ON por default; modo pré-atendimento como mais seguro para tenant sem ERP.
4. **Custo importa** — Anthropic Sonnet pra skills, Haiku pro orchestrator/analyst. Prompt caching obrigatório (split stable/volatile correto).
5. **Determinístico > estatístico onde possível** — validação pós-LLM (safety guards), keyword routing antes do orchestrator (fast-paths), force-call de tool em pré-atendimento.
6. **Tenant data isolada por schema** — nunca cruzar dados entre tenants.

---

## 11. Quando estiver em dúvida

1. Lê a spec do módulo. Se a spec não cobre, **proponha um update da spec primeiro**.
2. Procure no histórico do git: comentários inline costumam ter o contexto da decisão (são MUITO ricos neste repo — não os apague).
3. Pergunta pro dono do projeto antes de quebrar invariante listada nas specs.

---

## 12. Arquivos para Claude indexar primeiro (resumo)

```
docs/ARCHITECTURE.md            # mapa do sistema
docs/PRD.md                     # o que é o produto
docs/specs/00-overview.md       # índice
docs/specs/0X-*.md              # specs por módulo

agents/graph_builder.py         # como o grafo é montado
agents/state.py                 # shape do AgentState
agents/router.py                # roteamentos condicionais

api/main.py                     # entrypoint FastAPI + lifespan + middlewares
api/config.py                   # todas as settings (env vars)
api/workers/celery_app.py       # entrypoint do worker + process_message

api/services/capabilities.py    # feature flags
api/services/handoff.py         # transferência humana

llm/providers.py + llm/caching.py + llm/retry.py
```
