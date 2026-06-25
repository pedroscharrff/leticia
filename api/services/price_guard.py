"""
services/price_guard.py

Detecta quando o agente cita um preço "R$ X" que NÃO bate com nenhum preço
real dos produtos buscados neste turno.

Conservador — exige que a resposta mencione um preço e que ESSE preço não
esteja em nenhum dos produtos consultados na busca (tolerância R$ 0,01).

Limitação conhecida: não associa preço-a-produto específico (não faz NER).
Apenas garante que qualquer preço citado existe em algum produto matched.
"""
from __future__ import annotations

import re


# "R$ 12,90" / "R$12,90" / "R$ 12.90" / "12,90 reais" / "R$ 1.250,00" / "R$ 12"
# Um único pattern unifica todos os formatos. Lookahead negativo `(?![\.,]?\d)`
# garante que NÃO casamos "12" dentro de "12,90" (o que duplicaria matches).
_PRICE_PATTERNS = [
    r"R\$\s*([0-9]{1,3}(?:\.[0-9]{3})*(?:,[0-9]{2})?|[0-9]+(?:\.[0-9]{1,2})?)(?![\.,]?\d)",
    r"([0-9]{1,3}(?:\.[0-9]{3})*,[0-9]{2}|[0-9]+(?:[\.,][0-9]{2}))\s*reais?\b",
]

_TOLERANCE = 0.01


def _parse_brl(s: str) -> float | None:
    """'1.250,00' / '12,90' / '12.90' / '12' → float."""
    if not s:
        return None
    s = s.strip().replace(" ", "")
    # Se tem AMBOS '.' e ',', '.' é separador de milhar, ',' é decimal (BR)
    if "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return round(float(s), 2)
    except ValueError:
        return None


def _extract_prices(text: str) -> list[float]:
    if not text:
        return []
    found: list[float] = []
    for pat in _PRICE_PATTERNS:
        for m in re.finditer(pat, text):
            v = _parse_brl(m.group(1))
            if v is not None and v > 0:
                found.append(v)
    return found


def extract_prices(text: str) -> list[float]:
    """Wrapper público de `_extract_prices` (fonte única do regex de preço —
    SPEC 10 §"não duplicar regex de preço"). Usado pelo force-recall do runtime
    para detectar preço-fantasma (preço citado que não veio de nenhuma busca do
    turno)."""
    return _extract_prices(text)


def _known_prices(search_results: list[dict]) -> set[float]:
    prices: set[float] = set()
    for r in search_results or []:
        if not isinstance(r, dict):
            continue
        for p in r.get("matched_products") or []:
            v = p.get("price")
            if isinstance(v, (int, float)) and v > 0:
                prices.add(round(float(v), 2))
    return prices


def detect_price_issues(
    response_text: str,
    search_results: list[dict] | None,
) -> list[dict]:
    """Retorna [{"price_mentioned": float, "reason": "unknown_price"}].

    Vazio quando: nenhum preço mencionado, OU não há buscas com preço pra
    cruzar, OU todos os preços mencionados batem com algum conhecido.
    """
    if not response_text or not search_results:
        return []
    mentioned = _extract_prices(response_text)
    if not mentioned:
        return []
    known = _known_prices(search_results)
    if not known:
        # Sem preços conhecidos pra cruzar — não dá pra validar.
        return []

    issues: list[dict] = []
    for v in mentioned:
        # Match com tolerância
        if not any(abs(v - k) <= _TOLERANCE for k in known):
            issues.append({"price_mentioned": v, "reason": "unknown_price"})
    # Dedup (mesmo preço mencionado 2x)
    seen: set[float] = set()
    unique: list[dict] = []
    for it in issues:
        if it["price_mentioned"] in seen:
            continue
        seen.add(it["price_mentioned"])
        unique.append(it)
    return unique


def build_correction_message(issues: list[dict]) -> str:
    if not issues:
        return ""
    vals = sorted({i.get("price_mentioned") for i in issues if i.get("price_mentioned")})
    if not vals:
        return (
            "Deixa eu confirmar o valor exato com o atendente antes de seguir. "
            "Um momento!"
        )
    fmt = ", ".join(f"R$ {v:.2f}".replace(".", ",") for v in vals)
    return (
        f"Vou conferir o valor com o atendente — o preço de {fmt} que mencionei "
        f"pode estar desatualizado. Já te confirmo certinho."
    )
