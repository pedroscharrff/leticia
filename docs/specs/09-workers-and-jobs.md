# SPEC 09 — Workers + Jobs

**Propósito**: processar mensagens de forma assíncrona (não bloquear webhook) + jobs proativos (recovery, refill).

## Onde vive

```
api/workers/
├── celery_app.py             # Celery app + process_message + process_broker_message + process_bundled_message
└── jobs/
    ├── abandoned_cart.py     # recover_abandoned_carts_sync (beat hourly)
    └── refill_nudge.py       # nudge_continuous_refill_sync (beat daily)
```

## Setup Celery

```python
celery_app = Celery(
    "saas_farmacia",
    broker=settings.rabbitmq_url,          # amqp://...
    backend=f"redis://...",                 # backend para AsyncResult
)
celery_app.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    timezone="UTC", enable_utc=True,
    worker_concurrency=settings.celery_workers_concurrency,  # default 16
    task_acks_late=True,                    # ack só depois de done — replay em crash
    worker_prefetch_multiplier=1,           # fair scheduling
    beat_schedule={
        "recover_abandoned_carts": {"task": "jobs.recover_abandoned_carts", "schedule": 3600},
        "nudge_continuous_refill": {"task": "jobs.nudge_continuous_refill", "schedule": 86400},
    },
)
```

## Tasks

### `process_message` — webhook nativo

Entrypoint do canal nativo (`/webhook/{token}`).

```python
@celery_app.task(name="process_message", bind=True, max_retries=0)
def process_message(self, tenant_id, schema_name, callback_url, phone, session_id, current_message, media=None):
    asyncio.run(_run_graph(...))
```

Sequência (`_run_graph`):
1. Lazy init pools (Postgres, Redis) no loop async do worker
2. Fetch `skills_config` + `tenant_llm_config`
3. **Ciclo de vida da sessão**:
   - Auto-reset se `closed_at + pause expirou`
   - Encerra se mensagem casa `close_keywords` → entrega `close_message` + RETURN (sem rodar grafo)
4. Build `TenantConfig` + `build_graph_for_tenant`
5. `graph.ainvoke(initial_state)`
6. Decide handoff (worker, fora do grafo):
   - `agent_escalate` ou `order_just_finalized` ou keyword
   - `transfer_to_human` + `auto_pause_after_handoff`
7. POST callback com `reply` formatado
8. Pós-handoff: `send_order_summary` + `_send_pre_handoff_offers` em mensagens separadas
9. `persist_trace` em `agent_traces`

### `process_broker_message` — broker universal

Entrypoint do `/hooks/{tenant}/{slug}` (sem bundling).

Diferenças vs `process_message`:
- Carrega `tenant_integrations` row para `handoff_config`, `reply_body_template`, `reply_url`, `reply_method`, `reply_headers`
- Aplica `reply_body_template` via `broker.apply_mapping(template, ctx)`
- Modo `forward`: POST out em `reply_url` (com headers customizados)
- Modo `response`: NÃO entrega — body retornado já foi enviado no response síncrono do `/hooks` (descontinuado em favor de forward na maioria dos casos)
- **Pula forward de reply principal quando handoff executado** — `transfer_to_human` já entregou pelo provider do handoff (evita duplicar)
- Update `broker_raw_events.status` no final (`processed`/`failed`) + `forward_status_code`, `forward_response`

### `process_bundled_message` — debounce de mensagens picadas

Cliente manda "oi" + "tem dipirona?" + "preço?" em 5s. Sem bundling: 3 chamadas LLM, 3 respostas truncadas.

Com bundling:
1. Mensagem 1 chega → push em Redis list `bundle:{integration_id}:{phone}` + setex `:last_seen=ts1`
2. Agenda `process_bundled_message.apply_async(countdown=bundle_window_seconds, kwargs={scheduled_for_ts: ts1+window})`
3. Mensagem 2 chega → push + setex `:last_seen=ts2` + agenda nova task
4. Task da msg 1 acorda → vê `last_seen=ts2 > scheduled_for_ts` → **desiste**
5. Task da msg 3 acorda → `last_seen=ts3 == scheduled_for_ts` → consume buffer + concatena + dispara `_run_broker_flow`

Texto combinado é `"\n".join(items)`. Preserva mídia do **último** item (se algum tinha).

### `recover_abandoned_carts_task` (beat hourly)

Capability-gated dentro do job. Para cada tenant com `sales.abandoned_cart_recovery` ON:
1. Query carts em `{schema}.cart` com items != [] e `updated_at < NOW() - interval N hour` (config no capability)
2. Para cada: chama callback do canal com mensagem de re-engajamento (template do tenant + items do cart)
3. Marca timestamp pra não re-spammar

### `nudge_continuous_refill_task` (beat daily)

Capability-gated. Para cada tenant com `attendance.continuous_refill_nudge` ON:
1. Para cada customer com `continuous_meds` configurados:
2. Calcula próxima refill (último purchase + intervalo do meds)
3. Se dentro da janela de aviso, dispara mensagem

(TODO: time_of_day config — hoje roda sempre que beat acorda.)

## Invariantes

1. **Cada task `asyncio.run(_run_*)`** — Celery worker síncrono, mas internamente usamos asyncio. `init_pool()` é idempotente.
2. **`task_acks_late=True`** — task só "ack" no broker depois de completar. Worker crash = replay.
3. **`max_retries=0`** em todas — não fazemos retry de mensagem (idempotência seria complexa). Falha = log + persist trace + segue.
4. **Trace sempre persistido** no `finally` — mesmo se task falhou, há registro do que rodou.
5. **Handoff/offer pós-grafo NUNCA quebra a entrega principal** — try/except defensivo em cada etapa pós-resposta.
6. **`schema_name` veio do DB no momento do dispatch** — não buscar do tenant_id de novo no worker (já está como arg).
7. **`session_id` default = `{tenant_id}:{phone}`** se não vier do caller.
8. **Não retentar reply forward.** Idempotência do destino externo não garantida.

## Fluxos críticos

### Falha do grafo

```python
try:
    final_state = await graph.ainvoke(initial_state, config=config)
    # ...
except Exception as exc:
    CONV_TOTAL.labels(..., status="error").inc()
    log.error("task.failed", ...)
    trace_error = str(exc)
    # Best-effort: callback com mensagem de erro genérica
    await _deliver_response(callback_url, {..., "message": "Ocorreu um erro...", "error": True})
    raise
finally:
    await persist_trace(...)  # mesmo em erro
```

### Order finalizado força handoff

`finalizar_pedido` (modo normal) deixa `cart.just_finalized=True` + `cart.last_order={...}`. Worker:
```python
order_close = _extract_order_close_signal(final_state)   # lê cart.just_finalized
order_just_finalized = order_close is not None
do_handoff = handoff_cfg.enabled and (agent_escalate or order_just_finalized or keyword_match)
```

Mesma lógica em `anotar_pedido_balcao` (pré-atendimento) mas trigger via `escalate=True` no skill.

### Pre-handoff offers + order summary

Quando `handoff_was_executed=True`:
1. Pre: `send_order_summary` (capability `sales.order_summary`) — formata recibo do pedido fechado
2. Pre: `_send_pre_handoff_offers` (capability `sales.pre_handoff_offers`) — envia ofertas vigentes

Cada um em try/except — falha aqui não derruba o handoff.

## Pontos de extensão

### Novo job proativo

1. `api/workers/jobs/<nome>.py` — implementa `def <nome>_sync() -> dict` (síncrono; usa `asyncio.run` interno).
2. Em `celery_app.py`:
   ```python
   @celery_app.task(name="jobs.<nome>", bind=True, max_retries=0)
   def <nome>_task(self) -> dict:
       from workers.jobs.<nome> import <nome>_sync
       try: return <nome>_sync()
       except Exception as e: log.warning(...); return {"error": str(e)}

   celery_app.conf.beat_schedule["<nome>"] = {"task": "jobs.<nome>", "schedule": <segundos>}
   ```
3. Capability nova para gating (recomendado).

### Mudar concurrency

`CELERY_WORKERS_CONCURRENCY` env. Default 16. Workers fazem CPU-light (chamada LLM I/O bound), pode ir alto. Limite real = pool de conexões PgBouncer + custo LLM.

### Novo entrypoint de mensagem

Criar task no estilo `process_*_message` + endpoint correspondente em `routers/`. Mantenha sequência: lifecycle → graph → handoff → reply → trace.

## Regressões conhecidas / "Não fazer"

- **Não esquecer `init_pool()` + `init_redis()` no início da task.** Worker é processo separado, sem pool pré-aberto.
- **Não usar `task.delay` síncrono dentro do worker** pra encadear — use `apply_async` com countdown.
- **Não levantar exceção em `_send_pre_handoff_offers` ou `send_order_summary`** — handoff já saiu, nada deve quebrar.
- **Não atualizar `broker_raw_events.status='processed'` ANTES do forward** — se forward falhar, perde sinal de retry.
- **Não confiar em `prev_state` carregado fora do worker** — tudo tem que vir do args da task (tenant_id, schema_name, etc.).
- **Não usar `Celery retry mechanism` (retries automáticos)** — duplica mensagem ao cliente. Falha = trace + log + segue.
- **Não fazer broadcast em cima de Redis pra notificar UI em real-time** — sem essa feature hoje. Caminho seria WebSocket + pub/sub explicit (fora de escopo).
