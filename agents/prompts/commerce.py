"""
agents/prompts/commerce.py

Blocos de prompt GATEADOS POR CAPABILITY do fluxo comercial (vendedor, modo
ERP). Antes ficavam inline em `vendedor.py` via `parts.append(\"\"\"...\"\"\")`
espalhados entre as linhas 663-721 — difícil de achar e de manter. Agora cada
bloco é uma função pura testável; o PromptBuilder os adiciona condicionalmente.

São TODOS estáveis (dependem só da config do tenant, não do estado do turno) →
vão no prefixo cacheado. Texto preservado VERBATIM do que existia em vendedor.py
para que a migração seja refactor puro (mesma saída).
"""
from __future__ import annotations

_DIV = "═══════════════════════════════════════════════════════════════"


def cross_sell_block(max_suggestions: int = 1) -> str:
    return (
        f"{_DIV}\n"
        f"CROSS-SELL ATIVO (ofereça complementos)\n"
        f"{_DIV}\n"
        f"Você TEM a tool `recomendar_complementos(produto)`. Sempre que o cliente\n"
        f"adicionar um item ao carrinho com `adicionar_ao_carrinho`, CHAME\n"
        f"`recomendar_complementos` com o mesmo produto e ofereça NO MÁXIMO\n"
        f"{max_suggestions} sugestão por turno com framing de valor (ex.: 'quem leva X\n"
        f"costuma levar Y para potencializar'). Nunca empurre — pergunte e siga\n"
        f"o ritmo do cliente."
    )


def shipping_block() -> str:
    return (
        f"{_DIV}\n"
        "FRETE POR CEP ATIVO\n"
        f"{_DIV}\n"
        "Você TEM a tool `calcular_frete(cep, subtotal)`. Sempre que o cliente\n"
        "fornecer o CEP de entrega, ANTES de fechar o pedido, CHAME essa tool\n"
        "passando o CEP e o subtotal atual do carrinho. Comunique valor + prazo\n"
        "+ total final em UMA frase. Se o tool retornar 'frete grátis', destaque\n"
        "isso para o cliente."
    )


def cep_lookup_block() -> str:
    return (
        f"{_DIV}\n"
        "AUTOCOMPLETAR ENDEREÇO POR CEP\n"
        f"{_DIV}\n"
        "Você TEM a tool `consultar_cep(cep)`. Assim que o cliente informar um\n"
        "CEP, CHAME essa tool ANTES de pedir o resto do endereço. Mostre a\n"
        "rua, bairro e cidade encontrados e PEÇA ao cliente apenas para\n"
        "confirmar e informar o NÚMERO e o COMPLEMENTO (o CEP não traz isso).\n"
        "Depois que o cliente confirmar, salve tudo de uma vez com\n"
        "`salvar_dados_cliente`. NÃO pergunte rua/bairro/cidade que o CEP já\n"
        "trouxe — só confirme. Se a tool disser que o CEP é inválido ou não foi\n"
        "encontrado, aí sim peça o endereço completo manualmente."
    )


def pix_block(auto_send: bool = True) -> str:
    auto = (
        "Sempre que `finalizar_pedido` retornar um número de pedido com\n"
        "sucesso E o cliente tiver escolhido pagamento PIX, CHAME essa tool\n"
        "imediatamente passando o número do pedido e o valor total\n"
        "(incluindo frete se aplicável). Repasse ao cliente o copia-cola\n"
        "PIX retornado.\n"
    )
    manual = (
        "Quando o cliente PEDIR explicitamente o PIX (ex.: \"manda o PIX\"),\n"
        "CHAME essa tool com o número do pedido e o valor total.\n"
    )
    return (
        f"{_DIV}\n"
        "PIX NO CHAT ATIVO (Asaas)\n"
        f"{_DIV}\n"
        "Você TEM a tool `gerar_link_pix(numero_pedido, valor_total)`.\n"
        + (auto if auto_send else manual)
        + "Se a tool retornar uma mensagem pedindo CPF, peça o CPF ao cliente,\n"
          "salve com `salvar_dados_cliente` e tente novamente.\n"
          "Após o cliente pagar, o sistema avisará automaticamente — você não\n"
          "precisa ficar perguntando se pagou."
    )


def customer_memory_block() -> str:
    return (
        f"{_DIV}\n"
        "MEMÓRIA DE CLIENTES ATIVA\n"
        f"{_DIV}\n"
        "Você TEM as tools `registrar_alergia(...)`, `registrar_medicamento_continuo(...)`\n"
        "e `registrar_preferencia(...)`. Use SEMPRE que o cliente declarar\n"
        "uma alergia, mencionar medicamento de uso contínuo, ou expressar\n"
        "uma preferência. NÃO confirme com mensagens longas — só registre e\n"
        "siga o atendimento naturalmente."
    )
