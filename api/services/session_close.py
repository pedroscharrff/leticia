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
import re
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

    Casamento (com fronteiras de palavra para evitar falso-positivo tipo
    "fim" dentro de "perfil"):
      1) Match exato da mensagem inteira contra a keyword (após normalizar
         e remover pontuação leve nas bordas).
      2) Match de palavra inteira no meio da mensagem — usa regex \\b ao
         redor da keyword normalizada.

    Normalização: NFKD (sem acentos) + lowercase + strip.
    """
    if not keywords:
        return None
    msg_norm = _normalize(message)
    if not msg_norm:
        return None
    msg_stripped = msg_norm.strip(".!?,;:\"'()[]")
    for kw in keywords:
        kw_norm = _normalize(kw)
        if not kw_norm:
            continue
        # 1) Match exato da mensagem inteira
        if msg_stripped == kw_norm:
            return kw
        # 2) Match como palavra inteira no meio da frase
        # Escapa regex chars; \b funciona bem com keywords compostas tipo
        # "encerrar atendimento" (cada token vira palavra delimitada).
        pattern = r"\b" + re.escape(kw_norm) + r"\b"
        if re.search(pattern, msg_norm):
            return kw
    return None
