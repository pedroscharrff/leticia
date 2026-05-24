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
• remover_do_carrinho(produto) — remove um item do carrinho antes do fechamento
• atualizar_qtd_carrinho(produto, nova_quantidade) — altera quantidade no carrinho
• salvar_dados_cliente(campos) — UPSERT no cadastro do cliente. Chame SEMPRE
  que o cliente informar nome, CPF, email, CEP ou endereço (mesmo no meio da
  conversa). Ex: campos={"nome": "João Silva", "cpf": "12345678900"}
• finalizar_pedido(forma_pagamento, observacoes) — CRIA o pedido no sistema.
  Use APENAS quando o cliente confirmar explicitamente o fechamento.
  forma_pagamento: "pix", "cartao_credito", "cartao_debito", "dinheiro", "boleto"
• cancelar_pedido(numero_pedido) — cancela pedido pending/confirmed (vazio = último)
• editar_pedido(numero_pedido, adicionar, remover, nova_observacao) — edita
  pedido com status pending (adicionar=[{name, qty}], remover=[nome])

REGRA CRÍTICA SOBRE FECHAMENTO:
Quando o cliente confirmar a forma de pagamento ou disser "pode finalizar",
"fecha o pedido", "confirmado", etc., você DEVE chamar a tool finalizar_pedido.
NÃO diga "pedido confirmado" sem ter chamado a tool — o pedido só existe no
sistema depois que a tool for executada. A tool retorna o número do pedido.

REGRA CRÍTICA SOBRE DADOS DO CLIENTE:
Se a Configuração de Vendas exige campos obrigatórios (você verá um bloco
"## Dados obrigatórios para fechar o pedido" acima), você PRECISA coletar e
SALVAR cada campo via salvar_dados_cliente ANTES de chamar finalizar_pedido.
Se chamar finalizar_pedido com campos faltando, a tool vai responder
"Faltam dados..." e você deve pedir só o que está faltando — não mude de
assunto, não invente que o pedido foi feito.
"""


async def vendedor_node(state: AgentState, llm_factory) -> AgentState:
    """Skill vendedor — compras, preços e carrinho com tool-calling."""
    persona            = state.get("persona", {})
    skill_prompts      = state.get("skill_prompts", {})
    skill_instructions = state.get("skill_instructions", {})
    schema_name        = state.get("schema_name", "")
    cart               = state.get("cart", {"items": [], "subtotal": 0.0})
    sales_config       = state.get("sales_config", {}) or {}
    customer           = state.get("customer", {}) or {}
    trace              = list(state.get("trace_steps", []))
    handoff_context    = state.get("handoff_context", "")
    skill_history      = state.get("skill_history", [])
    prev_skill         = skill_history[-1] if skill_history else None
    prev_response      = state.get("final_response", "") if prev_skill and prev_skill != "vendedor" else ""
    received_handoff   = bool(prev_response)

    # Capabilities ativas para este tenant (gating de tools + prompt blocks).
    # Falha "fechada": qualquer erro no service deixa todas as flags em False.
    tenant_id = state.get("tenant_id")
    caps: dict[str, bool] = {
        "customer_memory": False,
        "cross_sell":      False,
        "shipping":        False,
        "interactive":     False,
        "pix":             False,
    }
    cap_config: dict[str, dict] = {}
    try:
        from services import capabilities as cap_svc
        keys = {
            "customer_memory": "attendance.customer_memory",
            "cross_sell":      "sales.cross_sell",
            "shipping":        "delivery.shipping_by_cep",
            "interactive":     "attendance.interactive_buttons",
            "pix":             "payments.pix_asaas",
        }
        for slug, key in keys.items():
            caps[slug] = await cap_svc.is_enabled(tenant_id, key)
            if caps[slug]:
                cap_config[slug] = await cap_svc.get_config(tenant_id, key)
    except Exception as _exc:  # noqa: BLE001
        log.warning("vendedor.capabilities_check_failed", exc=str(_exc))

    # Monta system prompt
    parts = []
    persona_txt = _persona_prefix(persona)
    if persona_txt:
        parts.append(persona_txt)

    # Bloco "O que sabemos do cliente" (gated)
    if caps["customer_memory"]:
        try:
            from services.persona import build_customer_memory_block
            mem_block = build_customer_memory_block(customer)
            if mem_block:
                parts.append(mem_block)
        except Exception as _exc:  # noqa: BLE001
            log.warning("vendedor.memory_block_failed", exc=str(_exc))

    parts.append(skill_prompts.get("vendedor", _SYSTEM))

    # Bloco condicional de cross-sell — instrui o LLM a usar a tool no momento certo.
    if caps["cross_sell"]:
        max_sug = int(cap_config.get("cross_sell", {}).get("max_suggestions_per_turn", 1))
        parts.append(
            "═══════════════════════════════════════════════════════════════\n"
            "CROSS-SELL ATIVO (ofereça complementos)\n"
            "═══════════════════════════════════════════════════════════════\n"
            f"Você TEM a tool `recomendar_complementos(produto)`. Sempre que o cliente\n"
            f"adicionar um item ao carrinho com `adicionar_ao_carrinho`, CHAME\n"
            f"`recomendar_complementos` com o mesmo produto e ofereça NO MÁXIMO\n"
            f"{max_sug} sugestão por turno com framing de valor (ex.: 'quem leva X\n"
            f"costuma levar Y para potencializar'). Nunca empurre — pergunte e siga\n"
            f"o ritmo do cliente."
        )

    # Bloco condicional de frete
    if caps["shipping"]:
        parts.append(
            "═══════════════════════════════════════════════════════════════\n"
            "FRETE POR CEP ATIVO\n"
            "═══════════════════════════════════════════════════════════════\n"
            "Você TEM a tool `calcular_frete(cep, subtotal)`. Sempre que o cliente\n"
            "fornecer o CEP de entrega, ANTES de fechar o pedido, CHAME essa tool\n"
            "passando o CEP e o subtotal atual do carrinho. Comunique valor + prazo\n"
            "+ total final em UMA frase. Se o tool retornar 'frete grátis', destaque\n"
            "isso para o cliente."
        )

    # Bloco condicional de PIX (Asaas)
    if caps["pix"]:
        pix_cfg = cap_config.get("pix", {})
        auto_send = pix_cfg.get("auto_send_after_confirm", True)
        parts.append(
            "═══════════════════════════════════════════════════════════════\n"
            "PIX NO CHAT ATIVO (Asaas)\n"
            "═══════════════════════════════════════════════════════════════\n"
            "Você TEM a tool `gerar_link_pix(numero_pedido, valor_total)`.\n"
            + ("Sempre que `finalizar_pedido` retornar um número de pedido com\n"
               "sucesso E o cliente tiver escolhido pagamento PIX, CHAME essa tool\n"
               "imediatamente passando o número do pedido e o valor total\n"
               "(incluindo frete se aplicável). Repasse ao cliente o copia-cola\n"
               "PIX retornado.\n"
               if auto_send else
               "Quando o cliente PEDIR explicitamente o PIX (ex.: \"manda o PIX\"),\n"
               "CHAME essa tool com o número do pedido e o valor total.\n")
            + "Se a tool retornar uma mensagem pedindo CPF, peça o CPF ao cliente,\n"
              "salve com `salvar_dados_cliente` e tente novamente.\n"
              "Após o cliente pagar, o sistema avisará automaticamente — você não\n"
              "precisa ficar perguntando se pagou."
        )

    # Bloco condicional de memória — instrui o LLM a registrar
    if caps["customer_memory"]:
        parts.append(
            "═══════════════════════════════════════════════════════════════\n"
            "MEMÓRIA DE CLIENTES ATIVA\n"
            "═══════════════════════════════════════════════════════════════\n"
            "Você TEM as tools `registrar_alergia(...)`, `registrar_medicamento_continuo(...)`\n"
            "e `registrar_preferencia(...)`. Use SEMPRE que o cliente declarar\n"
            "uma alergia, mencionar medicamento de uso contínuo, ou expressar\n"
            "uma preferência. NÃO confirme com mensagens longas — só registre e\n"
            "siga o atendimento naturalmente."
        )

    # Configuração de Vendas — campos obrigatórios + política de tentativas
    try:
        from services.sales_config import build_sales_config_block
        sales_block = build_sales_config_block(sales_config, customer)
        if sales_block:
            parts.append(sales_block)
    except Exception as exc:
        log.warning("vendedor.sales_config_block_failed", exc=str(exc))

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
            make_remove_from_cart_tool,
            make_update_cart_qty_tool,
            make_finalize_order_tool,
        )
        from agents.tools.customer import (
            make_save_customer_tool,
            make_cancel_order_tool,
            make_edit_order_tool,
        )
        session_key = state.get("session_id", "")
        phone_num   = state.get("phone", "")
        tools = [
            make_inventory_tool(schema_name),
            make_add_to_cart_tool(schema_name, cart),
            make_remove_from_cart_tool(cart),
            make_update_cart_qty_tool(cart),
            make_finalize_order_tool(
                schema_name, cart, session_key, phone_num,
                sales_config=sales_config, customer=customer,
            ),
            make_save_customer_tool(schema_name, phone_num, customer),
            make_cancel_order_tool(schema_name, phone_num),
            make_edit_order_tool(schema_name, phone_num),
        ]

        # Capability-gated tools — só vinculadas se a flag estiver ON.
        try:
            from agents.tools.sales_extras import (
                make_cross_sell_tool,
                make_shipping_tool,
                make_customer_memory_tools,
            )

            if caps["cross_sell"]:
                xs_cfg = cap_config.get("cross_sell", {})
                tools.append(make_cross_sell_tool(
                    schema_name,
                    min_weight=float(xs_cfg.get("min_relation_weight", 0.5)),
                    max_suggestions=int(xs_cfg.get("max_suggestions_per_turn", 1)),
                    customer_allergies=customer.get("allergies") or [],
                ))

            if caps["shipping"]:
                sh_cfg = cap_config.get("shipping", {})
                tools.append(make_shipping_tool(
                    tenant_id or "",
                    default_eta_days=int(sh_cfg.get("default_eta_days", 3)),
                    free_above=float(sh_cfg.get("free_above", 0)),
                ))

            if caps["customer_memory"]:
                tools.extend(make_customer_memory_tools(
                    schema_name, phone_num, customer,
                ))

            if caps["pix"]:
                from agents.tools.sales_extras import make_pix_tool
                pix_cfg = cap_config.get("pix", {})
                tools.append(make_pix_tool(
                    tenant_id or "",
                    schema_name,
                    phone_num,
                    customer,
                    expires_minutes=int(pix_cfg.get("expires_minutes", 60)),
                ))
        except Exception as _exc:  # noqa: BLE001
            log.warning("vendedor.extra_tools_failed", exc=str(_exc))

        llm = llm_factory("skill")
        llm_with_tools = llm.bind_tools(tools)

        from config import settings
        max_iters = settings.skill_max_tool_iterations
        lc_messages = list(messages)
        last_tool_result = ""
        final_response = ""
        tool_calls_trace: list[dict] = []
        iters_used = 0

        for i in range(max_iters):
            iters_used = i + 1
            response = await llm_with_tools.ainvoke(lc_messages)

            if not response.tool_calls:
                final_response = _extract_text(response.content)
                break

            # Executa as tool calls
            lc_messages.append(response)
            for tc in response.tool_calls:
                tool_map = {t.name: t for t in tools}
                tool = tool_map.get(tc["name"])
                tc_record: dict = {
                    "iter": iters_used,
                    "name": tc.get("name"),
                    "args": tc.get("args"),
                }
                if tool:
                    try:
                        result = await tool.ainvoke(tc["args"])
                        last_tool_result = str(result)
                        tc_record["result_preview"] = last_tool_result[:300]
                        from langchain_core.messages import ToolMessage
                        lc_messages.append(ToolMessage(content=last_tool_result, tool_call_id=tc["id"]))
                    except Exception as tool_exc:  # noqa: BLE001
                        tc_record["error"] = str(tool_exc)
                        log.warning("vendedor.tool_failed",
                                    name=tc.get("name"), exc=str(tool_exc))
                else:
                    tc_record["error"] = "tool_not_found"
                tool_calls_trace.append(tc_record)
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
            "cart_items":  len(cart.get("items", [])),
            "handoff_to":  handoff_target,
            "iters":       locals().get("iters_used", 0),
            "tool_calls":  locals().get("tool_calls_trace", []),
            "chars":       len(final_response or ""),
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
