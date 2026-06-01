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

log = structlog.get_logger()

_TTL_DEFAULT = 1800  # segundos (30 min) — fallback se DB/cache falhar


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

    updates: dict = {}

    # ── Histórico do Redis ────────────────────────────────────────────────────
    try:
        from db.redis_client import get_redis
        redis = get_redis()
        raw = await redis.get(f"hist:{session_id}")
        messages = json.loads(raw) if raw else []
        # Trunca para as últimas N mensagens para economizar tokens
        updates["messages"] = messages[-max_messages:]
    except Exception as exc:
        log.warning("context.redis.load_failed", session=session_id, exc=str(exc))
        updates["messages"] = []

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
        await redis.setex(f"hist:{session_id}", ttl_s, json.dumps(messages))
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
                cart.get("items", []) or [],
                float(cart.get("subtotal", 0) or 0),
                state.get("stock_mode") or "catalogo",
                int(cart.get("sales_attempts", 0) or 0),
            )

            # Log de conversa — guarda phone separado para agrupar a inbox por contato.
            phone_clean = state.get("phone") or ""
            await conn.execute(
                """
                INSERT INTO conversation_logs
                    (session_key, phone, role, content, skill_used, created_at)
                VALUES ($1, $5, 'user',      $2, $4, NOW()),
                       ($1, $5, 'assistant', $3, $4, NOW())
                """,
                session_id,
                current_msg,
                final_response,
                skill_used,
                phone_clean,
            )

    except Exception as exc:
        log.warning("context.db.save_failed", session=session_id, exc=str(exc))

    return {**state, "messages": messages}
