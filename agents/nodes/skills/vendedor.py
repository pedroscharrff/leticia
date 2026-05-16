"""
Skill: vendedor

Gerencia o processo de compra: consulta de preços, disponibilidade,
adição ao carrinho e orientação para finalizar o pedido.
"""
from __future__ import annotations

import structlog
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from agents.state import AgentState
from agents.nodes.skills._base import _persona_prefix, _build_messages, _parse_handoff, _extract_text

log = structlog.get_logger()

_SYSTEM = """\
[ESPECIALIDADE ATUAL: vendas / estoque]

Você está usando sua especialidade de vendas agora. Conduza o atendimento como
conversa real — não despeje tudo de uma vez. Siga o PLAYBOOK e a etapa atual.

REGRAS DE BREVIDADE (CRÍTICAS):
• Máximo 3-4 frases por resposta.
• UMA pergunta por vez.
• Quando consultar estoque: informe disponibilidade e preço do ITEM PEDIDO, sem
  oferecer alternativas a menos que esteja em falta.
• Vantagens comerciais (PIX, fidelidade, entrega grátis) APENAS no FECHAMENTO,
  não na primeira consulta de preço.
• Se cliente descreve sintoma sem nomear remédio, não recomende — use sua
  especialidade farmacêutica para triagem brevemente.

═══════════════════════════════════════════════════════════════════════
SUAS RESPONSABILIDADES
═══════════════════════════════════════════════════════════════════════
• Consultar disponibilidade e preços (use buscar_produto)
• Adicionar produtos ao carrinho (use adicionar_ao_carrinho)
• Informar subtotal do carrinho
• Orientar pagamento e entrega
• Sugerir complementos quando fizer sentido (ex.: vitamina C + zinco)

═══════════════════════════════════════════════════════════════════════
HANDOFFS — quando passar a bola
═══════════════════════════════════════════════════════════════════════
Você pode delegar terminando sua resposta com:

  [[HANDOFF:agente:contexto]]

Quando passar para FARMACEUTICO:
• Cliente descreveu SINTOMA sem nomear produto ("o que tomar pra enxaqueca?")
  Exemplo: "[[HANDOFF:farmaceutico:cliente quer remédio para enxaqueca]]"

Quando passar para GENERICOS:
• Cliente perguntou por alternativa mais barata após você mostrar um produto.
  Exemplo: "[[HANDOFF:genericos:Tylenol 750mg]]"

Quando passar para PRINCIPIO_ATIVO:
• Cliente perguntou o princípio ativo de um produto.

═══════════════════════════════════════════════════════════════════════
QUANDO NÃO FAZER HANDOFF
═══════════════════════════════════════════════════════════════════════
• Cliente perguntou por produto específico → você consulta e responde, FIM.
• Você está recebendo handoff do farmaceutico → consulta estoque do produto
  informado no contexto e responde, FIM. Não devolva para o farmacêutico.

═══════════════════════════════════════════════════════════════════════
TOM E DIRETRIZES
═══════════════════════════════════════════════════════════════════════
• Proativo, mas sem pressão de venda
• Sempre confirme antes de adicionar ao carrinho
• Se o produto não estiver em estoque, ofereça alternativa via handoff p/ genericos

Ferramentas disponíveis:
• buscar_produto(nome) — busca produto no catálogo (use também para principio ativo)
• adicionar_ao_carrinho(produto, quantidade) — adiciona item ao carrinho
• finalizar_pedido(forma_pagamento, observacoes) — CRIA o pedido no sistema.
  Use APENAS quando o cliente confirmar explicitamente o fechamento.
  forma_pagamento: "pix", "cartao_credito", "cartao_debito", "dinheiro", "boleto"

REGRA CRÍTICA SOBRE FECHAMENTO:
Quando o cliente confirmar a forma de pagamento ou disser "pode finalizar",
"fecha o pedido", "confirmado", etc., você DEVE chamar a tool finalizar_pedido.
NÃO diga "pedido confirmado" sem ter chamado a tool — o pedido só existe no
sistema depois que a tool for executada. A tool retorna o número do pedido.
"""


async def vendedor_node(state: AgentState, llm_factory) -> AgentState:
    """Skill vendedor — compras, preços e carrinho com tool-calling."""
    persona            = state.get("persona", {})
    skill_prompts      = state.get("skill_prompts", {})
    skill_instructions = state.get("skill_instructions", {})
    schema_name        = state.get("schema_name", "")
    cart               = state.get("cart", {"items": [], "subtotal": 0.0})
    trace              = list(state.get("trace_steps", []))
    handoff_context    = state.get("handoff_context", "")
    skill_history      = state.get("skill_history", [])
    prev_skill         = skill_history[-1] if skill_history else None
    prev_response      = state.get("final_response", "") if prev_skill and prev_skill != "vendedor" else ""
    received_handoff   = bool(prev_response)

    # Monta system prompt
    parts = []
    persona_txt = _persona_prefix(persona)
    if persona_txt:
        parts.append(persona_txt)
    parts.append(skill_prompts.get("vendedor", _SYSTEM))

    # extra_instructions do dono (camada de personalização)
    skill_extra = skill_instructions.get("vendedor", "")
    if skill_extra:
        parts.append(
            f"[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA — sobreponha qualquer "
            f"comportamento padrão]\n{skill_extra}"
        )

    # Se veio de outro agente, injeta resposta anterior + contexto e proíbe novo handoff
    if received_handoff:
        parts.append(
            "[CONTINUAÇÃO INTERNA — não é visível ao cliente]\n"
            f"Você acabou de dizer (como parte da mesma conversa contínua):\n"
            f"\"\"\"\n{prev_response}\n\"\"\"\n"
            "Agora você deve COMPLEMENTAR essa resposta consultando o estoque com "
            "a tool buscar_produto.\n"
            + (f"Produtos a verificar: {handoff_context}\n" if handoff_context else "")
            + "REGRAS:\n"
            "• NÃO repita o que já foi dito acima — apenas COMPLEMENTE com "
            "  disponibilidade e preço.\n"
            "• Sua resposta será CONCATENADA à anterior, então escreva como "
            "  continuação natural da mesma pessoa.\n"
            "• NÃO faça outro handoff. NÃO mencione 'sou o vendedor' ou similares.\n"
            "• Se o produto não estiver no estoque, ofereça alternativa do catálogo."
        )

    # Contexto do carrinho atual
    if cart.get("items"):
        cart_lines = [f"  • {i['qty']}x {i['name']} — R$ {i['price']:.2f}" for i in cart["items"]]
        parts.append(
            "Carrinho atual do cliente:\n" + "\n".join(cart_lines)
            + f"\n  Subtotal: R$ {cart.get('subtotal', 0):.2f}"
        )

    system_prompt = "\n\n".join(parts)
    messages = _build_messages(state, system_prompt)

    try:
        from agents.tools.inventory import (
            make_inventory_tool,
            make_add_to_cart_tool,
            make_finalize_order_tool,
        )
        session_key = state.get("session_id", "")
        phone_num   = state.get("phone", "")
        tools = [
            make_inventory_tool(schema_name),
            make_add_to_cart_tool(schema_name, cart),
            make_finalize_order_tool(schema_name, cart, session_key, phone_num),
        ]

        llm = llm_factory("skill")
        llm_with_tools = llm.bind_tools(tools)

        from config import settings
        max_iters = settings.skill_max_tool_iterations
        lc_messages = list(messages)
        last_tool_result = ""
        final_response = ""

        for _ in range(max_iters):
            response = await llm_with_tools.ainvoke(lc_messages)

            if not response.tool_calls:
                final_response = _extract_text(response.content)
                break

            # Executa as tool calls
            lc_messages.append(response)
            for tc in response.tool_calls:
                tool_map = {t.name: t for t in tools}
                tool = tool_map.get(tc["name"])
                if tool:
                    result = await tool.ainvoke(tc["args"])
                    last_tool_result = str(result)
                    from langchain_core.messages import ToolMessage
                    lc_messages.append(ToolMessage(content=last_tool_result, tool_call_id=tc["id"]))
        else:
            # Excedeu iterações — resposta parcial sem tools
            response = await llm.ainvoke(lc_messages)
            final_response = _extract_text(response.content)

        # Garante resposta textual ao cliente: se LLM ficou só em tool calls e
        # não gerou texto, fazemos uma chamada final SEM tools forçando a resposta.
        if not final_response or not final_response.strip():
            log.info("vendedor.empty_text_after_tools", tool_result=last_tool_result[:120])
            from langchain_core.messages import HumanMessage
            lc_messages.append(HumanMessage(content=(
                "Responda agora em texto curto (1-3 frases) ao cliente sobre o que "
                "você acabou de fazer, e termine com UMA pergunta para o próximo passo."
            )))
            response = await llm.ainvoke(lc_messages)
            final_response = _extract_text(response.content)

        # Último fallback: se ainda assim ficou vazio, usa o último tool result
        if not final_response or not final_response.strip():
            final_response = last_tool_result or (
                "Pronto! Algo mais que posso ajudar?"
            )

    except Exception as exc:
        log.error("vendedor.failed", exc=str(exc))
        final_response = (
            "Desculpe, tive uma dificuldade para consultar o catálogo agora. "
            "Pode me dizer o nome do produto que está procurando?"
        )

    # Parseia handoff (se permitido)
    handoff_target: str | None = None
    handoff_ctx_new = ""
    if not received_handoff:
        final_response, handoff_target, handoff_ctx_new = _parse_handoff(final_response)

    # Se está recebendo handoff, concatena resposta anterior + nova
    if received_handoff and final_response and final_response.strip():
        final_response = f"{prev_response.strip()}\n\n{final_response.strip()}"
    elif received_handoff:
        final_response = prev_response

    history_new = list(skill_history) + ["vendedor"]
    handoff_count = state.get("handoff_count", 0)

    import time as _time
    trace.append({
        "node": "skill:vendedor",
        "ts_ms": int(_time.time() * 1000),
        "data": {
            "cart_items": len(cart.get("items", [])),
            "handoff_to": handoff_target,
        },
    })

    return {
        **state,
        "final_response":  final_response,
        "cart":            cart,
        "trace_steps":     trace,
        "handoff_to":      handoff_target,
        "handoff_context": handoff_ctx_new,
        "handoff_count":   handoff_count + (1 if handoff_target else 0),
        "skill_history":   history_new,
        "selected_skill":  handoff_target or "vendedor",
    }
