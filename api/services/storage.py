"""
Object storage para mídia (imagem/áudio) das ofertas — wrapper enxuto sobre
MinIO/S3.

Princípios:
- Bucket único, namespace por tenant via key prefix `{tenant_id}/{uuid}.{ext}`.
- URL pública (sem assinatura) — brokers de canal (Z-API, WA Cloud, etc.)
  precisam baixar a URL do lado deles, sem auth.
- Bootstrap idempotente: cria bucket + política pública no primeiro upload.
- Falha de MinIO NÃO derruba a API — exceções viram HTTP 503 no router.
"""
from __future__ import annotations

import json
import uuid
from typing import Tuple

import structlog
from minio import Minio
from minio.error import S3Error

from config import settings

log = structlog.get_logger()

# ── Whitelists e limites ────────────────────────────────────────────────────

IMAGE_MIMES = {
    "image/jpeg": "jpg",
    "image/jpg":  "jpg",
    "image/png":  "png",
    "image/webp": "webp",
}

AUDIO_MIMES = {
    "audio/mpeg": "mp3",
    "audio/mp3":  "mp3",
    "audio/ogg":  "ogg",
    "audio/mp4":  "m4a",
    "audio/aac":  "aac",
    "audio/webm": "webm",
}

MAX_IMAGE_BYTES = 5  * 1024 * 1024   # 5 MB (limite WA Cloud)
MAX_AUDIO_BYTES = 16 * 1024 * 1024   # 16 MB (limite WA Cloud)


class StorageError(Exception):
    """Erro estruturado para o router converter em HTTP."""

    def __init__(self, message: str, *, status_code: int = 500):
        super().__init__(message)
        self.status_code = status_code


# ── Cliente lazy ─────────────────────────────────────────────────────────────

_client: Minio | None = None
_bucket_ready: bool = False


def _get_client() -> Minio:
    global _client
    if _client is None:
        if not settings.minio_secret_key:
            raise StorageError(
                "MinIO não configurado (MINIO_SECRET_KEY ausente).",
                status_code=503,
            )
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
    return _client


def _ensure_bucket(client: Minio) -> None:
    """Cria o bucket e marca como público. Idempotente."""
    global _bucket_ready
    if _bucket_ready:
        return
    bucket = settings.minio_bucket
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            log.info("storage.bucket_created", bucket=bucket)
        # Política pública de leitura (sem listing)
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"AWS": ["*"]},
                "Action":    ["s3:GetObject"],
                "Resource":  [f"arn:aws:s3:::{bucket}/*"],
            }],
        }
        client.set_bucket_policy(bucket, json.dumps(policy))
        _bucket_ready = True
    except S3Error as exc:
        log.error("storage.bucket_setup_failed", bucket=bucket, code=exc.code)
        raise StorageError(f"Falha ao preparar bucket: {exc.code}", status_code=503)


# ── API pública ──────────────────────────────────────────────────────────────

def classify_mime(mime: str) -> tuple[str, str]:
    """Retorna ('image'|'audio', ext) ou levanta StorageError 422."""
    mime = (mime or "").lower().split(";")[0].strip()
    if mime in IMAGE_MIMES:
        return "image", IMAGE_MIMES[mime]
    if mime in AUDIO_MIMES:
        return "audio", AUDIO_MIMES[mime]
    allowed = ", ".join(sorted({*IMAGE_MIMES, *AUDIO_MIMES}))
    raise StorageError(
        f"Tipo de arquivo não suportado ({mime!r}). Aceitos: {allowed}.",
        status_code=422,
    )


def upload_offer_media(
    tenant_id: str, data: bytes, mime: str,
) -> Tuple[str, str, str]:
    """Sobe mídia para o bucket. Retorna (public_url, media_type, object_key).

    Síncrono: minio SDK é blocking. Chamadores async devem rodar em thread.
    """
    media_type, ext = classify_mime(mime)
    size = len(data)
    if media_type == "image" and size > MAX_IMAGE_BYTES:
        raise StorageError(
            f"Imagem acima do limite de {MAX_IMAGE_BYTES // (1024*1024)} MB.",
            status_code=422,
        )
    if media_type == "audio" and size > MAX_AUDIO_BYTES:
        raise StorageError(
            f"Áudio acima do limite de {MAX_AUDIO_BYTES // (1024*1024)} MB.",
            status_code=422,
        )

    client = _get_client()
    _ensure_bucket(client)

    key = f"{tenant_id}/{uuid.uuid4().hex}.{ext}"
    import io
    try:
        client.put_object(
            settings.minio_bucket,
            key,
            io.BytesIO(data),
            length=size,
            content_type=mime,
        )
    except S3Error as exc:
        log.error("storage.put_failed", code=exc.code, key=key)
        raise StorageError(f"Falha ao subir arquivo: {exc.code}", status_code=503)

    public_url = f"{settings.minio_public_url.rstrip('/')}/{settings.minio_bucket}/{key}"
    log.info("storage.uploaded",
             tenant=tenant_id, media_type=media_type, size=size, key=key)
    return public_url, media_type, key
