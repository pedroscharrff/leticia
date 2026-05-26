"""
Ofertas/promoções por tenant (capability `sales.pre_handoff_offers`).

Portal (manager+):
  GET    /portal/offers
  POST   /portal/offers
  PATCH  /portal/offers/{id}
  DELETE /portal/offers/{id}
"""
from __future__ import annotations

from datetime import datetime
from typing import Annotated

import asyncio

import structlog
from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File
from pydantic import BaseModel, Field

from db.postgres import get_db_conn
from security import require_tenant_user, TenantUserContext
from services.audit import log_event
from services.storage import (
    StorageError, upload_offer_media, classify_mime,
    MAX_IMAGE_BYTES, MAX_AUDIO_BYTES,
)

log = structlog.get_logger()
router = APIRouter(prefix="/portal/offers", tags=["portal:offers"])
TenantUser = Annotated[TenantUserContext, Depends(require_tenant_user)]


class OfferIn(BaseModel):
    title:       str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=1000)
    valid_from:  datetime | None = None
    valid_until: datetime | None = None
    priority:    int  = Field(default=0, ge=0, le=1000)
    active:      bool = True


class OfferUpdate(BaseModel):
    title:       str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    valid_from:  datetime | None = None
    valid_until: datetime | None = None
    priority:    int  | None = Field(default=None, ge=0, le=1000)
    active:      bool | None = None


class OfferOut(BaseModel):
    id:          str
    title:       str
    description: str
    valid_from:  datetime | None
    valid_until: datetime | None
    priority:    int
    active:      bool
    media_type:  str | None = None
    media_url:   str | None = None
    media_mime:  str | None = None
    created_at:  datetime
    updated_at:  datetime


def _row(r) -> OfferOut:
    # Após a migration 037, todas as queries SELECT * trazem media_*.
    keys = set(r.keys())
    return OfferOut(
        id=str(r["id"]),
        title=r["title"],
        description=r["description"] or "",
        valid_from=r["valid_from"],
        valid_until=r["valid_until"],
        priority=int(r["priority"] or 0),
        active=bool(r["active"]),
        media_type=r["media_type"] if "media_type" in keys else None,
        media_url=r["media_url"]  if "media_url"  in keys else None,
        media_mime=r["media_mime"] if "media_mime" in keys else None,
        created_at=r["created_at"],
        updated_at=r["updated_at"],
    )


def _validate_window(valid_from: datetime | None, valid_until: datetime | None) -> None:
    if valid_from and valid_until and valid_from > valid_until:
        raise HTTPException(
            status_code=422,
            detail="valid_from deve ser ≤ valid_until",
        )


@router.get("", response_model=list[OfferOut])
async def list_offers(user: TenantUser) -> list[OfferOut]:
    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT * FROM public.offers
             WHERE tenant_id = $1
             ORDER BY active DESC, priority DESC, created_at DESC
            """,
            user.tenant_id,
        )
    return [_row(r) for r in rows]


@router.post("", response_model=OfferOut)
async def create_offer(payload: OfferIn, user: TenantUser) -> OfferOut:
    user.assert_role("manager")
    _validate_window(payload.valid_from, payload.valid_until)

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO public.offers
                (tenant_id, title, description, valid_from, valid_until,
                 priority, active)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING *
            """,
            user.tenant_id, payload.title, payload.description,
            payload.valid_from, payload.valid_until,
            payload.priority, payload.active,
        )

    await log_event(
        action="offer.create",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=str(row["id"]),
        meta={"title": payload.title, "priority": payload.priority},
    )
    return _row(row)


@router.patch("/{offer_id}", response_model=OfferOut)
async def update_offer(
    offer_id: str,
    payload: OfferUpdate,
    user: TenantUser,
) -> OfferOut:
    user.assert_role("manager")
    data = payload.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")

    # Quando a janela é tocada, valida-a contra os valores efetivos
    if "valid_from" in data or "valid_until" in data:
        async with get_db_conn() as conn:
            cur = await conn.fetchrow(
                "SELECT valid_from, valid_until FROM public.offers "
                "WHERE id = $1 AND tenant_id = $2",
                offer_id, user.tenant_id,
            )
        if not cur:
            raise HTTPException(status_code=404, detail="Oferta não encontrada")
        vf = data.get("valid_from", cur["valid_from"])
        vu = data.get("valid_until", cur["valid_until"])
        _validate_window(vf, vu)

    updates: list[str] = []
    params: list = []
    i = 1
    for col in ("title", "description", "valid_from", "valid_until",
                "priority", "active"):
        if col in data:
            updates.append(f"{col} = ${i}")
            params.append(data[col])
            i += 1

    updates.append("updated_at = NOW()")
    params.extend([offer_id, user.tenant_id])
    sql = (f"UPDATE public.offers SET {', '.join(updates)} "
           f"WHERE id = ${i} AND tenant_id = ${i+1} RETURNING *")

    async with get_db_conn() as conn:
        row = await conn.fetchrow(sql, *params)
    if not row:
        raise HTTPException(status_code=404, detail="Oferta não encontrada")

    await log_event(
        action="offer.update",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=offer_id,
        meta={k: data[k] for k in data},
    )
    return _row(row)


@router.delete("/{offer_id}")
async def delete_offer(offer_id: str, user: TenantUser) -> Response:
    user.assert_role("manager")
    async with get_db_conn() as conn:
        ok = await conn.fetchval(
            "DELETE FROM public.offers "
            "WHERE id = $1 AND tenant_id = $2 RETURNING id",
            offer_id, user.tenant_id,
        )
    if not ok:
        raise HTTPException(status_code=404, detail="Oferta não encontrada")
    await log_event(
        action="offer.delete",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=offer_id, meta={},
    )
    return Response(status_code=204)


# ── Mídia da oferta (imagem OU áudio) ───────────────────────────────────────

_MAX_UPLOAD_BYTES = max(MAX_IMAGE_BYTES, MAX_AUDIO_BYTES)


@router.post("/{offer_id}/media", response_model=OfferOut)
async def upload_media(
    offer_id: str,
    user: TenantUser,
    file: UploadFile = File(...),
) -> OfferOut:
    """Upload de imagem (jpg/png/webp) ou áudio (mp3/ogg/m4a/aac) para a
    oferta. Substitui mídia existente (sobrescreve as colunas, não apaga o
    arquivo antigo do bucket — cleanup futuro).
    """
    user.assert_role("manager")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=422, detail="Arquivo vazio.")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=422,
            detail=f"Arquivo acima do limite ({_MAX_UPLOAD_BYTES // (1024*1024)} MB).",
        )
    mime = file.content_type or ""

    # Validação de MIME antes de subir (rápido, evita gastar MinIO)
    try:
        classify_mime(mime)
    except StorageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    # Upload é blocking (minio SDK), roda em thread
    try:
        public_url, media_type, _key = await asyncio.to_thread(
            upload_offer_media, user.tenant_id, data, mime,
        )
    except StorageError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc))

    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.offers
               SET media_type = $1, media_url = $2, media_mime = $3,
                   updated_at = NOW()
             WHERE id = $4 AND tenant_id = $5
             RETURNING *
            """,
            media_type, public_url, mime, offer_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Oferta não encontrada")

    await log_event(
        action="offer.media_upload",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=offer_id,
        meta={"media_type": media_type, "mime": mime, "size": len(data)},
    )
    return _row(row)


@router.delete("/{offer_id}/media", response_model=OfferOut)
async def delete_media(offer_id: str, user: TenantUser) -> OfferOut:
    """Limpa mídia da oferta (deixa o arquivo no bucket — cleanup futuro)."""
    user.assert_role("manager")
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            """
            UPDATE public.offers
               SET media_type = NULL, media_url = NULL, media_mime = NULL,
                   updated_at = NOW()
             WHERE id = $1 AND tenant_id = $2
             RETURNING *
            """,
            offer_id, user.tenant_id,
        )
    if not row:
        raise HTTPException(status_code=404, detail="Oferta não encontrada")
    await log_event(
        action="offer.media_delete",
        actor_id=user.email, actor_type="user",
        tenant_id=user.tenant_id, target=offer_id, meta={},
    )
    return _row(row)
