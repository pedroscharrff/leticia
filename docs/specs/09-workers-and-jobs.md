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
   - `transfer_to_human` + `auto_pause_after_handoff` (chamado SEMPRE que `do_handoff`, **independente do `ok` da API externa** — ver "Finalização determinística" abaixo)
   - Se NÃO houve handoff e o agente sinalizou `[[END]]` (`final_state.end_conversation`) → `end_session` (closed_at, sem pausar, limpa histórico)
7. POST callback com `reply` formatado
8. Pós-handoff: `send_order_summary` + `_send_pre_handoff_offers` em mensagens separadas
9. `persist_trace` em `agent_traces`

### `process_broker_message` — broker universal

Entrypoint do `/hooks/{tenant}/{slug}` (sem bundling).

Tanto `process_broker_message` quanto `process_bundled_message` delegam ao
helper compartilhado `_run_broker_flow`.

> **Gate `is_ai_paused` (broker):** logo no início de `_run_broker_flow`, ANTES
> de `_maybe_close_or_reset_session`, checamos `is_ai_paused(tenant, phone)`.
> Se pausado → log `broker.flow.skipped.ai_paused`, marca o evento `processed`
> e RETURN (não roda o agente). É o equivalente broker do gate que o webhook
> nativo faz no ingest — mas aqui tem que ser no worker porque o bundling
> processa depois do ingest. Sem isso, o bot respondia durante a janela de
> handoff e re-finalizava pedidos antigos (Letícia 2026-06-07).
>
> **Fingerprint anti-auto-pausa:** `_run_broker_flow` chama `bot_echo.remember`
> em todos os sends do bot (reply forward, `transfer_to_human`, ofertas/resumo).
> A detecção de resposta humana (ingest, `routers/broker.py`) usa `bot_echo.is_echo`
> pra não confundir o eco do próprio bot com o atendente. Ver SPEC 05.

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

### `recover_abandoned_carts_task` (beat a cada 2 min)

Capability-gated dentro do job. Para cada tenant com `sales.abandoned_cart` ON:
1. Query carts em `{schema}.cart` com `items != []` e `updated_at < NOW() - delay_minutes` (config no capability; `delay_hours*60` legado como fallback), respeitando quiet hours e `max_attempts`. Guard `NOT EXISTS orders ... created_at >= cart.updated_at` evita recuperar pedido já fechado.
2. Para cada: envia mensagem de re-engajamento (template do tenant + items do cart) pelo path de outbound proativo.
3. Marca `sent_recovery_at`/`recovery_attempts` pra não re-spammar.

**Fonte de carts inclui pré-atendimento:** no modo `sales.stock_check` OFF, o cart só existe porque o vendedor chama `registrar_itens_interesse` (rascunho, sem `just_finalized`) durante a coleta — ver SPEC 02 §vendedor. Sem essa tool, o pré-atendimento nunca gera cart recuperável. Pedidos já confirmados via `anotar_pedido_balcao` ficam fora (cart limpo + guard de orders). **Fallback determinístico (2026-06-10):** como o LLM frequentemente ignora `registrar_itens_interesse`, o `vendedor_node` extrai itens via Haiku no fim do turno e grava no cart quando nenhuma tool de cart rodou mas a resposta enumera itens — ver SPEC 02 §vendedor. Métrica: `preattend_draft_fallback_total`.

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

Quando `handoff_was_executed=True`, `_send_post_handoff_messages` despacha os dois blocos:

| Campo `handoff_config.post_handoff_order` | Ordem de envio |
|---|---|
| `"summary_first"` (default — não quebra tenants existentes) | resumo → ofertas |
| `"offers_first"` | ofertas → resumo |

Cada bloco em try/except — falha num não cancela o outro nem derruba o handoff.

⚠️ **Ordem de ENVIO ≠ ordem de ENTREGA.** Ofertas com mídia saem direto pela API
do canal (ex. ClickMassa `base_url`); o resumo sai pelo `reply_url` (n8n/forward).
Transportes distintos + WhatsApp entrega mídia mais devagar que texto = o resumo
(texto) pode chegar antes da oferta (imagem) mesmo tendo sido enviado depois.
Por isso, no fluxo `offers_first`, quando `_send_pre_handoff_offers` retornou
`media_count > 0`, a helper espera `handoff_config.post_handoff_media_delay_seconds`
(default 2.5s, 0 desativa) antes do resumo. `_send_pre_handoff_offers` passou a
retornar `int` (qtd de mídias enviadas) justamente pra isso — não voltar a `None`.
Em `summary_first` não há atraso: texto-antes-imagem já cai na ordem desejada
naturalmente.

**`post_handoff_media_delay_seconds` é editável no portal** (campo numérico, 0–30s,
passo 0,5, default 2,5). Aparece só quando `post_handoff_order = "offers_first"`:
- **Broker** (`tenant_integrations.handoff_config`): `PortalBroker.tsx` — state
  `postHandoffMediaDelay`, salvo via `saveFlow` no `handoff_config`.
- **Webhook nativo** (`tenant_channels.handoff_config`): `PortalCanais.tsx` — padrão
  `cfg`/`set`, salvo via `updateChannel`.
- Tipo em `frontend/src/api/{portal,broker}.ts::HandoffConfig`.

⚠️ Histórico: o worker lia esse campo desde a v7, mas ele **não existia no tipo
`HandoffConfig` nem nos forms** — ficava órfão, travado no default 2,5s, sem como
o tenant ajustar (o ajuste é a primeira linha de defesa quando o resumo ultrapassa
a imagem). Plugado no front em 2026-06-14. **É ajuste de timing, não garantia**: a
corrida de transportes (mídia via API do canal × resumo via `reply_url`) continua
existindo — para eliminá-la de vez, mandar o resumo pelo MESMO transporte da mídia.

### Finalização determinística do atendimento (closed_at)

O status "encerrado" do portal (inbox em `routers/conversations.py`) vem de `conversation_state.closed_at`. Regras:

- **Handoff** (`do_handoff=True`): chamamos `auto_pause_after_handoff` **sempre**, mesmo se `transfer_to_human` retornar `ok=False`. Decisão de produto: o atendimento é dado por encerrado independentemente de a API externa (ClickMassa/TalkFarma) aceitar a transferência. Falha externa só gera `log.warning(...external_failed_closing_anyway)`.
- `auto_pause_after_handoff` SEMPRE seta `closed_at`; pausa a IA (`ai_paused`+`paused_until`) só quando `pause_minutes > 0`. Com `pause_minutes <= 0` finaliza o ticket SEM pausar (antes fazia early-return e não fechava nada — era o bug do "resumo enviado mas ticket aberto").
- **Fim sinalizado pelo agente** (`[[END]]` → `end_conversation=True`): quando NÃO houve handoff, o worker chama `end_session` (closed_at, `ai_paused=FALSE`, limpa histórico). Cobre "era só isso / tchau" sem depender de `close_keywords` cadastradas.
- **close_keywords** (cliente digita palavra configurada): tratado ANTES do grafo em `_maybe_close_or_reset_session` → `end_session`. Broker lê de `tenant_integrations.session_config`; nativo de `tenant_channels.session_config` (fontes distintas — cuidado ao configurar).

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
- **Ao zerar a sessão (`reset_session`/`end_session`), SEMPRE passar `session_id`.** O agente grava memória em `hist:{session_id}` e ownership em `owner:{session_id}` (`agents/nodes/context.py`), onde `session_id` = id da plataforma OU telefone — **NÃO** `{tenant_id}:{phone}`. Por anos o reset apagava só a chave legada `hist:{tenant_id}:{phone}` (inexistente) → a memória NUNCA era zerada e a conversa nova "lembrava" do atendimento anterior. `_clear_history_keys(tenant_id, phone, session_id)` apaga a chave real + a legada. Os call sites no worker propagam `session_id` (têm em escopo). Corrigido 2026-06-17.
- **Não usar `task.delay` síncrono dentro do worker** pra encadear — use `apply_async` com countdown.
- **Não levantar exceção em `_send_pre_handoff_offers` ou `send_order_summary`** — handoff já saiu, nada deve quebrar.
- **Não re-acoplar `auto_pause_after_handoff` (closed_at) ao `handoff_result.ok` nem voltar o early-return de `pause_minutes <= 0`.** Era o bug do "resumo enviado mas ticket nunca finaliza": o resumo é gated em `do_handoff`, o close ficava gated em `ok` + `pause_minutes>0`. Finalização do ticket é determinística; pausa da IA é o que depende de `pause_minutes`.
- **Não atualizar `broker_raw_events.status='processed'` ANTES do forward** — se forward falhar, perde sinal de retry.
- **Não confiar em `prev_state` carregado fora do worker** — tudo tem que vir do args da task (tenant_id, schema_name, etc.).
- **Não usar `Celery retry mechanism` (retries automáticos)** — duplica mensagem ao cliente. Falha = trace + log + segue.
- **Não fazer broadcast em cima de Redis pra notificar UI em real-time** — sem essa feature hoje. Caminho seria WebSocket + pub/sub explicit (fora de escopo).
- **Não remover o gate `is_ai_paused` no início de `_run_broker_flow`** — é o que cala o bot durante a janela de handoff no broker (webhook checa no ingest; broker NÃO pode, por causa do bundling). Removê-lo traz de volta o bug do bot re-finalizando pedido antigo.
- **Não esquecer `bot_echo.remember` ao adicionar um novo caminho de envio do bot no broker** — sem o fingerprint, o eco daquela mensagem é lido como resposta humana e pausa a IA por engano.
