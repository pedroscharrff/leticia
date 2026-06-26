"""Testes do detector de fato farmacológico não-ancorado (SPEC 10 §Grounding)."""
from services.grounding_guard import (
    detect_ungrounded_claims,
    build_grounding_correction,
)

# Léxico mínimo (normalizado já vem do _normalize internamente).
LEXICON = {"dipirona", "clorfeniramina", "cafeina", "paracetamol", "benegrip"}


def test_flags_volunteered_generic_composition():
    """O caso do print: genérico/composição citados sem vir de tool."""
    resp = (
        "O genérico do Benegripe é vendido pelos princípios ativos: "
        "Dipirona Monoidratada + Maleato de Clorfeniramina + Cafeína."
    )
    evidence = "Benegrip — Caixa c/ 12 comprimidos"  # foi o que a busca trouxe
    issues = detect_ungrounded_claims(resp, evidence, LEXICON)
    terms = {i["term"] for i in issues}
    assert "dipirona" in terms
    assert "clorfeniramina" in terms
    assert "cafeina" in terms


def test_grounded_claim_passes():
    """Mesma fala, mas a evidência do turno CONTÉM os princípios (vieram de tool)."""
    resp = "O princípio ativo é Paracetamol, conforme a bula."
    evidence = "Base de referência: Paracetamol | referência: Tylenol"
    assert detect_ungrounded_claims(resp, evidence, LEXICON) == []


def test_no_trigger_no_flag():
    """Sem marcador de afirmação farmacológica, não dispara mesmo citando o léxico."""
    resp = "Você prefere o Benegrip em comprimido?"
    assert detect_ungrounded_claims(resp, "", LEXICON) == []


def test_empty_lexicon_is_failopen():
    resp = "O genérico é Dipirona."
    assert detect_ungrounded_claims(resp, "", set()) == []
    assert detect_ungrounded_claims(resp, "", None) == []


def test_correction_does_not_leak_fact():
    msg = build_grounding_correction([{"term": "dipirona", "kind": "ungrounded_pharma_claim"}])
    assert "dipirona" not in msg.lower()
    assert "confirmar" in msg.lower()
