"""Unit tests for _detect_item_listing() — heurística do draft-fallback do
vendedor em pré-atendimento (ver spec-preattend-cart-cep-weak-llm-fix.md).

Ampliada para capturar item único narrado em prosa (sem bullet), que antes
exigia >=2 linhas tipo bullet e deixava o carrinho vazio pra LLM fraca.
"""
from agents.nodes.skills.vendedor import _detect_item_listing


def test_bullet_listing_still_detected():
    text = "Então temos:\n• 2x Dipirona\n• 1x Soro Fisiológico"
    assert _detect_item_listing(text)


def test_single_item_prose_with_quantity_detected():
    text = "Beleza, vou anotar 2x Dipirona pra você."
    assert _detect_item_listing(text)


def test_single_item_prose_with_ack_phrase_and_number_detected():
    text = "Show, anotei a Dipirona 500mg pra você."
    assert _detect_item_listing(text)


def test_ack_phrase_without_any_item_signal_not_detected():
    # "Certo, bom dia!" não deve disparar o fallback (small talk puro).
    text = "Certo, bom dia! Como posso ajudar?"
    assert not _detect_item_listing(text)


def test_plain_greeting_not_detected():
    text = "Oi! Tudo bem? Em que posso ajudar hoje?"
    assert not _detect_item_listing(text)


def test_listing_phrase_still_detected():
    text = "Vou confirmar seu pedido antes de finalizar."
    assert _detect_item_listing(text)
