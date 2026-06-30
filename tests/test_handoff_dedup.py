"""Testes do dedup de concatenação de handoff (`_base._is_near_duplicate`).

Trava o fix do "monte de resposta confusa" (jun/2026): farmaceutico e vendedor
batendo no mesmo catálogo vazio → duas mensagens de "não temos" que a
concatenação de handoff colava. Cf. SPEC 02 §handoff (dedup) e a memória
`project_farmaceutico_recommends_without_search`.
"""
from agents.nodes.skills._base import _is_near_duplicate


def test_dedup_both_not_found():
    """Os DOIS lados dizem 'não encontrei' (catálogo vazio) → colapsa."""
    prev = "Silvio, não encontrei aqui no catálogo os xaropes expectorantes, mas vou verificar internamente."
    new = "Silvio, consultei nosso catálogo e não encontrei xaropes disponíveis no momento."
    assert _is_near_duplicate(prev, new)


def test_dedup_localizei_variant():
    prev = "Não localizei xarope para tosse no catálogo digital. Vou passar pro balcão."
    new = "Dei uma vasculhada e não localizei nenhum xarope expectorante disponível. Vou passar pro setor."
    assert _is_near_duplicate(prev, new)


def test_legit_handoff_clinical_plus_availability_not_collapsed():
    """Handoff legítimo: clínica (prev) + disponibilidade positiva (new) → mantém os dois."""
    prev = "Para dor de cabeça leve, Paracetamol 750mg ou Dipirona 500mg são boas opções."
    new = "Temos sim o Paracetamol 750mg por R$ 8,90. Quer adicionar ao carrinho?"
    assert not _is_near_duplicate(prev, new)


def test_one_side_not_found_not_collapsed():
    """Só UM lado é not-found (farma recomenda, vendedor não tem) → cliente vê os dois."""
    prev = "Para tosse com catarro, um expectorante ajuda a soltar a secreção."
    new = "Infelizmente não temos esse expectorante no catálogo no momento."
    assert not _is_near_duplicate(prev, new)


def test_literal_containment_collapsed():
    """Continuação repete verbatim o miolo da anterior (modelo fraco copiando)."""
    prev = "Vou verificar a disponibilidade do seu pedido com o setor."
    new = "Vou verificar a disponibilidade do seu pedido com o setor. Um momento!"
    assert _is_near_duplicate(prev, new)


def test_empty_inputs():
    assert not _is_near_duplicate("", "qualquer coisa")
    assert not _is_near_duplicate("qualquer coisa", "")
