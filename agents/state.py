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

    # ── Commerce ──────────────────────────────────────────────────────────────
    cart:            dict[str, Any]      # {items: [], subtotal: float}
    stock_mode:      str                 # catalogo | estoque_real

    # ── Quality control ───────────────────────────────────────────────────────
    analyst_approved: bool
    escalate:        bool

    # ── Output ────────────────────────────────────────────────────────────────
    final_response:  str

    # ── Context / personalisation ─────────────────────────────────────────────
    persona:         dict[str, Any]      # loaded from DB (tone, name, etc.)
    skill_prompts:   dict[str, str]      # custom system prompts per skill

    # ── Observability ─────────────────────────────────────────────────────────
    trace_steps:     list[str]
