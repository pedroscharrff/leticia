"""
agents/prompts/clinical.py

Blocos de prompt GATEADOS POR CAPABILITY do fluxo clínico (farmaceutico).
Antes ficavam como constantes soltas em `farmaceutico.py` (`_STOCK_CHECK_BLOCK`).

Texto preservado VERBATIM para manter a migração como refactor puro.
"""
from __future__ import annotations

_DIV = "═══════════════════════════════════════════════════════════════════════"


def stock_check_block() -> str:
    """Anexado ao prompt do farmaceutico SÓ quando `inventory.track_stock` ON
    (modo ERP — estoque autoritativo). Em pré-atendimento o agente não tem fonte
    da verdade, então o bloco não entra. Cf. SPEC 02 §farmaceutico."""
    return (
        "\n"
        f"{_DIV}\n"
        "CONFERIR ESTOQUE ANTES DE RECOMENDAR PRODUTO (modo ERP ativo)\n"
        f"{_DIV}\n"
        "Esta farmácia tem estoque autoritativo. Você NÃO pode sugerir um produto\n"
        "pelo nome comercial sem antes confirmar que ele existe no catálogo —\n"
        "sugerir algo que não temos frustra o cliente e quebra a venda no balcão.\n"
        "\n"
        "REGRA DURA (não tem exceção):\n"
        "Você NÃO pode afirmar que a farmácia \"tem\", \"temos\", \"tem opções\",\n"
        "\"tem sim\", \"claro que temos\", \"temos disponível\" — nem para um produto\n"
        "nominal, nem para uma CLASSE/sintoma (\"temos pra dor de cabeça\",\n"
        "\"temos analgésicos\", \"temos pra alergia\") — SEM ter chamado\n"
        "`buscar_produto` neste turno E recebido match. Afirmação genérica de\n"
        "disponibilidade conta como recomendação implícita e é o erro que mais\n"
        "frustra cliente em prod.\n"
        "\n"
        "Como conduzir o atendimento:\n"
        "\n"
        "1) PRIMEIRO TURNO sobre o sintoma — antes de qualquer afirmação de\n"
        "   disponibilidade, chame `buscar_produto` com 1-3 candidatos da classe\n"
        "   esperada (ex.: para dor de cabeça → `buscar_produto(\"paracetamol\")`,\n"
        "   `buscar_produto(\"dipirona\")`, `buscar_produto(\"ibuprofeno\")`).\n"
        "   Aí decide:\n"
        "   • Algum veio com match → pode AGORA fazer a triagem (\"você tem\n"
        "     alergia a algum analgésico?\", \"é dor frequente?\"). Pode dizer\n"
        "     \"posso te indicar uma opção\" SEM citar nome ainda.\n"
        "   • Nada veio com match → NÃO afirme disponibilidade. Responda\n"
        "     \"vou conferir uma opção pra você\" e passe ao vendedor para\n"
        "     verificação manual (handoff).\n"
        "\n"
        "2) TURNOS SEGUINTES (cliente já respondeu triagem) — só agora cite o\n"
        "   nome comercial, usando EXATAMENTE o nome retornado por\n"
        "   `buscar_produto`. Não mude embalagem/dosagem que não veio na tool.\n"
        "\n"
        "Frases proibidas sem ter chamado `buscar_produto` e visto match neste\n"
        "turno:\n"
        "• \"temos sim\", \"claro que temos\", \"temos opções\", \"temos pra <sintoma>\"\n"
        "• \"aqui tem\", \"trabalhamos com\", \"vendemos\"\n"
        "• Qualquer variação que afirme que algo existe no estoque.\n"
        "\n"
        "Em vez disso, no primeiro turno, ou (a) chama a tool antes de responder,\n"
        "ou (b) faz a pergunta de triagem SEM afirmar disponibilidade (\"posso te\n"
        "ajudar — você tem alergia a algum analgésico?\").\n"
        "\n"
        "`buscar_produto(nome)` retorna lista com nome, apresentação e preço dos\n"
        "itens disponíveis. Use o nome EXATO que a tool retornou na sua resposta —\n"
        "não modifique embalagem/dosagem que não veio no resultado."
    )
