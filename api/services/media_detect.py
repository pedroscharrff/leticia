"""
Auto-detecta mídia em payloads de webhook conhecidos (Z-API, WhatsApp Cloud,
Meta WABA, WAHA) sem precisar de mapping manual.

Devolve um dict com chaves canônicas:
    {
      "media_type": "audio" | "image" | "video" | "document",
      "media_mime": "...",
      "media_url":  "https://...",   # ou None se só há media_id
      "media_id":   "..."             # WA Cloud — precisa download via token
    }

Retorna None se o payload não contém mídia reconhecível.

Filosofia: ser permissivo. Se em dúvida, retorna o que conseguir extrair —
o ingest_media node faz fallback gracioso quando faltar bytes.
"""
from __future__ import annotations

from typing import Any


def detect_media(payload: Any) -> dict | None:
    if not isinstance(payload, dict):
        return None

    # ── Z-API (formato ReceivedCallback) ────────────────────────────────────
    # { type: "ReceivedCallback", audio: { audioUrl, mimeType }, ... }
    for key, mtype, url_field in (
        ("audio",    "audio",    "audioUrl"),
        ("image",    "image",    "imageUrl"),
        ("video",    "video",    "videoUrl"),
        ("document", "document", "documentUrl"),
        ("ptt",      "audio",    "audioUrl"),  # push-to-talk = nota de voz
    ):
        node = payload.get(key)
        if isinstance(node, dict) and (node.get(url_field) or node.get("url")):
            return {
                "media_type": mtype,
                "media_mime": node.get("mimeType") or node.get("mime_type"),
                "media_url":  node.get(url_field) or node.get("url"),
                "media_id":   None,
            }

    # ── WhatsApp Cloud API (Meta oficial) ───────────────────────────────────
    # { entry: [{ changes: [{ value: { messages: [{ type, image: {id, mime_type}, ... }] }}]}]}
    try:
        msg = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        mtype = msg.get("type")
        if mtype in ("image", "audio", "voice", "video", "document"):
            sub = msg.get(mtype, {}) or {}
            norm = "audio" if mtype == "voice" else mtype
            return {
                "media_type": norm,
                "media_mime": sub.get("mime_type"),
                "media_url":  None,            # precisa resolver via Graph API
                "media_id":   sub.get("id"),
            }
    except (KeyError, IndexError, TypeError):
        pass

    # ── ClickMassa / WAHA / Evolution API e variantes ───────────────────────
    # ClickMassa: { message: { mediaType: "image", mediaUrl: "...", body: "caption" } }
    # WAHA:       { message: { type: "image", mediaUrl: "...", mimetype: "..." } }
    msg = payload.get("message") if isinstance(payload.get("message"), dict) else None
    if msg:
        # mediaType (ClickMassa) tem precedência; cai pra type (WAHA) se faltar
        mtype = msg.get("mediaType") or msg.get("type")
        if mtype in ("audio", "image", "video", "document", "ptt", "voice"):
            norm = "audio" if mtype in ("ptt", "voice") else mtype
            url = (msg.get("mediaUrl") or msg.get("url")
                   or msg.get("audioUrl") or msg.get("imageUrl"))
            # Mime pode estar no próprio msg ou aninhado em raw.Message.*Message
            mime = msg.get("mimetype") or msg.get("mimeType")
            if not mime:
                raw_msg = (msg.get("raw") or {}).get("Message") or {}
                for k in ("imageMessage", "audioMessage", "videoMessage",
                          "documentMessage"):
                    if isinstance(raw_msg.get(k), dict):
                        mime = raw_msg[k].get("mimetype")
                        break
            if url:
                return {
                    "media_type": norm,
                    "media_mime": mime,
                    "media_url":  url,
                    "media_id":   None,
                }

    # ── Genérico: campos óbvios no nível raiz ───────────────────────────────
    # Cobre integrações customizadas que mandam { audio_url, mime_type, ... }
    for url_field, mtype in (
        ("audio_url", "audio"),
        ("audioUrl",  "audio"),
        ("image_url", "image"),
        ("imageUrl",  "image"),
        ("video_url", "video"),
        ("videoUrl",  "video"),
    ):
        if payload.get(url_field):
            return {
                "media_type": mtype,
                "media_mime": payload.get("mime_type") or payload.get("mimeType"),
                "media_url":  payload.get(url_field),
                "media_id":   None,
            }

    return None


def enrich_canonical_with_media(
    canonical_input: dict,
    raw_payload: Any,
) -> dict:
    """
    Se o canonical_input ainda não tem media_type definido pelo mapping,
    tenta detectar no payload bruto e injetar. Retorna o mesmo dict por
    conveniência (mutado in-place).
    """
    if canonical_input.get("media_type"):
        return canonical_input
    detected = detect_media(raw_payload)
    if detected:
        canonical_input.update(detected)
    return canonical_input
