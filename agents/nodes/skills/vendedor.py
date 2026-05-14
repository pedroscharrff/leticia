"""
Skill: vendedor

Gerencia o processo de compra: consulta de preços, disponibilidade,
adição ao carrinho e orientação para finalizar o pedido.
"""
from __future__ import annotations

import structlog
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from agents.state import AgentState
from agents.nodes.skills._base import _persona_prefix, _build_messages

log = structlog.get_logger()

_SYSTEM = """\
Você é um vendedor especializado da farmácia, focado em ajudar o cliente a comprar.

Suas responsabilidades:
• Consultar disponibilidade e preços de produtos no catálogo
• Adicionar produtos ao carrinho do cliente
• Informar o subtotal atualizado do carrinho
• Orientar sobre formas de pagamento e entrega (se disponíveis)
• Sugerir produtos complementares quando fizer sentido (ex.: vitamina C com zinc)

Tom de atendimento:
• Proativo, mas sem pressão de venda
• Celebre as escolhas do cliente com frases curtas e naturais
• Sempre confirme o item antes de adicionar ao carrinho
• Exiba o carrinho resumido quando solicitado

Ao finalizar:
• Quando o cliente disser que quer fechar o pedido, forneça um resumo completo
• Informe que um atendente confirmará o pedido em breve (fluxo padrão)

Ferramentas disponíveis:
• buscar_produto(nome) — busca produto no catálogo
• adicionar_ao_carrinho(produto, quantidade) — adiciona item ao carrinho
"""


async def vendedor_node(state: AgentState, llm_factory) -> AgentState:
    """Skill vendedor — compras, preços e carrinho com tool-calling."""
    persona       = state.get("persona", {})
    skill_prompts = state.get("skill_prompts", {})
    schema_name   = state.get("schema_name", "")
    cart          = state.get("cart", {"items": [], "subtotal": 0.0})
    trace         = list(state.get("trace_steps", []))

    # Monta system prompt
    parts = []
    persona_txt = _persona_prefix(persona)
    if persona_txt:
        parts.append(persona_txt)
    parts.append(skill_prompts.get("vendedor", _SYSTEM))

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
        from agents.tools.inventory import make_inventory_tool, make_add_to_cart_tool
        tools = [
            make_inventory_tool(schema_name),
            make_add_to_cart_tool(schema_name, cart),
        ]

        llm = llm_factory("skill")
        llm_with_tools = llm.bind_tools(tools)

        from config import settings
        max_iters = settings.skill_max_tool_iterations
        lc_messages = list(messages)

        for _ in range(max_iters):
            response = await llm_with_tools.ainvoke(lc_messages)

            if not response.tool_calls:
                final_response = response.content
                break

            # Executa as tool calls
            lc_messages.append(response)
            for tc in response.tool_calls:
                tool_map = {t.name: t for t in tools}
                tool = tool_map.get(tc["name"])
                if tool:
                    result = await tool.ainvoke(tc["args"])
                    from langchain_core.messages import ToolMessage
                    lc_messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
        else:
            # Excedeu iterações — resposta parcial
            response = await llm.ainvoke(lc_messages)
            final_response = response.content

    except Exception as exc:
        log.error("vendedor.failed", exc=str(exc))
        final_response = (
            "Desculpe, tive uma dificuldade para consultar o catálogo agora. "
            "Pode me dizer o nome do produto que está procurando?"
        )

    trace.append(f"skill:vendedor → carrinho={len(cart.get('items', []))} itens")

    return {
        **state,
        "final_response": final_response,
        "cart":           cart,
        "trace_steps":    trace,
    }
