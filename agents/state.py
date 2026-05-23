"""
Shared LangGraph state definition.

All nodes read and write from this TypedDict.
"""
from __future__ import annotations
from typing import Any
from typing_extensions import TypedDict


class AgentState(TypedDict, total=False):
    # ── Identifiers ───────────────────────────────────────────────────────────
    tenant_id:       str
    session_id:      str
    phone:           str
    schema_name:     str
    callback_url:    str

    # ── Conversation ──────────────────────────────────────────────────────────
    current_message: str
    messages:        list[dict]          # [{role, content}]
    customer_profile: str                # indefinido | recorrente | vip | inadimplente

    # ── Routing ───────────────────────────────────────────────────────────────
    intent:          str
    selected_skill:  str
    confidence:      float
    available_skills: list[str]
    retry_count:     int

    # ── Multi-agent handoff ───────────────────────────────────────────────────
    handoff_to:      str | None          # próximo skill solicitado pelo skill atual
    handoff_count:   int                  # nº de handoffs nesta execução (limite p/ evitar loop)
    handoff_context: str                  # contexto passado entre skills (ex: nome do remédio)
    skill_history:   list[str]            # ordem dos skills executados nesta execução

    # ── Commerce ──────────────────────────────────────────────────────────────
    cart:            dict[str, Any]      # {items: [], subtotal: float}
    stock_mode:      str                 # catalogo | estoque_real
    sales_config:    dict[str, Any]      # required_fields, max_attempts, fallback_message
    customer:        dict[str, Any]      # row da tabela customers (id, name, doc, cep, etc.)

    # ── Quality control ───────────────────────────────────────────────────────
    analyst_approved: bool
    escalate:        bool

    # ── Output ────────────────────────────────────────────────────────────────
    final_response:  str

    # ── Context / personalisation ─────────────────────────────────────────────
    persona:           dict[str, Any]    # loaded from DB (tone, name, etc.)
    skill_prompts:     dict[str, str]    # SUBSTITUI prompt base (tenant override completo)
    skill_instructions: dict[str, str]   # APPENDA ao prompt (instruções extras do dono)

    # ── Multimodal ingestion (WhatsApp image/audio) ───────────────────────────
    media_type:        str | None         # 'image' | 'audio' | 'video' | 'document'
    media_mime:        str | None
    media_url:         str | None         # direct URL (Z-API) — fetchable without auth
    media_id:          str | None         # provider id (WA Cloud) — needs token to resolve
    media_b64:         str | None         # base64 bytes injected by webhook when token-fetched
    media_transcript:  str                # filled by ingest_media for audio
    media_description: str                # filled by ingest_media for image

    # ── Observability ─────────────────────────────────────────────────────────
    trace_steps:     list[str]
