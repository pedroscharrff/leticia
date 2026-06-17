"""
Multimodal ingestion: transcribe audio + describe images from WhatsApp.

Used by the LangGraph `ingest_media` node. Keeps the rest of the agent
pipeline blissfully unaware of binary payloads — everything downstream sees
plain text injected into `current_message`.

Providers:
  • Audio  → Groq Whisper (default) or OpenAI Whisper (fallback)
  • Image  → Anthropic Claude (vision, multimodal) — already supported by
             the existing provider factory; no extra dep needed.

The functions accept raw bytes so the caller controls how to obtain them
(WhatsApp Cloud needs a token to download; Z-API is a public URL).
"""
from __future__ import annotations

import base64
from typing import Literal

import httpx
import structlog

from config import settings

log = structlog.get_logger()


# ── Audio transcription ──────────────────────────────────────────────────────

async def transcribe_audio(
    audio_bytes: bytes,
    mime_type: str = "audio/ogg",
    language: str = "pt",
) -> str:
    """
    Transcribe an audio clip to text. Returns "" on failure (caller decides
    how to react — we never raise into the agent loop).

    WhatsApp voice notes arrive as OGG/Opus, which Whisper handles natively.
    """
    if not audio_bytes:
        return ""
    if len(audio_bytes) > settings.media_max_audio_bytes:
        log.warning("media.audio.too_large", size=len(audio_bytes))
        return ""

    provider = (settings.media_transcription_provider or "groq").lower()

    try:
        if provider == "groq":
            return await _transcribe_groq(audio_bytes, mime_type, language)
        if provider == "openai":
            return await _transcribe_openai(audio_bytes, mime_type, language)
        log.warning("media.audio.unknown_provider", provider=provider)
        return ""
    except Exception as exc:  # noqa: BLE001
        log.error("media.audio.transcribe_failed", provider=provider, exc=str(exc))
        return ""


async def _transcribe_groq(audio: bytes, mime: str, language: str) -> str:
    if not settings.groq_api_key:
        log.warning("media.audio.groq.no_key")
        return ""
    filename = "audio." + (_ext_for_mime(mime) or "ogg")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            files={"file": (filename, audio, mime or "audio/ogg")},
            data={
                "model": settings.media_transcription_model or "whisper-large-v3",
                "language": language,
                "response_format": "text",
            },
        )
    if resp.status_code >= 400:
        log.error("media.audio.groq.bad_status",
                  status=resp.status_code, body=resp.text[:300])
        return ""
    return resp.text.strip()


async def _transcribe_openai(audio: bytes, mime: str, language: str) -> str:
    if not settings.openai_api_key:
        log.warning("media.audio.openai.no_key")
        return ""
    filename = "audio." + (_ext_for_mime(mime) or "ogg")
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            files={"file": (filename, audio, mime or "audio/ogg")},
            data={
                "model": "whisper-1",
                "language": language,
                "response_format": "text",
            },
        )
    if resp.status_code >= 400:
        log.error("media.audio.openai.bad_status",
                  status=resp.status_code, body=resp.text[:300])
        return ""
    return resp.text.strip()


# ── Image description / OCR ──────────────────────────────────────────────────

async def describe_image(
    image_bytes: bytes,
    mime_type: str = "image/jpeg",
    caption: str = "",
    purpose: Literal["pharmacy", "generic"] = "pharmacy",
) -> str:
    """
    Run a vision LLM on the image and return a textual description.

    For pharmacy use case we instruct the model to extract:
      - Medication / product names visible
      - Dosage, posology if it's a prescription
      - Visible quantities
      - Any plain text (OCR pass)

    The output is plain text and gets injected into `current_message` so
    existing skills (which only know how to read text) can act on it.
    """
    if not image_bytes:
        return ""
    if len(image_bytes) > settings.media_max_image_bytes:
        log.warning("media.image.too_large", size=len(image_bytes))
        return ""

    # Provider preference: Anthropic Claude (default), fallback Gemini.
    provider = (settings.media_vision_provider or "anthropic").lower()
    if provider == "anthropic" and not settings.anthropic_api_key:
        if settings.google_api_key:
            provider = "google"  # auto-fallback
        else:
            log.warning("media.image.no_keys_configured")
            return ""
    if provider == "google" and not settings.google_api_key:
        if settings.anthropic_api_key:
            provider = "anthropic"
        else:
            log.warning("media.image.no_keys_configured")
            return ""

    if purpose == "pharmacy":
        system = (
            "Você é um assistente de farmácia analisando uma imagem enviada "
            "pelo cliente via WhatsApp. Descreva em português, de forma "
            "objetiva e estruturada, o que aparece na imagem. Se for "
            "RECEITA MÉDICA, extraia: nome do(s) medicamento(s), dosagem, "
            "posologia, quantidade, e nome do prescritor se visível. Se for "
            "FOTO DE PRODUTO, identifique nome comercial, princípio ativo, "
            "apresentação e fabricante. Se for outra coisa, descreva "
            "brevemente. Faça também OCR de qualquer texto visível. "
            "Não invente informações que não estão na imagem."
        )
    else:
        system = (
            "Descreva objetivamente em português o que aparece na imagem, "
            "incluindo OCR de qualquer texto visível."
        )

    user_text = (
        f"Legenda enviada pelo cliente: {caption.strip()}\n\nAnalise a imagem."
        if caption.strip() else "Analise a imagem."
    )

    if provider == "anthropic":
        return await _describe_anthropic(image_bytes, mime_type, system, user_text)
    if provider == "google":
        return await _describe_gemini(image_bytes, mime_type, system, user_text)
    log.warning("media.image.unknown_provider", provider=provider)
    return ""


async def _describe_anthropic(image_bytes: bytes, mime_type: str,
                              system: str, user_text: str) -> str:
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        log.error("media.image.anthropic_not_installed")
        return ""
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    model = settings.media_vision_model or "claude-sonnet-4-6"
    try:
        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        resp = await client.messages.create(
            model=model,
            max_tokens=800,
            system=system,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64",
                        "media_type": mime_type or "image/jpeg",
                        "data": encoded,
                    }},
                    {"type": "text", "text": user_text},
                ],
            }],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
        return "\n".join(parts).strip()
    except Exception as exc:  # noqa: BLE001
        log.error("media.image.anthropic.failed", exc=str(exc))
        return ""


async def _describe_gemini(image_bytes: bytes, mime_type: str,
                           system: str, user_text: str) -> str:
    """Fallback via Gemini quando Anthropic não está configurado."""
    encoded = base64.standard_b64encode(image_bytes).decode("ascii")
    model = settings.media_vision_model
    # Se o modelo configurado for um Claude, troca por um Gemini default
    if not model or "claude" in model.lower():
        model = "gemini-2.5-flash"  # 2.0 foi descontinuado na API (404); 2.5-flash tem visão
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={settings.google_api_key}")
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": mime_type or "image/jpeg",
                                 "data": encoded}},
                {"text": user_text},
            ],
        }],
        "generationConfig": {"maxOutputTokens": 800, "temperature": 0.2},
    }
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, json=body)
        if resp.status_code >= 400:
            log.error("media.image.gemini.bad_status",
                      status=resp.status_code, body=resp.text[:300])
            return ""
        data = resp.json()
        parts = (data.get("candidates", [{}])[0]
                 .get("content", {}).get("parts", []))
        return "\n".join(p.get("text", "") for p in parts if p.get("text")).strip()
    except Exception as exc:  # noqa: BLE001
        log.error("media.image.gemini.failed", exc=str(exc))
        return ""


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ext_for_mime(mime: str | None) -> str:
    if not mime:
        return ""
    mime = mime.split(";")[0].strip().lower()
    return {
        "audio/ogg": "ogg",
        "audio/opus": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/x-m4a": "m4a",
        "audio/wav": "wav",
        "audio/webm": "webm",
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
    }.get(mime, "")


async def fetch_media_bytes(url: str) -> bytes | None:
    """Download bytes from a public URL (used by Z-API)."""
    if not url:
        return None
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            log.warning("media.fetch.bad_status", status=resp.status_code, url=url)
            return None
        return resp.content
    except Exception as exc:  # noqa: BLE001
        log.error("media.fetch.failed", url=url, exc=str(exc))
        return None
