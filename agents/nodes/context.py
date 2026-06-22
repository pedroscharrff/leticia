"""
Nodes: load_context  /  save_context

load_context — antes do orchestrator:
  • Carrega histórico da conversa do Redis
  • Carrega persona e prompts customizados do DB
  • Carrega perfil do cliente

save_context — ao final do grafo:
  • Persiste a nova mensagem no histórico (Redis)
  • Grava log de conversa no PostgreSQL
"""
from __future__ import annotations

import json
import time
import structlog

from agents.state import AgentState
from llm.usage_tracking import aggregate_turn_usage

log = structlog.get_logger()

_TTL_DEFAULT = 1800  # segundos (30 min) — fallback se DB/cache falhar


def _mem_keys(state: AgentState) -> tuple[str, str]:
    """Chaves Redis de memória da conversa: (hist_key, owner_key).

    Identidade ESTÁVEL = (tenant_id, phone) — a MESMA que o ciclo de vida da
    sessão usa para resetar (`services/conversation_state._clear_history_keys`).
    Historicamente o agente keyava por `session_id` (id de plataforma OU phone),
    enquanto o reset apagava `{tenant_id}:{phone}` → as chaves DIVERGIAM e a
    memória NUNCA era zerada entre atendimentos (conversa nova "lembrava" do
    pedido anterior). Unificado em (tenant_id, phone). Fallback para session_id
    se faltar tenant/phone (degenerado — não deve ocorrer em prod).
    """
    tid = (state.get("tenant_id") or "").strip()
    ph = (state.get("phone") or "").strip()
    if tid and ph:
        return f"hist:{tid}:{ph}", f"owner:{tid}:{ph}"
    sid = state.get("session_id") or ""
    return f"hist:{sid}", f"owner:{sid}"


def _apply_signature(text: str, state: AgentState) -> str:
    """Anexa a assinatura da persona (`/portal/persona`) à resposta — de forma
    DETERMINÍSTICA, depois da LLM.

    A assinatura NÃO passa mais pelo prompt (era uma instrução "opcional, no fim
    de respostas longas" que a LLM quase nunca cumpria, já que as respostas são
    curtas por design). Aqui ela é colada direto no texto enviado ao cliente.

    Escopo (decisão de produto): SÓ respostas normais. Pula transferência para
    humano (`escalate`), encerramento (`end_conversation`) e respostas vazias —
    não faz sentido assinar uma mensagem de "vou te transferir" ou um resumo
    pós-handoff. Idempotente: não duplica se o texto já termina com a assinatura.
    """
    sid = state.get("session_id")
    body = (text or "").strip()
    if not body:
        log.info("signature.skip", session=sid, reason="empty_response")
        return text
    # Pula mensagens que NÃO são "resposta normal": transferência humana
    # (escalate), encerramento (end_conversation), handoff entre skills e o
    # recibo de pedido fechado (`cart.just_finalized` — vira a mensagem de
    # transferência no worker, ver [[reference_transfer_and_offers_flow]]).
    # Assinar qualquer um deles polui a mensagem de handoff/recibo.
    cart_finalized = bool((state.get("cart") or {}).get("just_finalized"))
    if (
        state.get("escalate")
        or state.get("end_conversation")
        or state.get("handoff_to")
        or cart_finalized
    ):
        reason = (
            "escalate" if state.get("escalate")
            else "end_conversation" if state.get("end_conversation")
            else "handoff_to" if state.get("handoff_to")
            else "cart_just_finalized"
        )
        log.info("signature.skip", session=sid, reason=reason)
        return text
    persona = state.get("persona") or {}
    signature = (persona.get("signature") or "").strip()
    if not signature:
        # Caso MAIS comum de "não saiu assinatura": coluna vazia no DB.
        # has_persona ajuda a distinguir "persona não carregou" de "campo vazio".
        log.info("signature.skip", session=sid, reason="no_signature",
                 has_persona=bool(persona))
        return text
    # Dedupe defensivo: se a LLM (por mímica do histórico) já assinou, não repete.
    if body.endswith(signature):
        log.info("signature.skip", session=sid, reason="already_present")
        return text
    log.info("signature.applied", session=sid, sig_len=len(signature))
    return f"{body}\n\n{signature}"


async def _resolve_session_ttl(tenant_id: str | None) -> int:
    """Lê session_ttl_minutes do tenant (default 30) e retorna em segundos.

    Cacheado em Redis por 5 min — alteração no portal demora no máximo 5min
    para entrar em vigor para sessões já abertas. Falha-aberta para o default.
    """
    if not tenant_id:
        return _TTL_DEFAULT
    cache_key = f"ttl:tenant:{tenant_id}"
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached:
            return int(cached)
    except Exception:
        pass
    try:
        from db.postgres import get_db_conn
        async with get_db_conn() as conn:
            row = await conn.fetchrow(
                "SELECT session_ttl_minutes FROM public.tenants WHERE id = $1",
                tenant_id,
            )
        minutes = int((row and row["session_ttl_minutes"]) or 30)
        ttl_s = max(60, minutes * 60)  # mínimo 1 minuto
        try:
            from db.redis_client import get_redis
            redis = get_redis()
            await redis.setex(cache_key, 300, str(ttl_s))
        except Exception:
            pass
        return ttl_s
    except Exception:
        return _TTL_DEFAULT


# ── load_context ──────────────────────────────────────────────────────────────

async def load_context(state: AgentState) -> AgentState:
    """Carrega histórico, persona e perfil do cliente."""
    session_id   = state["session_id"]
    tenant_id    = state["tenant_id"]
    schema_name  = state["schema_name"]
    max_messages = 10
    hist_key, owner_key = _mem_keys(state)

    updates: dict = {}

    # ── Histórico do Redis ────────────────────────────────────────────────────
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        raw = await redis.get(hist_key)
        messages = json.loads(raw) if raw else []
        # Trunca para as últimas N mensagens para economizar tokens
        updates["messages"] = messages[-max_messages:]
    except Exception as exc:
        log.warning("context.redis.load_failed", session=session_id, exc=str(exc))
        updates["messages"] = []

    # ── Sticky ownership: dono atual da conversa (persistido entre turnos) ─────
    # Lido sempre; só consumido pelo orchestrator quando sticky está ON. Falha
    # aberta para None (sem owner → orchestrator reclassifica normalmente).
    try:
        from db.redis_client import get_redis
        owner_raw = await get_redis().get(owner_key)
        if isinstance(owner_raw, bytes):
            owner_raw = owner_raw.decode()
        updates["current_owner"] = owner_raw or None
    except Exception as exc:
        log.warning("context.redis.owner_load_failed", session=session_id, exc=str(exc))

    # ── Persona e prompts do PostgreSQL ───────────────────────────────────────
    try:
        from db.postgres import get_db_conn
        async with get_db_conn() as conn:
            # Persona (tabela public.tenant_persona) — SELECT * para que NOVAS
            # colunas (business_hours, catchphrases, forbidden_topics, etc.)
            # cheguem ao agente sem precisar editar o SELECT a cada alteração
            # do schema. Cf. docs/specs/02-skills.md §Persona — campos suportados.
            persona_row = await conn.fetchrow(
                "SELECT * FROM public.tenant_persona WHERE tenant_id = $1",
                tenant_id,
            )
            if persona_row:
                updates["persona"] = dict(persona_row)

            # Prompts customizados + extra_instructions por skill
            # (tabela public.tenant_skill_prompts)
            #   system_prompt      → SUBSTITUI o prompt base do skill
            #   extra_instructions → APPENDA ao prompt em uso (base ou custom)
            prompt_rows = await conn.fetch(
                """
                SELECT skill_name, system_prompt, extra_instructions
                FROM public.tenant_skill_prompts
                WHERE tenant_id = $1
                """,
                tenant_id,
            )
            updates["skill_prompts"] = {
                r["skill_name"]: r["system_prompt"]
                for r in prompt_rows
                if r["system_prompt"]
            }
            updates["skill_instructions"] = {
                r["skill_name"]: r["extra_instructions"]
                for r in prompt_rows
                if r["extra_instructions"]
            }

            # Sales config do tenant (campos obrigatórios + política de tentativas)
            try:
                from services.sales_config import load_sales_config
                updates["sales_config"] = await load_sales_config(tenant_id)
            except Exception as exc:
                log.warning("context.sales_config.load_failed",
                            tenant=tenant_id, exc=str(exc))
                updates["sales_config"] = {}

            # Perfil do cliente + carrinho persistido (tabelas do schema do tenant)
            try:
                await conn.execute(f"SET search_path = {schema_name}, public")
                session_row = await conn.fetchrow(
                    "SELECT customer_profile FROM sessions WHERE session_key = $1",
                    session_id,
                )
                if session_row and session_row["customer_profile"]:
                    updates["customer_profile"] = session_row["customer_profile"]

                # Cadastro do cliente (por telefone) — usado p/ checar campos
                # obrigatórios e carregar memória de longo prazo (alergias,
                # medicamentos contínuos, preferências, segmento, LTV).
                phone = state.get("phone", "")
                if phone:
                    cust_row = await conn.fetchrow(
                        """
                        SELECT id, phone, name, email, doc, cep, street,
                               street_number, complement, neighborhood,
                               city, state, notes,
                               allergies, continuous_meds, preferences,
                               tags, segment, total_orders, total_spent,
                               ltv, last_purchase_at
                        FROM customers WHERE phone = $1
                        """,
                        phone,
                    )
                    if cust_row:
                        cust = dict(cust_row)
                        cust["id"] = str(cust["id"])  # UUID → str p/ JSON-safe
                        # Normaliza JSONB que pode vir como string do asyncpg
                        for k in ("continuous_meds", "preferences"):
                            v = cust.get(k)
                            if isinstance(v, str):
                                try:
                                    cust[k] = json.loads(v)
                                except json.JSONDecodeError:
                                    cust[k] = [] if k == "continuous_meds" else {}
                        if cust.get("last_purchase_at"):
                            cust["last_purchase_at"] = cust["last_purchase_at"].isoformat()
                        updates["customer"] = cust
                    else:
                        updates["customer"] = {"phone": phone}

                # Carrinho persistido — recupera o que está salvo na DB
                cart_row = await conn.fetchrow(
                    "SELECT items, subtotal, stock_mode, sales_attempts "
                    "FROM cart WHERE session_key = $1",
                    session_id,
                )
                if cart_row:
                    items = cart_row["items"]
                    if isinstance(items, str):
                        items = json.loads(items)
                    updates["cart"] = {
                        "items":          items or [],
                        "subtotal":       float(cart_row["subtotal"] or 0),
                        "sales_attempts": int(cart_row["sales_attempts"] or 0),
                    }
                    if cart_row["stock_mode"]:
                        updates["stock_mode"] = cart_row["stock_mode"]
            except Exception as exc:
                # Tabelas ainda não existem nesse schema — ignora
                log.warning("context.tenant_schema.load_skipped",
                            schema=schema_name, exc=str(exc))

    except Exception as exc:
        log.warning("context.db.load_failed", tenant=tenant_id, exc=str(exc))

    return {**state, **updates}


# ── save_context ──────────────────────────────────────────────────────────────

async def save_context(state: AgentState) -> AgentState:
    """Persiste conversa no Redis e log no PostgreSQL."""
    session_id     = state["session_id"]
    schema_name    = state["schema_name"]
    current_msg    = state.get("current_message", "")
    final_response = state.get("final_response", "")
    skill_used     = state.get("selected_skill", "unknown")
    hist_key, owner_key = _mem_keys(state)

    # Assinatura determinística (persona). `outbound` = o que o cliente recebe
    # (com assinatura); `final_response` puro vai pro histórico do Redis para a
    # LLM NÃO ver a assinatura e passar a mimetizá-la (geraria duplicata).
    outbound = _apply_signature(final_response, state)

    # Atualiza histórico — evita persistir mensagens vazias (causariam 400 no Anthropic)
    messages = list(state.get("messages", []))
    if (current_msg or "").strip():
        messages.append({"role": "user", "content": current_msg.strip()})
    if (final_response or "").strip():
        messages.append({"role": "assistant", "content": final_response.strip()})

    # ── Redis ─────────────────────────────────────────────────────────────────
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        ttl_s = await _resolve_session_ttl(state.get("tenant_id"))
        await redis.setex(hist_key, ttl_s, json.dumps(messages))

        # ── Sticky ownership: grava o dono (skill que respondeu) ou limpa ─────
        # Limpa quando o atendimento terminou/escalou/finalizou pedido — a
        # próxima mensagem deve reclassificar do zero. Caso contrário, fixa o
        # owner para o skill que conduziu o turno. Mesma TTL do histórico (o
        # owner expira junto com a sessão). Consumido pelo orchestrator quando
        # sticky_ownership_enabled.
        try:
            cart_just_finalized = bool((state.get("cart") or {}).get("just_finalized"))
            if (
                state.get("end_conversation")
                or state.get("escalate")
                or cart_just_finalized
            ):
                await redis.delete(owner_key)
            elif skill_used and skill_used != "unknown":
                await redis.setex(owner_key, ttl_s, skill_used)
        except Exception as exc:
            log.warning("context.redis.owner_save_failed", session=session_id, exc=str(exc))
    except Exception as exc:
        log.warning("context.redis.save_failed", session=session_id, exc=str(exc))

    # ── PostgreSQL ────────────────────────────────────────────────────────────
    try:
        from db.postgres import get_db_conn
        async with get_db_conn() as conn:
            await conn.execute(f"SET search_path = {schema_name}, public")

            # Upsert de sessão
            await conn.execute(
                """
                INSERT INTO sessions (session_key, phone, turn_count, updated_at)
                VALUES ($1, $2, 1, NOW())
                ON CONFLICT (session_key) DO UPDATE
                SET turn_count = sessions.turn_count + 1,
                    updated_at = NOW()
                """,
                session_id,
                state.get("phone", ""),
            )

            # Persiste carrinho (importante: vendedor mutou em memória, agora salvamos)
            # ⚠️ NÃO chamar json.dumps no items — o codec em db/postgres.py já
            # serializa parâmetros jsonb. Double-encoding gravava o array
            # como string JSON ("[{...}]"), quebrando jsonb_typeof/array_length
            # nas queries de Recuperação. Ver [[jsonb-double-encoding]] e
            # migration 050 que desfez o estrago em dados antigos.
            cart = state.get("cart") or {"items": [], "subtotal": 0.0}
            # `just_finalized` é setado por tools de fechamento (finalizar_pedido
            # no ERP, anotar_pedido_balcao no pré-atendimento). Modo ERP já
            # DELETEa a row do cart dentro da tool — chega aqui com items=[]
            # naturalmente. Modo pré-atendimento NÃO deleta: mantém os items
            # populados em memória para o worker montar `send_order_summary`
            # via `cart.last_order` ([[transferencia-handoff-escalate-ofertas-pre-handoff-fluxo-completo]]).
            # Sem o guard abaixo, esses itens persistem em `{schema}.cart` com
            # items > 0 e o job de recuperação ([[recover-silent-skip]]) acha
            # "abandono ativo" horas depois — apesar do ticket já estar fechado
            # (closed_at) e do pedido já existir em `orders`. Solução: persistir
            # `items=[]` quando `just_finalized` está ativo, espelhando o efeito
            # do DELETE do ERP. O state em memória NÃO é alterado — o worker
            # continua tendo `cart.items`/`cart.last_order` disponíveis pro
            # resumo e ofertas (esta função retorna o state inalterado abaixo).
            cart_finalized = bool(cart.get("just_finalized"))
            persisted_items = [] if cart_finalized else (cart.get("items", []) or [])
            persisted_subtotal = 0.0 if cart_finalized else float(cart.get("subtotal", 0) or 0)
            await conn.execute(
                """
                INSERT INTO cart (session_key, items, subtotal, stock_mode,
                                  sales_attempts, updated_at)
                VALUES ($1, $2::jsonb, $3, $4, $5, NOW())
                ON CONFLICT (session_key) DO UPDATE
                SET items          = EXCLUDED.items,
                    subtotal       = EXCLUDED.subtotal,
                    stock_mode     = COALESCE(EXCLUDED.stock_mode, cart.stock_mode),
                    sales_attempts = EXCLUDED.sales_attempts,
                    updated_at     = NOW()
                """,
                session_id,
                persisted_items,
                persisted_subtotal,
                state.get("stock_mode") or "catalogo",
                int(cart.get("sales_attempts", 0) or 0),
            )
            if cart_finalized:
                log.info("context.cart.cleared_on_finalize", session=session_id)

            # Log de conversa — guarda phone separado para agrupar a inbox por contato.
            # Tokens consumidos no turno (in/out/model) vêm do ContextVar populado
            # pelo TokenUsageCallback durante graph.ainvoke. Atribuímos ao row
            # 'assistant' (a chamada LLM produziu a resposta — atribuir ao 'user'
            # daria a impressão errada de que o input do usuário custou tokens).
            phone_clean = state.get("phone") or ""
            usage = aggregate_turn_usage()
            await conn.execute(
                """
                INSERT INTO conversation_logs
                    (session_key, phone, role, content, skill_used,
                     tokens_in, tokens_out, llm_model, created_at)
                VALUES ($1, $5, 'user',      $2, $4, 0,   0,   NULL, NOW()),
                       ($1, $5, 'assistant', $3, $4, $6,  $7,  $8,   NOW())
                """,
                session_id,
                current_msg,
                outbound,
                skill_used,
                phone_clean,
                int(usage["tokens_in"]),
                int(usage["tokens_out"]),
                usage["llm_model"],
            )

    except Exception as exc:
        log.warning("context.db.save_failed", session=session_id, exc=str(exc))

    return {**state, "messages": messages, "final_response": outbound}
