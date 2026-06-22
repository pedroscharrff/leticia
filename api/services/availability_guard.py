"""
services/availability_guard.py

Detecta alucinação de disponibilidade na resposta do agente, comparando contra
os resultados das chamadas de `buscar_produto` deste turno.

Função pura, determinística, sem I/O — fácil de testar.

Conservadora por design: só sinaliza quando há evidência forte (resposta
contém frase de afirmação E menciona um produto que sabemos NÃO estar
disponível). Falso negativo é aceitável; falso positivo destrói confiança.

Formato esperado de `search_results` (lista populada pela tool buscar_produto):
    [
      {"query": "dipirona 500mg", "found": False, "in_stock": False, "matched_name": None},
      {"query": "engov",         "found": True,  "in_stock": True,  "matched_name": "Engov After"},
    ]
"""
from __future__ import annotations

import re
import unicodedata


# Frases que indicam afirmação positiva de disponibilidade — todas em forma
# normalizada (lowercase, sem acentos). \b força fronteira de palavra.
_AFFIRMATION_PATTERNS = [
    r"\btemos?\b",
    r"\btem\s+sim\b",
    r"\btenho\b",
    r"\bsim,?\s+temos?\b",
    r"\besta\s+dispon",            # "está disponível"
    r"\bestao\s+dispon",           # "estão disponíveis"
    r"\bem\s+estoque\b",
    r"\bconsigo\s+(te\s+)?separar\b",
    r"\bvou\s+separar\b",
    r"\bposso\s+enviar\b",
    r"\bpossu",                    # "possuímos"
    r"\bdispon[ií]vel\b",
    r"\bpode\s+aproveitar\b",
]

# Frases que NEGAM disponibilidade — se aparecem, presumimos honestidade
# (evita falso positivo em "não temos dipirona" sendo lido como "temos").
_NEGATION_PATTERNS = [
    r"\bnao\s+temos?\b",
    r"\bnao\s+ten",                # não tenho/tinha
    r"\bsem\s+estoque\b",
    r"\bindispon",                 # indisponível
    r"\besgotad",                  # esgotado
    r"\bnao\s+encontr",            # não encontrei
    r"\bnao\s+esta\s+dispon",
    r"\binfelizmente\s+nao",
]


def _normalize(text: str) -> str:
    """Lowercase + sem acentos. Usado pra match insensível."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", text)
    no_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return no_accents.lower()


def _mentions(haystack_norm: str, needle: str) -> bool:
    """Verdadeiro se a resposta contém a query buscada (ou o token principal)."""
    if not needle:
        return False
    n = _normalize(needle)
    if not n:
        return False
    if n in haystack_norm:
        return True
    # Token principal — primeira palavra "significativa" (≥4 chars)
    for tok in n.split():
        if len(tok) >= 4 and tok in haystack_norm:
            return True
    return False


def has_unverified_affirmation(response_text: str) -> bool:
    """True quando a resposta AFIRMA disponibilidade ("temos", "tem sim", "em
    estoque"...) sem negação clara — INDEPENDENTE de ter havido busca.

    É o sinal usado pelo andaime de force-recall do runtime (LLM fraca em modo
    ERP que afirma "temos" SEM chamar `buscar_produto` neste turno — caso que o
    `detect_hallucinations` NÃO cobre porque `search_results` vem vazio). Pura,
    determinística; reusa os MESMOS patterns do guard (fonte única do regex —
    SPEC 10 §não duplicar). Conservadora no sentido oposto: aqui falso-positivo
    só custa um re-prompt de busca (recuperável), então toleramos.
    """
    if not response_text:
        return False
    norm = _normalize(response_text)
    if not any(re.search(p, norm) for p in _AFFIRMATION_PATTERNS):
        return False
    if any(re.search(p, norm) for p in _NEGATION_PATTERNS):
        return False
    return True


def detect_hallucinations(
    response_text: str,
    search_results: list[dict] | None,
) -> list[dict]:
    """Retorna lista de alucinações detectadas (vazia = resposta limpa).

    Cada item: {"query": str, "matched": str|None, "reason": "not_in_catalog"|"out_of_stock"}.

    Lógica conservadora — só sinaliza quando TODAS as condições verdadeiras:
      (a) houve pelo menos uma busca cujo resultado foi indisponível
      (b) a resposta contém afirmação positiva de disponibilidade
      (c) a resposta menciona a query (ou seu token principal)
      (d) a resposta NÃO contém negação clara perto da menção
    """
    if not response_text or not search_results:
        return []

    norm_resp = _normalize(response_text)

    has_affirmation = any(re.search(p, norm_resp) for p in _AFFIRMATION_PATTERNS)
    if not has_affirmation:
        return []

    has_negation = any(re.search(p, norm_resp) for p in _NEGATION_PATTERNS)

    halls: list[dict] = []
    for r in search_results:
        if not isinstance(r, dict):
            continue
        query = (r.get("query") or "").strip()
        if not query:
            continue

        found = bool(r.get("found"))
        in_stock = bool(r.get("in_stock"))
        if found and in_stock:
            continue  # tudo certo

        if not _mentions(norm_resp, query):
            continue  # query não foi citada na resposta — sem evidência

        # Se a resposta tem negação clara, presumimos que agente já admitiu
        # a indisponibilidade. Não flagga.
        if has_negation:
            continue

        reason = "not_in_catalog" if not found else "out_of_stock"
        halls.append({
            "query":   query,
            "matched": r.get("matched_name") or None,
            "reason":  reason,
        })

    # Deduplica por (query, reason) preservando ordem
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for h in halls:
        key = (h["query"].lower(), h["reason"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(h)
    return unique


def build_correction_message(hallucinations: list[dict]) -> str:
    """Compõe correção honesta a partir das alucinações detectadas."""
    names: list[str] = []
    for h in hallucinations:
        n = (h.get("matched") or h.get("query") or "").strip()
        if n and n not in names:
            names.append(n)
    if not names:
        return (
            "Desculpa a confusão — deixa eu checar com mais cuidado a "
            "disponibilidade desses itens. Um atendente vai te ajudar."
        )
    items = ", ".join(names)
    return (
        f"Desculpa, deixa eu corrigir: na verdade {items} não temos "
        f"disponível no momento. Posso te ajudar com alguma alternativa?"
    )
