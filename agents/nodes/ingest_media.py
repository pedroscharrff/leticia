"""
Node: ingest_media

Runs right after load_context, before orchestrator. If the inbound message
carried image/audio metadata (set by the channel adapters), this node:

  • Audio  → transcribes via Groq Whisper, replaces `current_message`
             with the transcript (preserving any original caption).
  • Image  → runs a vision LLM, prepends the description to
             `current_message`. Caption is preserved.

Plain-text messages flow through untouched.

State keys consumed:
    media_type, media_mime, media_url, media_id, media_bytes (optional)

State keys written:
    current_message     — replaced/augmented with the textual rendering
    media_transcript    — raw transcript (audit / persona prompts)
    media_description   — raw vision output
"""
from __future__ import annotations

import base64
import structlog

from agents.state import AgentState

log = structlog.get_logger()


async def ingest_media(state: AgentState) -> AgentState:
    media_type = state.get("media_type")
    if not media_type:
        return state

    try:
        return await _do_ingest(state)
    except Exception as exc:  # noqa: BLE001
        # Falha catastrófica (rede, lib quebrada, etc) — JAMAIS derruba o agente.
        log.error("ingest_media.unhandled_error",
                  media_type=media_type, exc=str(exc))
        fallback = (
            state.get("current_message", "") or
            f"[Cliente enviou {media_type} mas houve falha técnica no processamento.]"
        )
        return {**state, "current_message": fallback}


async def _do_ingest(state: AgentState) -> AgentState:
    media_type = state.get("media_type")
    log.info("ingest_media.start",
             media_type=media_type,
             has_url=bool(state.get("media_url")),
             has_b64=bool(state.get("media_b64")),
             mime=state.get("media_mime"))
    # Lazy imports — keep cold-start lean for plain-text traffic
    from services.media import (
        transcribe_audio, describe_image, fetch_media_bytes,
    )

    media_bytes: bytes | None = None

    # State may carry base64-encoded bytes (set by webhook handler when it
    # had to use a tenant token to fetch — e.g. WhatsApp Cloud).
    b64 = state.get("media_b64")
    if b64:
        try:
            media_bytes = base64.b64decode(b64)
        except Exception as exc:  # noqa: BLE001
            log.warning("ingest_media.b64_decode_failed", exc=str(exc))

    # Otherwise fetch from the URL (Z-API direct link).
    if media_bytes is None and state.get("media_url"):
        media_bytes = await fetch_media_bytes(state["media_url"])

    if not media_bytes:
        log.warning("ingest_media.no_bytes",
                    media_type=media_type, has_url=bool(state.get("media_url")))
        # Degrade gracefully — let the agent handle "received unreadable media"
        return {
            **state,
            "current_message": (
                state.get("current_message", "") or
                f"[Cliente enviou {media_type} que não consegui processar.]"
            ),
        }

    caption = state.get("current_message", "") or ""
    updates: dict = {}

    if media_type in ("audio", "voice"):
        transcript = await transcribe_audio(
            media_bytes,
            mime_type=state.get("media_mime") or "audio/ogg",
            language="pt",
        )
        if transcript:
            updates["media_transcript"] = transcript
            # Combine caption (rare for voice notes) + transcript
            updates["current_message"] = (
                f"{caption}\n[Áudio transcrito]: {transcript}".strip()
                if caption else f"[Áudio do cliente]: {transcript}"
            )
        else:
            updates["current_message"] = (
                caption or "[Cliente enviou áudio que não consegui transcrever.]"
            )
        log.info("ingest_media.audio.done", chars=len(transcript or ""))

    elif media_type == "image":
        description = await describe_image(
            media_bytes,
            mime_type=state.get("media_mime") or "image/jpeg",
            caption=caption,
            purpose="pharmacy",
        )
        if description:
            updates["media_description"] = description
            updates["current_message"] = (
                f"[Cliente enviou imagem]\n{description}"
                + (f"\n[Legenda do cliente]: {caption}" if caption else "")
            )
        else:
            updates["current_message"] = (
                caption or "[Cliente enviou imagem que não consegui analisar.]"
            )
        log.info("ingest_media.image.done", chars=len(description or ""))

    else:
        # video / document — describe minimally; full parsing out of scope
        updates["current_message"] = (
            f"[Cliente enviou {media_type}]"
            + (f" com legenda: {caption}" if caption else "")
        )
        log.info("ingest_media.passthrough", media_type=media_type)

    trace = list(state.get("trace_steps", []))
    trace.append(f"ingest_media:{media_type}")
    updates["trace_steps"] = trace

    return {**state, **updates}
