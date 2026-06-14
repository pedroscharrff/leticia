"""
agents/prompts/

Fragmentos de prompt componíveis + `PromptBuilder`.

Objetivo: acabar com os `_SYSTEM` monolíticos. Cada preocupação transversal
(controle de fluxo, blocos de capability comercial/clínica) vira um fragmento
nomeado e testável; o `PromptBuilder` os monta preservando a separação
estável/volátil do prompt caching.

Módulos:
  • builder.py   — PromptBuilder (montagem declarativa)
  • flow.py      — handoff/escalate/end (DERIVADO do contrato das tools de fluxo)
  • commerce.py  — blocos gateados por capability do vendedor (cross-sell, frete, PIX, memória)
  • clinical.py  — blocos do farmaceutico (stock_check em modo ERP)
"""
from __future__ import annotations

from agents.prompts.builder import PromptBuilder

__all__ = ["PromptBuilder"]
