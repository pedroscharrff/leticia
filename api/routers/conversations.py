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
