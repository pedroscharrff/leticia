"""
Registro de envio de MÍDIA (imagem/áudio) por provider de canal.

Texto puro já tem caminho próprio (broker `reply_url` + `reply_body_template`
ou `callback_url` legado). Este módulo cuida APENAS de imagem e áudio, que
precisam de endpoint dedicado em cada provider.

Para adicionar suporte a um provider novo:
  1. Implemente duas funções async `<provider>_send_image(cfg, phone, caption,
     media_url) -> dict` e `<provider>_send_audio(...)`.
  2. Registre em `PROVIDERS` abaixo.
  3. Atualize a memória [[reference_channel_media_endpoints]] com a spec.

Spec compartilhada para todas as funções:
  cfg:        dict da integração ativa (ex.: handoff_config / channel config —
              base_url, token, etc.)
  phone:      número do cliente (apenas dígitos)
  caption:    texto que acompanha a mídia (title + description da oferta)
  media_url:  URL pública da imagem/áudio (MinIO)
  return:     {"ok": bool, "status_code": int|None, "response": dict|None,
               "error": str|None}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

import httpx
import structlog

log = structlog.get_logger()


# ── Tipo: spec por provider ─────────────────────────────────────────────────

ProviderSendFn = Callable[
    [dict[str, Any], str, str, str],
    Awaitable[dict[str, Any]],
]


@dataclass(frozen=True)
class ProviderMediaSpec:
    send_image: ProviderSendFn
    send_audio: ProviderSendFn


# ── ClickMassa (TalkFarma) ──────────────────────────────────────────────────
# Endpoint único para imagem e áudio: POST {base_url}/?token={token}
# Body: {"number": "...", "body": "<caption>", "externalKey": "IA", "mediaUrl": "..."}
# Provider infere o tipo (image/audio) pelo Content-Type da mediaUrl.

_CLICKMASSA_EXTERNAL_KEY = "IA"


async def _clickmassa_send_media(
    cfg: dict[str, Any], phone: str, caption: str, media_url: str,
    *, kind: str,
) -> dict[str, Any]:
    base_url = (cfg.get("base_url") or "").strip().rstrip("/")
    token    = (cfg.get("token") or "").strip()

    missing = []
    if not base_url:  missing.append("base_url")
    if not token:     missing.append("token")
    if not phone:     missing.append("phone")
    if not media_url: missing.append("media_url")
    if missing:
        return {
            "ok": False, "status_code": None, "response": None,
            "error": f"Config ClickMassa incompleta. Faltando: {', '.join(missing)}",
        }

    url = f"{base_url}/?token={token}"
    payload = {
        "number":      phone,
        "body":        (caption or "").strip(),
        "externalKey": _CLICKMASSA_EXTERNAL_KEY,
        "mediaUrl":    media_url,
    }

    log.info("channel_media.clickmassa.dispatching",
             kind=kind, url_prefix=base_url, phone_prefix=phone[:4])
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {"_text": resp.text[:2000]}
        ok = 200 <= resp.status_code < 300
        if ok:
            log.info("channel_media.clickmassa.success",
                     kind=kind, status=resp.status_code)
        else:
            log.warning("channel_media.clickmassa.bad_status",
                        kind=kind, status=resp.status_code,
                        preview=str(data)[:300])
        return {
            "ok": ok, "status_code": resp.status_code, "response": data,
            "error": None if ok else f"ClickMassa retornou {resp.status_code}",
        }
    except Exception as exc:  # noqa: BLE001
        log.error("channel_media.clickmassa.failed", kind=kind, error=str(exc))
        return {
            "ok": False, "status_code": None, "response": None,
            "error": f"Falha ao conectar ClickMassa: {exc}",
        }


async def _clickmassa_send_image(
    cfg: dict[str, Any], phone: str, caption: str, media_url: str,
) -> dict[str, Any]:
    return await _clickmassa_send_media(cfg, phone, caption, media_url, kind="image")


async def _clickmassa_send_audio(
    cfg: dict[str, Any], phone: str, caption: str, media_url: str,
) -> dict[str, Any]:
    return await _clickmassa_send_media(cfg, phone, caption, media_url, kind="audio")


# ── Registro ────────────────────────────────────────────────────────────────

PROVIDERS: dict[str, ProviderMediaSpec] = {
    "clickmassa": ProviderMediaSpec(
        send_image=_clickmassa_send_image,
        send_audio=_clickmassa_send_audio,
    ),
    # Adicione novos providers aqui:
    # "zapi":     ProviderMediaSpec(send_image=..., send_audio=...),
    # "wa_cloud": ProviderMediaSpec(send_image=..., send_audio=...),
}


def supports(provider: str, media_type: str) -> bool:
    """True se há implementação não-stub para (provider, media_type)."""
    spec = PROVIDERS.get((provider or "").lower())
    if not spec:
        return False
    # Distinção stub vs real: stubs ClickMassa retornam ok=False com
    # mensagem 'não configurado'. Aqui consideramos "registrado" — a
    # detecção de stub real fica para o caller, que verá ok=False.
    return media_type in ("image", "audio")


async def send_media(
    provider: str, cfg: dict[str, Any], *,
    media_type: str, phone: str, caption: str, media_url: str,
) -> dict[str, Any]:
    """Despacha o envio de mídia para o provider.

    Retorna {"ok": False, "error": ...} quando provider/media_type não estão
    registrados — caller deve cair em fallback de texto.
    """
    spec = PROVIDERS.get((provider or "").lower())
    if not spec:
        return {
            "ok": False, "status_code": None, "response": None,
            "error": f"Provider '{provider}' sem suporte a mídia.",
        }
    if media_type == "image":
        return await spec.send_image(cfg, phone, caption, media_url)
    if media_type == "audio":
        return await spec.send_audio(cfg, phone, caption, media_url)
    return {
        "ok": False, "status_code": None, "response": None,
        "error": f"media_type inválido: {media_type!r}",
    }
