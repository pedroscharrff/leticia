"""Skill nodes — cada um especializado em um domínio farmacêutico.

SKILL_REGISTRY mapeia skill_name → módulo (usado pelos routers do admin/portal
para retornar o `SYSTEM_PROMPT` padrão de cada skill).
"""
from __future__ import annotations

from types import SimpleNamespace

from agents.nodes.skills import (
    farmaceutico,
    principio_ativo,
    genericos,
    vendedor,
    recuperador,
    saudacao,
    guardrails,
)


def _wrap(module) -> SimpleNamespace:
    """Expõe o _SYSTEM do módulo como SYSTEM_PROMPT (interface esperada)."""
    return SimpleNamespace(SYSTEM_PROMPT=getattr(module, "_SYSTEM", None))


SKILL_REGISTRY: dict[str, SimpleNamespace] = {
    "farmaceutico":    _wrap(farmaceutico),
    "principio_ativo": _wrap(principio_ativo),
    "genericos":       _wrap(genericos),
    "vendedor":        _wrap(vendedor),
    "recuperador":     _wrap(recuperador),
    "saudacao":        _wrap(saudacao),
    "guardrails":      _wrap(guardrails),
}
