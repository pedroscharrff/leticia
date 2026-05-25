"""
Router de gerenciamento de conversas (pausa/encerramento da IA por cliente).

Usado pelo portal do tenant para:
  • Visualizar quais conversas estão pausadas
  • Pausar a IA manualmente para um cliente (quando atendente humano assume)
  • Retomar a IA
  • Encerrar atendimento (IA não responde mais)

A pausa automática após handoff é tratada em workers/celery_app.py.
"""
from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db.postgres import get_db_conn, tenant_conn
from security import require_tenant_user, TenantUserContext
from services import conversation_state as conv_svc

log = structlog.get_logger()

router = APIRouter(prefix="/portal/conversations", tags=["portal-conversations"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


# ── Schemas ──────────────────────────────────────────────────────────────────

class ConversationStateOut(BaseModel):
    phone: str
    ai_paused: bool
    paused_until: str | None = None
    paused_by: str | None = None
    paused_reason: str | None = None
    closed_at: str | None = None
    updated_at: str | None = None


class PauseIn(BaseModel):
    until_minutes: int | None = Field(
        default=None,
        ge=0,
        description="Minutos até a IA voltar automaticamente. None/0 = indefinido."
    )
    reason: str | None = Field(default=None, max_length=200)


class CloseIn(BaseModel):
    keep_history: bool = Field(
        default=True,
        description="Se False, apaga o histórico do Redis também."
    )


class InboxItem(BaseModel):
    """Item da lista de conversas (à esquerda na inbox)."""
    phone: str
    last_message: str | None = None
    last_role: str | None = None
    last_skill: str | None = None
    last_at: str | None = None
    message_count: int = 0
    ai_paused: bool = False
    paused_until: str | None = None
    paused_reason: str | None = None
    closed_at: str | None = None
    customer_name: str | None = None


class MessageItem(BaseModel):
    id: str
    role: str
    content: str
    skill_used: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    latency_ms: int | None = None
    created_at: str


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ConversationStateOut])
async def list_states(
    user: TenantUser,
    only_paused: bool = False,
    limit: int = 100,
) -> list[ConversationStateOut]:
    """Lista os estados de conversa mais recentes do tenant."""
    rows = await conv_svc.list_recent(
        user.tenant_id, limit=min(limit, 500), only_paused=only_paused,
    )
    return [ConversationStateOut(**r) for r in rows]


@router.get("/{phone}", response_model=ConversationStateOut)
async def get_one(phone: str, user: TenantUser) -> ConversationStateOut:
    state = await conv_svc.get_state(user.tenant_id, phone)
    return ConversationStateOut(**state)


@router.post("/{phone}/pause", response_model=ConversationStateOut)
async def pause_conversation(
    phone: str, body: PauseIn, user: TenantUser,
) -> ConversationStateOut:
    """Pausa a IA para esta conversa."""
    user.assert_role("operator")
    state = await conv_svc.pause(
        user.tenant_id, phone,
        until_minutes=body.until_minutes if body.until_minutes and body.until_minutes > 0 else None,
        by=user.email,
        reason=body.reason or "manual",
    )
    return ConversationStateOut(**state)


@router.post("/{phone}/resume", response_model=ConversationStateOut)
async def resume_conversation(phone: str, user: TenantUser) -> ConversationStateOut:
    """Reativa a IA para esta conversa."""
    user.assert_role("operator")
    state = await conv_svc.resume(user.tenant_id, phone, by=user.email)
    return ConversationStateOut(**state)


@router.post("/{phone}/close", response_model=ConversationStateOut)
async def close_conversation(
    phone: str, body: CloseIn, user: TenantUser,
) -> ConversationStateOut:
    """Encerra o atendimento. A IA não responde mais até alguém dar resume."""
    user.assert_role("operator")
    state = await conv_svc.close(
        user.tenant_id, phone, by=user.email, keep_history=body.keep_history,
    )
    return ConversationStateOut(**state)


# ── Inbox (lista agrupada de conversas) ──────────────────────────────────────

async def _schema_for(tenant_id: str) -> str:
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT schema_name FROM public.tenants WHERE id = $1 AND active = TRUE",
            tenant_id,
        )
    if not row:
        raise HTTPException(404, "Farmácia não encontrada")
    return row["schema_name"]


@router.get("/inbox/list", response_model=list[InboxItem])
async def inbox(
    user: TenantUser,
    limit: int = 100,
    search: str | None = None,
    filter_state: str = "all",  # all | paused | active | closed
) -> list[InboxItem]:
    """Lista conversas distintas (uma por telefone) com prévia + estado.

    Agrupa por phone via conversation_logs, traz última mensagem e contagens.
    Junta com customers (nome) e conversation_state (pausa).
    """
    schema = await _schema_for(user.tenant_id)

    # Filtros opcionais
    search_clause = ""
    if search:
        search_clause = "AND (cl.session_key ILIKE $3 OR c.name ILIKE $3 OR c.phone ILIKE $3)"

    async with tenant_conn(schema) as conn:
        # Agrupa logs por phone — extrai phone do session_key (formato "tid:phone")
        # Usa a última coluna após ':'  para o phone.
        rows = await conn.fetch(
            f"""
            WITH ranked AS (
                SELECT cl.session_key,
                       split_part(cl.session_key, ':', array_length(string_to_array(cl.session_key, ':'), 1)) AS phone,
                       cl.role,
                       cl.content,
                       cl.skill_used,
                       cl.created_at,
                       ROW_NUMBER() OVER (PARTITION BY cl.session_key ORDER BY cl.created_at DESC) AS rn,
                       COUNT(*) OVER (PARTITION BY cl.session_key) AS msg_count
                  FROM conversation_logs cl
            )
            SELECT r.phone,
                   r.session_key,
                   r.role        AS last_role,
                   r.content     AS last_content,
                   r.skill_used  AS last_skill,
                   r.created_at  AS last_at,
                   r.msg_count,
                   c.name        AS customer_name
              FROM ranked r
              LEFT JOIN customers c ON c.phone = r.phone
             WHERE r.rn = 1
               {search_clause}
             ORDER BY r.created_at DESC
             LIMIT $1
            """ + (" OFFSET $2" if False else ""),
            *([limit, 0, f"%{search}%"] if search else [limit]),
        )

    # Junta com conversation_state (uma query só)
    phones = [r["phone"] for r in rows if r["phone"]]
    state_map: dict[str, dict] = {}
    if phones:
        async with get_db_conn() as conn:
            state_rows = await conn.fetch(
                """
                SELECT phone, ai_paused, paused_until, paused_reason, closed_at
                  FROM public.conversation_state
                 WHERE tenant_id = $1 AND phone = ANY($2::text[])
                """,
                user.tenant_id, phones,
            )
            for sr in state_rows:
                state_map[sr["phone"]] = {
                    "ai_paused": sr["ai_paused"],
                    "paused_until": sr["paused_until"].isoformat() if sr["paused_until"] else None,
                    "paused_reason": sr["paused_reason"],
                    "closed_at": sr["closed_at"].isoformat() if sr["closed_at"] else None,
                }

    result: list[InboxItem] = []
    for r in rows:
        phone = r["phone"]
        if not phone:
            continue
        st = state_map.get(phone, {})

        # Filtro por estado
        is_paused = bool(st.get("ai_paused"))
        is_closed = bool(st.get("closed_at"))
        if filter_state == "paused" and not is_paused:
            continue
        if filter_state == "active" and (is_paused or is_closed):
            continue
        if filter_state == "closed" and not is_closed:
            continue

        result.append(InboxItem(
            phone=phone,
            last_message=(r["last_content"] or "")[:160],
            last_role=r["last_role"],
            last_skill=r["last_skill"],
            last_at=r["last_at"].isoformat() if r["last_at"] else None,
            message_count=int(r["msg_count"] or 0),
            ai_paused=is_paused,
            paused_until=st.get("paused_until"),
            paused_reason=st.get("paused_reason"),
            closed_at=st.get("closed_at"),
            customer_name=r["customer_name"],
        ))

    return result


@router.get("/{phone}/messages", response_model=list[MessageItem])
async def conversation_messages(
    phone: str,
    user: TenantUser,
    limit: int = 200,
) -> list[MessageItem]:
    """Retorna o histórico completo de mensagens de um telefone (ordem crono)."""
    schema = await _schema_for(user.tenant_id)
    async with tenant_conn(schema) as conn:
        rows = await conn.fetch(
            """
            SELECT id::text, role, content, skill_used,
                   tokens_in, tokens_out, latency_ms, created_at
              FROM conversation_logs
             WHERE session_key LIKE $1
             ORDER BY created_at ASC
             LIMIT $2
            """,
            f"%:{phone}",
            limit,
        )
    return [
        MessageItem(
            id=r["id"],
            role=r["role"],
            content=r["content"] or "",
            skill_used=r["skill_used"],
            tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"],
            latency_ms=r["latency_ms"],
            created_at=r["created_at"].isoformat(),
        )
        for r in rows
    ]
