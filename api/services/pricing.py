"""
Pricing por modelo LLM — USD por milhão de tokens (input, output).

Usado pelo job diário (workers/jobs/aggregate_usage.py) pra calcular
`cost_usd` em `public.usage_tracking`, e por dashboards via recording rules
do Prometheus (futuro).

Preços vigentes em 2026-06. Revisar trimestralmente — Anthropic/OpenAI/Google
costumam ajustar tabela. Lookup é case-insensitive e tolera versão sufixada
(ex.: "claude-haiku-4-5-20251001" casa o prefixo "claude-haiku-4-5").
"""
from __future__ import annotations

from typing import NamedTuple


class ModelPrice(NamedTuple):
    """Preço em USD por **milhão** de tokens."""
    in_per_mtok: float
    out_per_mtok: float


# Match exato OU por prefixo (longest-match). Versão datada do modelo
# (ex.: -20251001) cai no prefixo sem versão.
MODEL_PRICES: dict[str, ModelPrice] = {
    # ── Anthropic Claude 4.x ──────────────────────────────────────────────
    "claude-opus-4-8":        ModelPrice(15.00, 75.00),
    "claude-opus-4-7":        ModelPrice(15.00, 75.00),
    "claude-opus-4-6":        ModelPrice(15.00, 75.00),
    "claude-sonnet-4-6":      ModelPrice(3.00, 15.00),
    "claude-sonnet-4-5":      ModelPrice(3.00, 15.00),
    "claude-haiku-4-5":       ModelPrice(1.00, 5.00),
    "claude-haiku-4-0":       ModelPrice(0.80, 4.00),

    # ── OpenAI GPT-5.x ────────────────────────────────────────────────────
    "gpt-5.5":                ModelPrice(10.00, 30.00),
    "gpt-5.4":                ModelPrice(8.00, 24.00),
    "gpt-5.4-mini":           ModelPrice(2.00, 8.00),
    "gpt-5":                  ModelPrice(5.00, 15.00),
    "gpt-5-mini":             ModelPrice(1.50, 6.00),
    "gpt-5-nano":             ModelPrice(0.30, 1.20),

    # ── OpenAI GPT-4.x ────────────────────────────────────────────────────
    "gpt-4.1":                ModelPrice(2.00, 8.00),
    "gpt-4.1-mini":           ModelPrice(0.40, 1.60),
    "gpt-4.1-nano":           ModelPrice(0.10, 0.40),
    "gpt-4o":                 ModelPrice(2.50, 10.00),
    "gpt-4o-mini":            ModelPrice(0.15, 0.60),

    # ── OpenAI reasoning (o-series) ──────────────────────────────────────
    "o3":                     ModelPrice(15.00, 60.00),
    "o3-mini":                ModelPrice(1.10, 4.40),
    "o4-mini":                ModelPrice(1.10, 4.40),

    # ── Google Gemini ─────────────────────────────────────────────────────
    # ATENÇÃO: get_price faz longest-prefix match. "gemini-2.0-flash-lite" casa o
    # prefixo "gemini-2.0-flash" → a entrada explícita (chave mais longa) tem que
    # existir, senão flash-lite seria cobrado como flash. Idem no
    # prometheus_rules.yml (lá o match é por label EXATO — manter os dois em sync).
    "gemini-2.0-flash-lite":  ModelPrice(0.075, 0.30),   # mais barato (BYOK econômico)
    "gemini-2.0-flash":       ModelPrice(0.10, 0.40),
    "gemini-2.0-pro":         ModelPrice(1.25, 5.00),
    "gemini-2.5-flash-lite":  ModelPrice(0.10, 0.40),
    "gemini-2.5-flash":       ModelPrice(0.30, 2.50),     # mais esperto, mais caro
    "gemini-2.5-pro":         ModelPrice(1.25, 10.00),

    # ── DeepSeek (API OpenAI-compatible) ─────────────────────────────────
    "deepseek-chat":          ModelPrice(0.27, 1.10),    # V3
    "deepseek-reasoner":      ModelPrice(0.55, 2.19),    # R1

    # ── Ollama / self-hosted (custo de inferência = 0 do ponto de vista API) ──
    "llama3.2":               ModelPrice(0.00, 0.00),
}


_FALLBACK_PRICE = ModelPrice(0.00, 0.00)


def get_price(model: str | None) -> ModelPrice:
    """Resolve preço por (a) match exato, (b) longest prefix, (c) fallback 0.

    Fallback 0 é intencional: melhor subestimar custo que quebrar agregação
    quando aparecer modelo desconhecido. Log do collector deve avisar pra
    atualizar a tabela.
    """
    if not model:
        return _FALLBACK_PRICE
    key = model.strip().lower()
    if key in MODEL_PRICES:
        return MODEL_PRICES[key]
    # Longest-prefix match — claude-haiku-4-5-20251001 → claude-haiku-4-5
    matches = [(k, v) for k, v in MODEL_PRICES.items() if key.startswith(k)]
    if matches:
        matches.sort(key=lambda kv: len(kv[0]), reverse=True)
        return matches[0][1]
    return _FALLBACK_PRICE


def estimate_cost_usd(model: str | None, tokens_in: int, tokens_out: int) -> float:
    """Retorna custo estimado em USD. Trunca para 6 casas (sub-centavo de USD)."""
    price = get_price(model)
    cost = (tokens_in / 1_000_000) * price.in_per_mtok \
         + (tokens_out / 1_000_000) * price.out_per_mtok
    return round(cost, 6)
