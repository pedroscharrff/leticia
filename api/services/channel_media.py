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
# STUB: endpoints de imagem/áudio ainda não fornecidos pelo usuário.
# Atualizar quando o usuário enviar a spec; ver
# memory/reference_channel_media_endpoints.md.

async def _clickmassa_send_image(
    cfg: dict[str, Any], phone: str, caption: str, media_url: str,
) -> dict[str, Any]:
    log.warning("channel_media.clickmassa.image.not_configured",
                phone_prefix=phone[:4])
    return {
        "ok": False, "status_code": None, "response": None,
        "error": "ClickMassa: endpoint de envio de imagem ainda não configurado.",
    }


async def _clickmassa_send_audio(
    cfg: dict[str, Any], phone: str, caption: str, media_url: str,
) -> dict[str, Any]:
    log.warning("channel_media.clickmassa.audio.not_configured",
                phone_prefix=phone[:4])
    return {
        "ok": False, "status_code": None, "response": None,
        "error": "ClickMassa: endpoint de envio de áudio ainda não configurado.",
    }


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
