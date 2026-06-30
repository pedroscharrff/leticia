"""Testes do detector de afirmação/oferta/recomendação de disponibilidade.

Fixa os casos REAIS que vazaram em prod (jun/2026) — a LLM fraca afirmava ou
recomendava produto sem ter chamado `buscar_produto`, e só ia ao catálogo quando
o cliente pedia o preço. Cf. SPEC 10 §força-busca de estoque (sinal A) e a
memória `project_farmaceutico_recommends_without_search`.

São funções puras (regex), sem I/O — o objetivo é travar a cobertura para que o
próximo ajuste de padrão não regrida silenciosamente um caso já coberto.
"""
from services.availability_guard import (
    has_unverified_affirmation,
    has_presentation_offer,
    recommends_unverified_product,
    affirms_or_offers_availability,
    expresses_unavailability,
    detect_hallucinations,
)


# ── Afirmação direta ("temos", "tem sim"…) ────────────────────────────────────
def test_affirmation_direct():
    assert has_unverified_affirmation("Sim, temos sim esse remédio")
    assert has_unverified_affirmation("temos opções como xarope expectorante")
    assert has_unverified_affirmation("está disponível para entrega")


def test_affirmation_negation_passes():
    """Negação clara = agente foi honesto → não sinaliza."""
    assert not has_unverified_affirmation("não temos esse no momento")
    assert not has_unverified_affirmation("infelizmente não encontrei no estoque")


def test_affirmation_clinical_neutral_passes():
    """Resposta clínica sem vocabulário de disponibilidade não dispara."""
    assert not has_unverified_affirmation("a dose de dipirona é 500mg")
    assert not has_unverified_affirmation("você está com tosse seca ou com catarro?")


# ── Oferta de apresentação (forma + convite de compra) ────────────────────────
def test_presentation_offer_needs_two_signals():
    # forma + convite → dispara
    assert has_presentation_offer("a dipirona vem em comprimido ou gotas, qual prefere?")
    # só forma (fala clínica) → NÃO dispara
    assert not has_presentation_offer("a dose recomendada é 500mg")
    # só convite, sem forma → NÃO dispara
    assert not has_presentation_offer("qual você prefere?")


# ── Recomendação de produto (cue + forma) — o vetor novo ──────────────────────
def test_recommendation_real_leaks_fire():
    """As frases EXATAS que vazaram no trace de prod."""
    assert recommends_unverified_product(
        "O xarope expectorante mais comum aqui é o Fluimucil ou Bisolvon"
    )
    assert recommends_unverified_product(
        "O Expec é um xarope expectorante à base de guaifenesina, ótimo para tosse"
    )


def test_recommendation_clinical_referral_does_not_fire():
    """'recomendo' sem forma farmacêutica = fala clínica → NÃO dispara."""
    assert not recommends_unverified_product("Para isso recomendo procurar um médico")
    assert not recommends_unverified_product("Recomendo bastante água e repouso")


def test_recommendation_negation_passes():
    assert not recommends_unverified_product(
        "não temos esse xarope, mas recomendo procurar a versão em comprimido em outra loja"
    )


# ── Sinal combinado (o que o force-recall usa) ────────────────────────────────
def test_combined_signal():
    leaks = [
        "temos opções como xarope expectorante",
        "O xarope expectorante mais comum aqui é o Fluimucil",
        "O Expec é um xarope à base de guaifenesina, ótimo para tosse",
        "a dipirona vem em comprimido ou gotas, qual prefere?",
    ]
    for t in leaks:
        assert affirms_or_offers_availability(t), f"deveria sinalizar: {t!r}"

    safe = [
        "recomendo procurar um médico",
        "você está com tosse seca ou com catarro?",
        "não temos esse produto no momento",
        "ótima escolha! vou conferir aqui",
    ]
    for t in safe:
        assert not affirms_or_offers_availability(t), f"NÃO deveria sinalizar: {t!r}"


# ── detect_hallucinations (guard pós-LLM, sanity) ─────────────────────────────
def test_detect_hallucinations_flags_not_in_catalog():
    resp = "Sim, temos o Fluimucil disponível!"
    results = [{"query": "Fluimucil", "found": False, "in_stock": False, "matched_name": None}]
    halls = detect_hallucinations(resp, results)
    assert any(h["query"] == "Fluimucil" and h["reason"] == "not_in_catalog" for h in halls)


def test_detect_hallucinations_clean_when_found():
    resp = "Sim, temos o Fluimucil!"
    results = [{"query": "Fluimucil", "found": True, "in_stock": True, "matched_name": "Fluimucil 600mg"}]
    assert detect_hallucinations(resp, results) == []


def test_detect_hallucinations_empty_results_is_blindspot():
    """Sem busca, search_results vazio → guard NÃO pega (é o que o force-recall cobre)."""
    assert detect_hallucinations("Sim, temos!", []) == []
    assert detect_hallucinations("Sim, temos!", None) == []


# ── expresses_unavailability (usado pelo dedup de handoff) ────────────────────
def test_expresses_unavailability_variants():
    for t in [
        "não temos esse no momento",
        "não encontrei no catálogo",
        "não localizei xarope para tosse",
        "infelizmente não temos",
        "está esgotado",
        "produto indisponível",
    ]:
        assert expresses_unavailability(t), f"deveria marcar not-found: {t!r}"


def test_expresses_unavailability_negative():
    for t in [
        "temos sim o paracetamol",
        "o expectorante ajuda a soltar a secreção",
        "boa noite, como posso ajudar?",
    ]:
        assert not expresses_unavailability(t), f"NÃO é not-found: {t!r}"
