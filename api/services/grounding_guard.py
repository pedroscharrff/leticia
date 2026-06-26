"""
services/grounding_guard.py

Detecta afirmação de FATO FARMACOLÓGICO não-ancorada na resposta do agente —
o caso em que a LLM fraca (Gemini/local) VOLUNTARIA, de memória, um genérico,
princípio ativo ou composição que NÃO veio de nenhuma tool deste turno.

É o vetor que `availability_guard` (cruza NOME DE PRODUTO vs estoque) e o
force-recall (`StockRecall`, dispara em afirmação de disponibilidade / preço-
fantasma) NÃO pegam: a fala não afirma estoque nem cita preço — afirma COMPOSIÇÃO
("o genérico do Benegripe é Dipirona + Clorfeniramina + Cafeína"). Cf. SPEC 10
§Grounding de fato farmacológico.

Função pura, determinística, sem I/O — fácil de testar. Reusa o `_normalize` do
availability_guard (fonte única — SPEC 10 §não duplicar).

Conservadora por design (igual aos outros guards): só sinaliza quando há DOIS
sinais juntos — (a) um marcador de afirmação de fato farmacológico (trigger) E
(b) um termo do léxico curado presente na resposta mas AUSENTE da evidência do
turno (tool results + falas do cliente). Falso negativo é aceitável; falso
positivo destrói confiança.
"""
from __future__ import annotations

import re

# Reusa a normalização única (lowercase + sem acentos). Não duplicar regex/normalização.
from services.availability_guard import _normalize


# Marcadores de que a resposta está AFIRMANDO um fato farmacológico (genérico,
# princípio ativo, composição). Forma normalizada (lowercase, sem acento).
_CLAIM_TRIGGER_PATTERNS = [
    r"\bgenerico",                 # genérico / genéricos / generico
    r"\bprincipio\s+ativo",        # princípio ativo
    r"\bprincipios\s+ativos",      # princípios ativos
    r"\bcomposi[cç]",              # composição / composto
    r"\bcomposto\s+por\b",
    r"\b[aà]\s+base\s+de\b",       # "à base de X"
    r"\bmesma\s+f[oó]rmula\b",
    r"\bmesmo\s+princ[ií]pio\b",
    r"\bmesma\s+composi",
    r"\bsimilar\s+(ao|do|da|de)\b",
    r"\bequivale(nte)?\s+(ao|do|a|à)\b",
]

# Tokens curtos/genéricos que aparecem em nomes de princípio ativo mas que
# sozinhos não devem casar (evita ruído). Pulamos termos do léxico com < 5 chars.
_MIN_LEXICON_TOKEN = 5


def _has_claim_trigger(norm_response: str) -> bool:
    return any(re.search(p, norm_response) for p in _CLAIM_TRIGGER_PATTERNS)


def detect_ungrounded_claims(
    response: str,
    evidence: str,
    lexicon: set[str] | None,
) -> list[dict]:
    """Retorna lista de afirmações de fato farmacológico não-ancoradas.

    Cada item: {"term": str, "kind": "ungrounded_pharma_claim"}.

    Dispara quando TODAS verdadeiras:
      (a) a resposta contém um marcador de afirmação de fato farmacológico
          (`_CLAIM_TRIGGER_PATTERNS`);
      (b) a resposta menciona um termo do `lexicon` (princípio ativo / marca de
          referência conhecidos), com ≥ `_MIN_LEXICON_TOKEN` chars;
      (c) esse termo NÃO aparece na `evidence` do turno (texto das tool results +
          falas do cliente). Ausente da evidência ⇒ veio da memória do modelo.

    `lexicon` vazio/None ⇒ `[]` (fail-open: sem base curada não há o que cruzar).
    """
    if not response or not lexicon:
        return []

    norm_resp = _normalize(response)
    if not _has_claim_trigger(norm_resp):
        return []

    norm_evidence = _normalize(evidence or "")

    issues: list[dict] = []
    seen: set[str] = set()
    for term in lexicon:
        nterm = _normalize(term)
        if len(nterm) < _MIN_LEXICON_TOKEN:
            continue
        if nterm in seen:
            continue
        # Termo do léxico citado na resposta…
        if nterm not in norm_resp:
            continue
        # …mas SEM lastro na evidência do turno → afirmação de memória.
        if nterm in norm_evidence:
            continue
        seen.add(nterm)
        issues.append({"term": term, "kind": "ungrounded_pharma_claim"})

    return issues


def build_grounding_correction(issues: list[dict]) -> str:
    """Fala segura quando NÃO há tool de fonte para reancorar (ex.: vendedor, que
    não binda a base de referência). Não despeja o fato inventado — oferece
    confirmar/consultar. Igual à filosofia do availability_guard."""
    return (
        "Deixa eu confirmar essa informação certinho antes de te passar — não quero "
        "te dar nada impreciso sobre o medicamento. Quer que eu veja o equivalente "
        "disponível pra você?"
    )
