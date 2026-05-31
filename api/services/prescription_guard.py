"""
services/prescription_guard.py

Detecta quando o agente afirma "não precisa de receita" sobre produto que
no catálogo está marcado como `prescription_required=TRUE`.

Função pura. Conservadora — só sinaliza quando há evidência clara.
"""
from __future__ import annotations

import re
import unicodedata


_NO_PRESCRIPTION_PATTERNS = [
    r"\bnao\s+precisa\s+de?\s+receita\b",
    r"\bsem\s+receita\b",
    r"\bnao\s+exige\s+receita\b",
    r"\bvenda\s+livre\b",
    r"\bn[ãa]o\s+precisa\s+receit",
    r"\bdispensa\s+receita\b",
    r"\blivre\s+de\s+receita\b",
]

_PRESCRIPTION_NOTED_PATTERNS = [
    # Frases que indicam que agente JÁ avisou que precisa receita — evita falso+
    r"\bprecisa\s+de?\s+receita\b",
    r"\bexige\s+receita\b",
    r"\bcom\s+receita\b",
    r"\bsomente\s+com\s+receita\b",
    r"\bmediante\s+receita\b",
    r"\breceita\s+m[eé]dica\b",
]


def _normalize(text: str) -> str:
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def detect_prescription_issues(
    response_text: str,
    search_results: list[dict] | None,
) -> list[dict]:
    """Retorna list de {"product": str, "reason": "missing_prescription_warning"}.

    Lógica:
      (a) algum produto matched do turno tem prescription_required=True
      (b) resposta contém afirmação tipo "não precisa receita"
      (c) resposta NÃO já avisou que precisa receita

    Cobre o caso clássico: cliente pede Rivotril, agente diz "tem sim, sem
    receita" — flagga.
    """
    if not response_text or not search_results:
        return []
    norm = _normalize(response_text)

    restricted: list[str] = []
    for r in search_results:
        if not isinstance(r, dict):
            continue
        for p in r.get("matched_products") or []:
            if p.get("prescription_required"):
                name = (p.get("name") or "").strip()
                if name and name not in restricted:
                    restricted.append(name)

    if not restricted:
        return []

    has_no_presc = any(re.search(p, norm) for p in _NO_PRESCRIPTION_PATTERNS)
    if not has_no_presc:
        return []

    has_presc_noted = any(re.search(p, norm) for p in _PRESCRIPTION_NOTED_PATTERNS)
    if has_presc_noted:
        # Mistura confusa — agente disse "sem receita" mas também menciona
        # receita. Em vez de re-escrever (pode quebrar contexto), não flagga.
        return []

    issues: list[dict] = []
    for name in restricted:
        # Só flagga se o produto for mencionado na resposta (case insensitive)
        if _normalize(name) in norm or _normalize(name).split()[0] in norm:
            issues.append({
                "product": name,
                "reason":  "missing_prescription_warning",
            })
    return issues


def build_correction_message(issues: list[dict]) -> str:
    names = sorted({i.get("product", "") for i in issues if i.get("product")})
    if not names:
        return (
            "Atenção: alguns desses medicamentos exigem receita médica. "
            "Posso anotar pra você apresentar no balcão na hora da retirada."
        )
    items = ", ".join(names)
    return (
        f"Importante: {items} exige(m) receita médica. "
        f"Posso anotar o pedido pra você apresentar a receita no balcão "
        f"na hora da retirada. Tudo bem?"
    )
