"""
services/session_close.py

Helpers para encerramento de sessão via palavra-chave configurada no canal.

session_config (JSONB, em tenant_integrations e tenant_channels):
    {
      "close_keywords": ["encerrar", "tchau", "fim"],
      "close_message":  "Atendimento encerrado. Quando precisar, é só chamar!",
      "reset_after_handoff": true   # reservado p/ futuro
    }
"""
from __future__ import annotations

import json
import unicodedata
from typing import Any


DEFAULT_CLOSE_MESSAGE = (
    "Atendimento encerrado. Quando precisar de algo, é só me chamar!"
)


def _normalize(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.lower().strip()


def coerce_session_config(raw: Any) -> dict:
    """Garante dict — colunas JSONB podem vir como dict (asyncpg) ou string."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw) or {}
        except Exception:
            return {}
    return {}


def matches_close_keyword(message: str, keywords: list[str] | None) -> str | None:
    """Retorna a keyword que casou, ou None.

    Casamento: a mensagem normalizada (lowercase, sem acentos) precisa ser
    EXATAMENTE igual a uma das keywords normalizadas — evita falso-positivo
    de palavras embutidas em frases longas (ex: "fim" dentro de "perfil").
    Aceita também a keyword precedida apenas de pontuação leve.
    """
    if not keywords:
        return None
    msg_norm = _normalize(message)
    if not msg_norm:
        return None
    # Tira pontuação simples nas bordas
    msg_stripped = msg_norm.strip(".!?,;:\"'()[]")
    for kw in keywords:
        kw_norm = _normalize(kw)
        if not kw_norm:
            continue
        if msg_stripped == kw_norm:
            return kw
    return None
