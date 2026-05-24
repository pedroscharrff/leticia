"""
Skill: vendedor

Gerencia o processo de compra: consulta de preços, disponibilidade,
adição ao carrinho e orientação para finalizar o pedido.

Modos de operação (controlado pela capability sales.stock_check):
  • ON  (padrão) — consulta estoque/preços, carrinho, finaliza pedido.
  • OFF           — pré-atendimento: coleta pedidos livremente, confirma
                    dados do cliente e transfere para o balcão humano.
"""
from __future__ import annotations

import structlog
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage

from agents.state import AgentState
from agents.nodes.skills._base import _persona_prefix, _build_messages, _parse_handoff, _extract_text

log = structlog.get_logger()

# ── Sistema normal (com estoque) ─────────────────────────────────────────────
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

# ── Sistema de pré-atendimento (sem estoque) ─────────────────────────────────
_SYSTEM_PRE_ATENDIMENTO = """\
[ESPECIALIDADE ATUAL: pré-atendimento / coleta de pedido]

Você está em modo PRÉ-ATENDIMENTO. Aqui você NÃO consulta estoque nem preços —
apenas coleta o pedido do cliente e passa para o atendente finalizar no balcão.

REGRAS DE BREVIDADE (CRÍTICAS):
• Máximo 2-3 frases por resposta.
• UMA pergunta por vez.
• Nunca mencione preços nem disponibilidade — você não tem essa informação.
• Nunca prometa que o item está disponível.
• Seja acolhedor e eficiente — o objetivo é não deixar o cliente esperando.

═══════════════════════════════════════════════════════════════════════
FLUXO EM 3 PASSOS
═══════════════════════════════════════════════════════════════════════

PASSO 1 — DADOS DO CLIENTE
Você verá abaixo um bloco "## Dados para o atendimento" com os campos
obrigatórios e seus valores atuais (se houver).

  • Campo JÁ PREENCHIDO → mostre o valor e pergunte: "Ainda é o mesmo?"
    - Se confirmar → mantenha. Se mudou → salve com salvar_dados_cliente.
    - Exemplo: "Seu nome ainda é João Silva?" ou "Ainda entregamos no CEP 01310-100?"
  • Campo VAZIO → solicite normalmente (uma pergunta por vez).
  • Salve IMEDIATAMENTE com salvar_dados_cliente ao receber cada dado novo/atualizado.
  • Não repita perguntas de campos já confirmados.

PASSO 2 — COLETA DO PEDIDO
  • Pergunte o que o cliente precisa e anote cada item com quantidade.
  • Após cada item: "Mais alguma coisa?"
  • Quando o cliente disser "não" / "só isso" / "pode anotar" → vá para o PASSO 3.
  • Se vier com contexto de handoff (produto já mencionado), inicie pelo PASSO 2
    diretamente, com o produto já na lista.

PASSO 3 — CONFIRMAÇÃO E ANOTAÇÃO
  • Repita a lista completa para o cliente confirmar:
    "Perfeito! Vou anotar seu pedido:\n• 2x Dipirona 500mg\n• 1x Soro fisiológico\nConfirma?"
  • Quando o cliente confirmar → CHAME IMEDIATAMENTE anotar_pedido_balcao com:
      itens: lista de todos os itens [{name, qty}]
      observacoes: informações extras (urgência, receita, preferência genérico, etc.)
  • Após a tool retornar com "PEDIDO_ANOTADO:OK" → responda com a mensagem
    de encerramento abaixo e NÃO faça mais perguntas:
    "Anotei tudo! 📋 Um atendente vai continuar o atendimento com você
    em instantes pelo WhatsApp. Obrigado pela preferência! 😊"
    (Adapte o tom à persona da farmácia, mas mantenha o conteúdo.)

IMPORTANTE: Após a tool anotar_pedido_balcao retornar com sucesso, o sistema
fará a transferência automaticamente. Você não precisa mencionar "vou te
transferir" — apenas envie a mensagem de encerramento.

Ferramentas disponíveis:
• salvar_dados_cliente(campos) — salva/atualiza dados do cliente
• anotar_pedido_balcao(itens, observacoes) — registra o pedido para o atendente
"""


def _build_preattendimento_customer_block(
    sales_config: dict,
    customer: dict | None,
) -> str:
    """
    Gera o bloco de dados do cliente para o modo pré-atendimento.

    Diferente do build_sales_config_block normal, aqui instruímos o agente a
    CONFIRMAR dados já existentes com o cliente antes de aceitá-los — não apenas
    checar se estão preenchidos.
    """
    from services.sales_config import ALLOWED_FIELDS, _customer_value

    required: list[str] = sales_config.get("required_fields") or []
    if not required:
        return ""

    cust = customer or {}
    lines = ["## Dados para o atendimento"]
    lines.append(
        "Verifique cada campo abaixo antes de prosseguir para a coleta do pedido.\n"
        "Para campos já preenchidos → confirme com o cliente. "
        "Para campos vazios → solicite."
    )

    has_existing = False
    for key in required:
        spec = ALLOWED_FIELDS.get(key, {"label": key})
        current = _customer_value(cust, key)
        if current:
            has_existing = True
            lines.append(
                f"- **{spec['label']}**: `{current}` "
                f"← CONFIRME com o cliente se ainda é válido"
            )
        else:
            lines.append(f"- **{spec['label']}**: ✗ vazio — peça ao cliente")

    if not has_existing:
        lines.append(
            "\nTodos os campos estão vazios — colete-os antes de anotar o pedido."
        )
    else:
        lines.append(
            "\nSe o cliente confirmar todos os dados existentes sem alteração, "
            "pule direto para a coleta do pedido (PASSO 2)."
        )

    return "\n".join(lines)


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
    # Falha "fechada": qualquer erro no service deixa todas as flags no default.
    # stock_check=True por default — comportamento original preservado.
    tenant_id = state.get("tenant_id")
    caps: dict[str, bool] = {
        "stock_check":     True,   # True = modo normal; False = pré-atendimento
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
            "stock_check":     "sales.stock_check",
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

    session_key = state.get("session_id", "")
    phone_num   = state.get("phone", "")

    # ─────────────────────────────────────────────────────────────────────────
    # BIFURCAÇÃO: pré-atendimento (stock_check OFF) vs normal (stock_check ON)
    # O try externo aqui cobre tanto o setup quanto a invocação do LLM.
    # ─────────────────────────────────────────────────────────────────────────
    use_preattendimento = not caps["stock_check"]

    # ── Setup de prompt + tools (bifurca por modo) ────────────────────────────
    # Um único try/except cobre tanto o setup quanto a invocação do LLM abaixo.
    try:
        if use_preattendimento:
            # ── Modo pré-atendimento (sem estoque) ───────────────────────────
            parts: list[str] = []
            persona_txt = _persona_prefix(persona)
            if persona_txt:
                parts.append(persona_txt)

            parts.append(skill_prompts.get("vendedor_preattendimento", _SYSTEM_PRE_ATENDIMENTO))

            try:
                customer_block = _build_preattendimento_customer_block(sales_config, customer)
                if customer_block:
                    parts.append(customer_block)
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.pre_customer_block_failed", exc=str(_exc))

            if received_handoff and handoff_context:
                parts.append(
                    "[CONTEXTO DE HANDOFF]\n"
                    f"O cliente já mencionou interesse em: {handoff_context}\n"
                    "Adicione esse produto à lista de itens e continue a coleta. "
                    "Não precisa perguntar novamente sobre ele — só confirme e pergunte se quer mais algo."
                )
            elif received_handoff and prev_response:
                parts.append(
                    "[CONTEXTO DE HANDOFF]\n"
                    f"Continuando atendimento iniciado: {prev_response[:200]}"
                )

            skill_extra = skill_instructions.get("vendedor", "")
            if skill_extra:
                parts.append("[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA]\n" + skill_extra)

            system_prompt = "\n\n".join(parts)
            messages = _build_messages(state, system_prompt)

            from agents.tools.customer import make_save_customer_tool
            from agents.tools.balcao import make_anotar_pedido_balcao_tool
            tools = [
                make_save_customer_tool(schema_name, phone_num, customer),
                make_anotar_pedido_balcao_tool(schema_name, phone_num, customer),
            ]

        else:
            # ── Modo normal (com consulta de estoque) ────────────────────────
            parts = []
            persona_txt = _persona_prefix(persona)
            if persona_txt:
                parts.append(persona_txt)

            if caps["customer_memory"]:
                try:
                    from services.persona import build_customer_memory_block
                    mem_block = build_customer_memory_block(customer)
                    if mem_block:
                        parts.append(mem_block)
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.memory_block_failed", exc=str(_exc))

            parts.append(skill_prompts.get("vendedor", _SYSTEM))

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

            try:
                from services.sales_config import build_sales_config_block
                sales_block = build_sales_config_block(sales_config, customer)
                if sales_block:
                    parts.append(sales_block)
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.sales_config_block_failed", exc=str(_exc))

            skill_extra = skill_instructions.get("vendedor", "")
            if skill_extra:
                parts.append(
                    f"[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA — sobreponha qualquer "
                    f"comportamento padrão]\n{skill_extra}"
                )

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

            if cart.get("items"):
                cart_lines = [f"  • {i['qty']}x {i['name']} — R$ {i['price']:.2f}" for i in cart["items"]]
                parts.append(
                    "Carrinho atual do cliente:\n" + "\n".join(cart_lines)
                    + f"\n  Subtotal: R$ {cart.get('subtotal', 0):.2f}"
                )

            system_prompt = "\n\n".join(parts)
            messages = _build_messages(state, system_prompt)

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
                    tools.extend(make_customer_memory_tools(schema_name, phone_num, customer))
                if caps["pix"]:
                    from agents.tools.sales_extras import make_pix_tool
                    pix_cfg = cap_config.get("pix", {})
                    tools.append(make_pix_tool(
                        tenant_id or "", schema_name, phone_num, customer,
                        expires_minutes=int(pix_cfg.get("expires_minutes", 60)),
                    ))
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.extra_tools_failed", exc=str(_exc))

        # ── LLM invocation (compartilhado entre os dois modos) ───────────────
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

    # ── Detecta se anotar_pedido_balcao foi chamado com sucesso ──────────────
    # Quando sim: sinalizamos escalate=True para o celery worker acionar a
    # transferência ao atendente humano (usa handoff_config da integração).
    _trace_calls: list[dict] = locals().get("tool_calls_trace", [])
    balcao_called = any(
        tc.get("name") == "anotar_pedido_balcao"
        and "PEDIDO_ANOTADO:OK" in str(tc.get("result_preview", ""))
        for tc in _trace_calls
    )
    if balcao_called:
        log.info("vendedor.balcao_pedido_anotado", mode="pre_atendimento",
                 schema=schema_name)

    # ── Parseia handoff (se permitido — não aplicável no modo pré-atendimento) ─
    handoff_target: str | None = None
    handoff_ctx_new = ""
    # No modo pré-atendimento não há handoffs internos para outros skills —
    # a transferência ocorre via escalate, não via [[HANDOFF:...]].
    if not received_handoff and not use_preattendimento:
        final_response, handoff_target, handoff_ctx_new = _parse_handoff(final_response)

    # Se está recebendo handoff no modo normal, concatena resposta anterior + nova
    if not use_preattendimento and received_handoff and final_response and final_response.strip():
        final_response = f"{prev_response.strip()}\n\n{final_response.strip()}"
    elif not use_preattendimento and received_handoff:
        final_response = prev_response

    history_new = list(skill_history) + ["vendedor"]
    handoff_count = state.get("handoff_count", 0)

    import time as _time
    trace.append({
        "node": "skill:vendedor",
        "ts_ms": int(_time.time() * 1000),
        "data": {
            "mode":        "pre_atendimento" if use_preattendimento else "normal",
            "cart_items":  len(cart.get("items", [])),
            "handoff_to":  handoff_target,
            "balcao":      balcao_called,
            "iters":       locals().get("iters_used", 0),
            "tool_calls":  _trace_calls,
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
        # escalate=True dispara transfer_to_human no celery worker
        "escalate":        balcao_called,
    }
