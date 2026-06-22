"""
agents/prompts/clinical.py

Blocos de prompt GATEADOS POR CAPABILITY do fluxo clínico (farmaceutico).
Antes ficavam como constantes soltas em `farmaceutico.py` (`_STOCK_CHECK_BLOCK`).

Texto preservado VERBATIM para manter a migração como refactor puro.
"""
from __future__ import annotations

_DIV = "═══════════════════════════════════════════════════════════════════════"


def stock_check_block() -> str:
    """Anexado ao prompt do farmaceutico sempre que EXISTE catálogo
    (`sales.stock_check` ON — modo Sheets/CSV OU ERP). Em pré-atendimento (sem
    catálogo) o agente não tem fonte da verdade, então o bloco não entra. A bula
    confirma que o remédio EXISTE; só o catálogo diz se a LOJA o carrega — daí a
    regra. Cf. SPEC 02 §farmaceutico + SPEC 04 §modos."""
    return (
        "\n"
        f"{_DIV}\n"
        "CONFERIR O CATÁLOGO ANTES DE AFIRMAR QUE A LOJA TEM O PRODUTO\n"
        f"{_DIV}\n"
        "Esta farmácia tem um CATÁLOGO de produtos. A bula da ANVISA confirma que\n"
        "um medicamento EXISTE no mundo — NÃO que esta loja o tenha. Você NÃO pode\n"
        "dizer que a farmácia tem um produto sem antes confirmar no catálogo via\n"
        "`buscar_produto` — afirmar pela bula algo que não está no catálogo frustra\n"
        "o cliente e o vendedor desmente depois.\n"
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
