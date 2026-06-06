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
from agents.nodes.skills._base import (
    _persona_prefix, _build_messages, _parse_handoff, _parse_escalate,
    _parse_end, _extract_text,
)

log = structlog.get_logger()

# ── Sistema normal (com estoque) ─────────────────────────────────────────────
_SYSTEM = """\
[ESPECIALIDADE ATUAL: vendas / estoque]

Você é o agente de VENDAS de uma farmácia em uma conversa de WhatsApp.
Sua única função é conduzir o cliente do interesse até o pedido criado,
consultando estoque/preço, montando o carrinho e fechando.

═══════════════════════════════════════════════════════════════════════
🛑 REGRAS ABSOLUTAS — NUNCA QUEBRAR
═══════════════════════════════════════════════════════════════════════
1. Não invente produto, preço, estoque ou prazo. SÓ informe o que veio de
   uma tool. Antes de citar QUALQUER produto/marca/genérico ao cliente,
   chame `buscar_produto` no mesmo turno e confirme que veio resultado.
   Seu conhecimento prévio sobre marcas/referências/genéricos NÃO conta —
   só o catálogo via tool. Se a busca de um nome voltou vazio, tente
   variantes óbvias (genérico ↔ referência) antes de declarar "não temos".
2. NUNCA diga "pedido confirmado", "pedido criado", "número do seu pedido"
   sem antes chamar `finalizar_pedido` e receber um número de pedido.
3. NUNCA diga "adicionei ao carrinho" sem chamar `adicionar_ao_carrinho`.
4. Quando o cliente informar nome/CPF/CEP/endereço, chame `salvar_dados_cliente`
   IMEDIATAMENTE — sem texto antes — antes de seguir.
5. Sem dados obrigatórios faltando: não chame `finalizar_pedido`. A tool
   vai falhar e contar tentativa.
6. NÃO recomende remédio para sintoma — passe ao FARMACEUTICO.
7. NÃO repita perguntas de campos já confirmados.
8. NÃO mande mais de UMA pergunta por mensagem.
9. NÃO use jargões médicos ou comerciais agressivos. Tom: profissional, claro.
10. NUNCA cite QUANTIDADE em estoque ao cliente. Não diga "temos 5 unidades",
    "restam 3", "está acabando", "última unidade", "muitas em estoque", etc.
    Para o cliente, a resposta é sempre "temos sim" ou "esse não temos".
    Se a tool `buscar_produto` retornar um bloco [INTERNO: N un], esse número
    é PRIVADO seu — use para decisões (ex.: oferecer genérico quando N < qty
    pedida), mas NUNCA repita ao cliente.
11. NUNCA invente variações de embalagem, dosagem ou apresentação. Use
    EXATAMENTE o que `buscar_produto` retornou. Se a tool devolveu apenas
    "Benegrip — Caixa c/ 12 comprimidos", você só tem essa opção — não
    pergunte "é a caixa com 20 ou 36?" nem ofereça "blister", "frasco",
    "ampola" se não vieram na resposta da tool. Quando o cliente pedir um
    tamanho/forma que não existe, ofereça o que existe no catálogo SEM
    inventar variantes.
12. Se uma busca específica voltou vazia (ex.: "Benegrip 36 comprimidos" não
    achou), refaça com o nome base (ex.: "Benegrip") antes de declarar que
    "não temos". É comum o cliente pedir uma variante que não cadastramos —
    nesses casos, ofereça a que existe.

═══════════════════════════════════════════════════════════════════════
SAÍDA — formato e tamanho
═══════════════════════════════════════════════════════════════════════
• Máximo 3 frases curtas por resposta.
• Sempre termine com UMA pergunta clara OU com um botão de ação claro.
• Sem emojis exagerados (no máximo 1 por mensagem, e só quando agregar).
• Sem listas longas — se precisar listar produtos, máximo 3.

═══════════════════════════════════════════════════════════════════════
PLAYBOOK — etapas da conversa
═══════════════════════════════════════════════════════════════════════
1. Consulta de produto:
   - Cliente nomeia produto → chame `buscar_produto(nome)`.
   - Em estoque → informe nome, apresentação e preço, FIM. Pergunte
     "quer adicionar ao carrinho?".
   - Fora de estoque → faça [[HANDOFF:genericos:nome_do_produto]] para
     o agente buscar alternativa. Não invente alternativa.

2. Adicionar ao carrinho:
   - Cliente confirma → chame `adicionar_ao_carrinho(produto, qty)`.
   - Após adicionar, pergunte "Mais algum item?" — UMA pergunta só.

3. Coleta de dados obrigatórios:
   - Se houver bloco "## Dados obrigatórios para fechar o pedido" acima,
     verifique campo a campo:
     • Já temos → não pergunte de novo.
     • Falta → peça ao cliente. Ao receber, `salvar_dados_cliente` ANTES
       de qualquer outra resposta.

4. Fechamento:
   - Cliente diz "fecha", "pode finalizar", "confirmado" → escolha forma
     de pagamento (se ainda não escolheu) e chame `finalizar_pedido`.
   - Tool retorna número → confirme com o número EXATO retornado pela tool.

═══════════════════════════════════════════════════════════════════════
HANDOFFS — passar para outro agente
═══════════════════════════════════════════════════════════════════════
Termine sua mensagem com `[[HANDOFF:agente:contexto]]` quando:
• Sintoma sem nome de produto → `[[HANDOFF:farmaceutico:descrição]]`
• Cliente pediu genérico/mais barato → `[[HANDOFF:genericos:produto]]`
• Cliente perguntou princípio ativo → `[[HANDOFF:principio_ativo:produto]]`

NÃO faça handoff quando:
• Já está respondendo um handoff recebido (você verá o bloco
  "[CONTINUAÇÃO INTERNA]" no system prompt).
• Cliente apenas confirmou ou tirou dúvida simples.

═══════════════════════════════════════════════════════════════════════
HANDOFF PARA ATENDENTE HUMANO (BALCÃO)
═══════════════════════════════════════════════════════════════════════
Termine sua resposta com `[[ESCALATE]]` quando:
• Cliente pedir EXPLICITAMENTE ("quero falar com atendente", "humano", "balcão").
• Você não conseguir resolver após 2 tentativas (ex.: tool falhou repetidas
  vezes, cliente reclamou de problema fora do escopo).
• Cliente relatou uma EMERGÊNCIA médica.

Quando usar `[[ESCALATE]]`:
• Coloque ANTES uma frase curta de transição: "Entendo, vou te passar para
  um de nossos atendentes." e em seguida `[[ESCALATE]]`.
• O sistema vai fazer a transferência automaticamente.

⚠️ REGRA ABSOLUTA — O MARCADOR É OBRIGATÓRIO:
Se você decidir transferir, é PROIBIDO escrever "vou te transferir", "vou te
passar para um atendente", "um momento que chamo alguém" ou QUALQUER frase de
transferência SEM terminar a mensagem com `[[ESCALATE]]`. A frase sozinha NÃO
transfere nada — só o marcador `[[ESCALATE]]` aciona a transferência de verdade.
Se você prometer transferência e não colocar o marcador, o cliente fica
abandonado falando sozinho. Então: ou você transfere DE VERDADE (frase +
`[[ESCALATE]]` juntos na mesma resposta), ou continua o atendimento normalmente
sem mencionar transferência. NUNCA prometa transferir sem o marcador.

🛑 TRAVA ANTI-TRANSFERÊNCIA INDEVIDA (CRÍTICA):
NÃO use `[[ESCALATE]]` nem `[[HANDOFF:...]]` em nenhuma outra situação além
das listadas acima. Especificamente, NUNCA transfira quando:
• o cliente está apenas comprando, confirmando ou respondendo "sim/não/ok";
• você conseguiu responder normalmente (achou produto, fechou pedido);
• o cliente só agradeceu ou se despediu.
Na dúvida, NÃO transfira — continue o atendimento você mesmo. Transferir sem
motivo real atrapalha o cliente e sobrecarrega o balcão. Só transfira se um
dos gatilhos explícitos acima realmente ocorreu na ÚLTIMA mensagem do cliente.

═══════════════════════════════════════════════════════════════════════
FERRAMENTAS (tools)
═══════════════════════════════════════════════════════════════════════
• buscar_produto(nome)
• adicionar_ao_carrinho(produto, quantidade)
• remover_do_carrinho(produto)
• atualizar_qtd_carrinho(produto, nova_quantidade)
• salvar_dados_cliente(campos)
    Ex: campos={"nome": "João Silva", "cpf": "12345678900"}
• finalizar_pedido(forma_pagamento, observacoes)
    forma_pagamento: "pix" | "cartao_credito" | "cartao_debito" | "dinheiro" | "boleto"
• consultar_pedido(codigo)
    Use quando o cliente perguntar o status/andamento de um pedido. `codigo` é o
    número do pedido (ex: '7e2a5b91'). Vazio = pedido mais recente do cliente.
• cancelar_pedido(numero_pedido)
• editar_pedido(numero_pedido, adicionar, remover, nova_observacao)

LEMBRE-SE: você NÃO tem conhecimento próprio sobre estoque, preço ou
disponibilidade. Toda informação que você dá ao cliente DEVE ter vindo de
uma tool no turno atual ou em turnos anteriores desta conversa.
"""

# ── Sistema de pré-atendimento (sem estoque) ─────────────────────────────────
_SYSTEM_PRE_ATENDIMENTO = """\
[ESPECIALIDADE ATUAL: pré-atendimento / coleta de pedido]

Você está em modo PRÉ-ATENDIMENTO em uma conversa de WhatsApp. Seu papel
é COLETAR o pedido do cliente — itens, quantidades e dados de cadastro —
e ENTREGAR para o atendente humano finalizar no balcão. Você NÃO finaliza
nada sozinho.

═══════════════════════════════════════════════════════════════════════
🛑 REGRAS ABSOLUTAS — NUNCA QUEBRAR
═══════════════════════════════════════════════════════════════════════
1. NÃO existe estoque para você consultar. NUNCA invente preço, marca,
   disponibilidade, prazo de entrega ou validade. Se o cliente perguntar,
   responda: "Vou anotar e o atendente confirma o valor com você no
   balcão."
2. NÃO diga "anotei", "anotado", "registrado", "um atendente vai te
   chamar" SEM ANTES ter chamado `anotar_pedido_balcao` no mesmo turno.
   Sem essa chamada o pedido NÃO existe.
3. NÃO faça mais de UMA pergunta por mensagem.
4. Quando o cliente CITAR medicamento por nome (com ou sem dosagem/forma)
   OU descrever sintoma, passe ao FARMACEUTICO via
   `[[HANDOFF:farmaceutico:nome ou descrição]]` ANTES de anotar — o
   farmacêutico confirma na bula da ANVISA se a apresentação existe e
   evita anotar dosagens/marcas inexistentes. Itens claramente não-medicamento
   (fralda, xampu, bala, soro, álcool) podem ir direto à coleta sem handoff.
5. Quando o cliente declarar nome/CPF/CEP/endereço, chame
   `salvar_dados_cliente` IMEDIATAMENTE — sem texto antes.

═══════════════════════════════════════════════════════════════════════
SAÍDA — formato e tamanho
═══════════════════════════════════════════════════════════════════════
• Máximo 2 frases curtas por resposta.
• UMA pergunta clara por vez.
• Tom acolhedor, eficiente, sem pressão.
• Sem emojis exagerados (máximo 1 por mensagem, e só quando agregar).
• Não despeje todos os campos de uma vez — pergunte um por vez.

═══════════════════════════════════════════════════════════════════════
PLAYBOOK — 3 ETAPAS
═══════════════════════════════════════════════════════════════════════

ETAPA 1 — DADOS DO CLIENTE
Veja o bloco "## Dados para o atendimento" abaixo. Ele lista, para CADA campo
obrigatório desta farmácia, se você já tem o valor e o que fazer com ele
(confirmar com o cliente, confiar e seguir, ou pedir). Siga EXATAMENTE a
instrução do bloco — não confirme campos que o bloco mandou só usar, e não
assuma campos que o bloco mandou pedir. Sempre que o cliente informar ou
corrigir um dado, chame `salvar_dados_cliente` IMEDIATAMENTE — sem texto antes.
Sem campos obrigatórios pendentes → vá para a Etapa 2.

ETAPA 2 — COLETA DO PEDIDO
  • Pergunte o que o cliente precisa.
  • ⚠️ MEDICAMENTO citado por nome (com ou sem dosagem): NÃO anote ainda.
    PRIMEIRO faça `[[HANDOFF:farmaceutico:<nome>]]` (regra 4) — o farmacêutico
    confere a apresentação na bula. Só itens claramente NÃO-medicamento
    (fralda, soro, xampu, álcool, bala) é que você anota direto, "como o
    cliente disse". Em dúvida se é medicamento, faça o handoff — é seguro.
  • Para itens não-medicamento: anote o nome e a quantidade EXATAMENTE como o
    cliente disse.
  • Após cada item (já validado/anotado): "Mais alguma coisa?"
  • Cliente disser "não", "só isso", "pode anotar" → vá para a Etapa 3.

ETAPA 3 — CONFIRMAÇÃO E REGISTRO (TOOL OBRIGATÓRIA)
Sequência exata:
  a) Repita a lista completa em UMA mensagem e peça confirmação:
     "Vou anotar seu pedido:
        • 2x Dipirona 500mg
        • 1x Soro fisiológico
      Pode confirmar?"
  b) Cliente confirma → seu PRÓXIMO output deve ser SOMENTE a chamada da
     tool `anotar_pedido_balcao(itens=[{name,qty},...], observacoes=...)`.
     SEM TEXTO antes da tool — só a chamada.
  c) Tool retorna "PEDIDO_ANOTADO:OK" → responda APENAS então:
     "Pronto! Um atendente vai continuar com você em instantes. Obrigado!"

═══════════════════════════════════════════════════════════════════════
ESCALATION HUMANA IMEDIATA — quando NÃO coletar pedido
═══════════════════════════════════════════════════════════════════════
Termine sua resposta com `[[ESCALATE]]` (sem chamar `anotar_pedido_balcao`)
quando:
• Cliente pedir EXPLICITAMENTE atendente humano antes de listar itens.
• Cliente relatar EMERGÊNCIA médica.
• Cliente fizer reclamação grave de pedido anterior, problema com entrega,
  cobrança ou similar — algo fora do escopo de coletar pedido.

Exemplo:
  "Entendo, vou te passar para um de nossos atendentes agora.[[ESCALATE]]"

═══════════════════════════════════════════════════════════════════════
FIM DE ATENDIMENTO — encerrar a conversa ([[END]])
═══════════════════════════════════════════════════════════════════════
Termine sua resposta com `[[END]]` (marcador invisível, removido antes de ir
ao cliente) quando o cliente sinalizar que TERMINOU e NÃO há pedido pendente
para anotar nem nada a transferir:
• "era só isso", "só queria tirar essa dúvida", "obrigado, mais nada".
• Despedida sem pedido: "tchau", "valeu", "até mais".

Coloque ANTES uma despedida curta e cordial e então o marcador. Exemplo:
  "Imagina, qualquer coisa é só chamar. Tenha um ótimo dia![[END]]"

🛑 NÃO use `[[END]]` quando:
• Há itens que o cliente pediu mas ainda NÃO foram anotados via
  `anotar_pedido_balcao` — nesse caso conclua a Etapa 3 (anotar) primeiro.
• O cliente pediu atendente humano (use `[[ESCALATE]]`).
• O cliente ainda está escolhendo / pode querer mais algo — pergunte antes.

[[END]] é só para fechar a conversa quando não restou nenhuma ação pendente.

═══════════════════════════════════════════════════════════════════════
FERRAMENTAS (tools)
═══════════════════════════════════════════════════════════════════════
• salvar_dados_cliente(campos)
    Ex: campos={"nome":"João Silva","cpf":"12345678900","cep":"01310-100"}
• consultar_pedido(codigo)
    Use quando o cliente perguntar o status/andamento de um pedido já feito.
    `codigo` é o número do pedido (ex: '7e2a5b91'). Vazio = pedido mais recente.
• anotar_pedido_balcao(itens, observacoes)
    itens: [{"name":"Dipirona 500mg","qty":2}, {"name":"Soro","qty":1}]
    observacoes: texto livre (ex: "tem receita", "urgente", "prefere genérico")
    SEM essa chamada o pedido NÃO existe. NÃO finja que chamou.

LEMBRE-SE: você é o "anotador" — confirma dados, anota itens, registra
via tool. Tudo mais (preço, valor final, prazo) é com o atendente humano.
"""

# Palavras que indicam que o LLM está tentando encerrar o atendimento sem ter
# chamado a tool. Usadas no fallback abaixo para forçar a chamada.
_CLOSING_HINTS = (
    "anotei", "anotado", "anotei tudo", "registrei", "registrado",
    "atendente vai", "atendente irá", "um momento", "obrigado pela preferência",
    "vai te chamar", "vai continuar com você",
)


def _build_preattendimento_customer_block(
    sales_config: dict,
    customer: dict | None,
    skip_known_field_confirmation: bool = False,
) -> str:
    """
    Gera o bloco de dados do cliente para o modo pré-atendimento.

    Carrega a política de confirmação per-turno (volátil): o prompt cacheado
    fala só "siga as instruções deste bloco" — é AQUI que o agente descobre,
    pra cada campo, se deve confirmar com o cliente, confiar e seguir, ou pedir.

    Quando `skip_known_field_confirmation=True` (capability
    `sales.skip_known_field_confirmation` ATIVA) o agente confia nos campos já
    preenchidos e só pede os vazios — sem reconfirmar nada.
    """
    from services.sales_config import ALLOWED_FIELDS, _customer_value

    required: list[str] = sales_config.get("required_fields") or []
    if not required:
        return ""

    cust = customer or {}
    lines = ["## Dados para o atendimento"]
    if skip_known_field_confirmation:
        lines.append(
            "Para cada campo abaixo, use APENAS as instruções entre colchetes. "
            "Campos com valor já cadastrado: USE o valor sem perguntar nada — "
            "NÃO confirme, NÃO mencione o dado, só siga. Campos vazios: peça ao "
            "cliente (uma pergunta por vez)."
        )
    else:
        lines.append(
            "Verifique cada campo abaixo antes de prosseguir para a coleta do pedido.\n"
            "Para campos já preenchidos → confirme com o cliente. "
            "Para campos vazios → solicite."
        )

    has_existing = False
    has_missing = False
    for key in required:
        spec = ALLOWED_FIELDS.get(key, {"label": key})
        current = _customer_value(cust, key)
        if current:
            has_existing = True
            if skip_known_field_confirmation:
                lines.append(
                    f"- **{spec['label']}**: `{current}` "
                    f"[USE — não confirme, não pergunte]"
                )
            else:
                lines.append(
                    f"- **{spec['label']}**: `{current}` "
                    f"← CONFIRME com o cliente se ainda é válido"
                )
        else:
            has_missing = True
            lines.append(f"- **{spec['label']}**: ✗ vazio — peça ao cliente")

    if skip_known_field_confirmation:
        if not has_missing:
            lines.append(
                "\nTodos os campos obrigatórios já estão preenchidos — "
                "vá DIRETO para a coleta do pedido (Etapa 2), sem mencionar "
                "os dados do cadastro."
            )
        elif has_existing:
            lines.append(
                "\nPeça apenas os campos vazios. Não traga à tona os campos que "
                "já estão preenchidos."
            )
    else:
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
        # Pré-atendimento: rotear nome de medicamento ao farmacêutico p/ validar
        # na bula antes de anotar (evita dosagem/apresentação inventada).
        "pharmacist_validation": False,
        # Pré-atendimento: pular confirmação de campos do cliente já cadastrados
        # (USE direto, sem perguntar "posso confirmar seu nome como ...?").
        "skip_known_field_confirmation": False,
<<<<<<< HEAD
=======
        # Saudação no período correto do dia (bom dia / boa tarde / boa noite /
        # boa madrugada). Injeta bloco volátil com hora + período.
        "time_aware_greeting": False,
>>>>>>> 7f77d76 (atualizando estado de saudação do agente)
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
            "pharmacist_validation": "sales.pharmacist_validation",
            "skip_known_field_confirmation": "sales.skip_known_field_confirmation",
<<<<<<< HEAD
=======
            "time_aware_greeting":   "attendance.time_aware_greeting",
>>>>>>> 7f77d76 (atualizando estado de saudação do agente)
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
    log.info(
        "vendedor.mode",
        mode="pre_atendimento" if use_preattendimento else "normal",
        tenant_id=tenant_id,
        stock_check_enabled=caps["stock_check"],
    )

    # ── Setup de prompt + tools (bifurca por modo) ────────────────────────────
    # Um único try/except cobre tanto o setup quanto a invocação do LLM abaixo.
    try:
        if use_preattendimento:
            # ── Modo pré-atendimento (sem estoque) ───────────────────────────
            # parts = ESTÁVEL (cacheado) · volatile_parts = por-turno (não cacheado)
            parts: list[str] = []
            volatile_parts: list[str] = []
            persona_txt = _persona_prefix(persona)
            if persona_txt:
                parts.append(persona_txt)

            parts.append(skill_prompts.get("vendedor_preattendimento", _SYSTEM_PRE_ATENDIMENTO))

            skill_extra = skill_instructions.get("vendedor", "")
            if skill_extra:
                parts.append("[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA]\n" + skill_extra)

            # ── Volátil: status do cliente + contexto de handoff ─────────────
            try:
                customer_block = _build_preattendimento_customer_block(
                    sales_config,
                    customer,
                    skip_known_field_confirmation=caps["skip_known_field_confirmation"],
                )
                if customer_block:
                    volatile_parts.append(customer_block)
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.pre_customer_block_failed", exc=str(_exc))

            # Contexto temporal (hora + período) — cap attendance.time_aware_greeting
            if caps["time_aware_greeting"]:
                try:
                    from services.time_context import build_time_context_block
                    time_block = build_time_context_block()
                    if time_block:
                        volatile_parts.append(time_block)
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.pre_time_block_failed", exc=str(_exc))

            if received_handoff and handoff_context:
                volatile_parts.append(
                    "[CONTEXTO DE HANDOFF]\n"
                    f"O cliente já mencionou interesse em: {handoff_context}\n"
                    "Adicione esse produto à lista de itens e continue a coleta. "
                    "Não precisa perguntar novamente sobre ele — só confirme e pergunte se quer mais algo."
                )
            elif received_handoff and prev_response:
                volatile_parts.append(
                    "[CONTEXTO DE HANDOFF]\n"
                    f"Continuando atendimento iniciado: {prev_response[:200]}"
                )

            system_prompt = "\n\n".join(parts)
            volatile_prompt = "\n\n".join(volatile_parts)
            messages = _build_messages(state, system_prompt, volatile_prompt=volatile_prompt)

            from agents.tools.customer import (
                make_save_customer_tool,
                make_consultar_pedido_tool,
            )
            from agents.tools.balcao import make_anotar_pedido_balcao_tool
            tools = [
                make_save_customer_tool(schema_name, phone_num, customer),
                make_consultar_pedido_tool(schema_name, phone_num),
                make_anotar_pedido_balcao_tool(schema_name, phone_num, customer, cart),
            ]

        else:
            # ── Modo normal (com consulta de estoque) ────────────────────────
            # parts = ESTÁVEL (cacheado) · volatile_parts = por-turno (não cacheado)
            parts = []
            volatile_parts = []
            persona_txt = _persona_prefix(persona)
            if persona_txt:
                parts.append(persona_txt)

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

            # Modo de fechamento (coleta vs completo) — definido pela farmácia,
            # sobrepõe a intuição do agente sobre perguntar pagamento/entrega.
            # É ESTÁVEL (depende só da config do tenant) → fica no prefixo cacheado.
            try:
                from services.sales_config import build_checkout_flow_block
                checkout_block = build_checkout_flow_block(sales_config)
                if checkout_block:
                    parts.append(checkout_block)
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.checkout_flow_block_failed", exc=str(_exc))

            skill_extra = skill_instructions.get("vendedor", "")
            if skill_extra:
                parts.append(
                    f"[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA — sobreponha qualquer "
                    f"comportamento padrão]\n{skill_extra}"
                )

            # ── VOLÁTIL (após o marcador de cache) ───────────────────────────
            # Dados do cliente (memória) — mudam conforme o cadastro.
            if caps["customer_memory"]:
                try:
                    from services.persona import build_customer_memory_block
                    mem_block = build_customer_memory_block(customer)
                    if mem_block:
                        volatile_parts.append(mem_block)
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.memory_block_failed", exc=str(_exc))

            # Status dos campos obrigatórios ("✓ temos / ✗ falta") — muda a cada
            # dado que o cliente fornece → volátil.
            try:
                from services.sales_config import build_sales_config_block
                sales_block = build_sales_config_block(sales_config, customer)
                if sales_block:
                    volatile_parts.append(sales_block)
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.sales_config_block_failed", exc=str(_exc))

            # Endereço já cadastrado (modo completo + ask_delivery) — depende do
            # cliente → volátil. Permite confirmar em vez de pedir do zero.
            try:
                from services.sales_config import build_known_address_hint
                addr_hint = build_known_address_hint(sales_config, customer)
                if addr_hint:
                    volatile_parts.append(addr_hint)
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.address_hint_failed", exc=str(_exc))

            # Contexto temporal (hora + período) — cap attendance.time_aware_greeting
            if caps["time_aware_greeting"]:
                try:
                    from services.time_context import build_time_context_block
                    time_block = build_time_context_block()
                    if time_block:
                        volatile_parts.append(time_block)
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.time_block_failed", exc=str(_exc))

            if received_handoff:
                volatile_parts.append(
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

            # Carrinho — muda a cada add/remove → o principal motivo do cache miss.
            if cart.get("items"):
                cart_lines = [f"  • {i['qty']}x {i['name']} — R$ {i['price']:.2f}" for i in cart["items"]]
                volatile_parts.append(
                    "Carrinho atual do cliente:\n" + "\n".join(cart_lines)
                    + f"\n  Subtotal: R$ {cart.get('subtotal', 0):.2f}"
                )

            system_prompt = "\n\n".join(parts)
            volatile_prompt = "\n\n".join(volatile_parts)
            messages = _build_messages(state, system_prompt, volatile_prompt=volatile_prompt)

            from agents.tools.inventory import (
                make_inventory_tool,
                make_add_to_cart_tool,
                make_remove_from_cart_tool,
                make_update_cart_qty_tool,
                make_finalize_order_tool,
            )
            from agents.tools.customer import (
                make_save_customer_tool,
                make_consultar_pedido_tool,
                make_cancel_order_tool,
                make_edit_order_tool,
            )
            tools = [
                make_inventory_tool(schema_name, tenant_id, cart=cart),
                make_add_to_cart_tool(schema_name, cart),
                make_remove_from_cart_tool(cart),
                make_update_cart_qty_tool(cart),
                make_finalize_order_tool(
                    schema_name, cart, session_key, phone_num,
                    sales_config=sales_config, customer=customer,
                ),
                make_save_customer_tool(schema_name, phone_num, customer),
                make_consultar_pedido_tool(schema_name, phone_num),
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

        # ── Force-call no pré-atendimento ────────────────────────────────────
        # Se o LLM "fechou" o atendimento ("anotei", "atendente vai te chamar")
        # SEM ter chamado anotar_pedido_balcao, o pedido não foi salvo. Forçamos
        # uma nova iteração informando o erro e exigindo a chamada da tool.
        if use_preattendimento:
            balcao_called_in_loop = any(
                tc.get("name") == "anotar_pedido_balcao"
                and "PEDIDO_ANOTADO:OK" in str(tc.get("result_preview", ""))
                for tc in tool_calls_trace
            )
            lower_resp = (final_response or "").lower()
            looks_like_closing = any(hint in lower_resp for hint in _CLOSING_HINTS)
            if looks_like_closing and not balcao_called_in_loop:
                log.warning(
                    "vendedor.preattendimento.closing_without_tool",
                    final_response_preview=final_response[:200],
                )
                # IMPORTANTE: usa HumanMessage, não SystemMessage. Anthropic
                # rejeita system messages não-consecutivas (depois de Human/AI)
                # com ValueError "Received multiple non-consecutive system
                # messages" — quebrava 3+ turnos/dia em prod.
                lc_messages.append(HumanMessage(content=(
                    "[INSTRUÇÃO INTERNA DO SISTEMA — não é o cliente falando]\n"
                    "⚠️ VOCÊ ESQUECEU DE CHAMAR A TOOL `anotar_pedido_balcao`.\n"
                    "Sua resposta dá a entender que o pedido foi anotado, mas "
                    "a tool NÃO FOI CHAMADA — o pedido NÃO existe no sistema.\n\n"
                    "AGORA: chame `anotar_pedido_balcao` IMEDIATAMENTE passando "
                    "todos os itens que o cliente pediu nesta conversa. Use o "
                    "formato itens=[{\"name\":\"...\",\"qty\":N}, ...]. "
                    "NÃO escreva texto antes — só a tool call."
                )))
                # Re-invoca COM as tools — desta vez deve chamar a tool.
                response2 = await llm_with_tools.ainvoke(lc_messages)
                if response2.tool_calls:
                    lc_messages.append(response2)
                    for tc in response2.tool_calls:
                        tool_map = {t.name: t for t in tools}
                        tool = tool_map.get(tc["name"])
                        tc_record: dict = {
                            "iter": "forced",
                            "name": tc.get("name"),
                            "args": tc.get("args"),
                        }
                        if tool:
                            try:
                                result = await tool.ainvoke(tc["args"])
                                last_tool_result = str(result)
                                tc_record["result_preview"] = last_tool_result[:300]
                                from langchain_core.messages import ToolMessage
                                lc_messages.append(ToolMessage(
                                    content=last_tool_result, tool_call_id=tc["id"]
                                ))
                            except Exception as tool_exc:  # noqa: BLE001
                                tc_record["error"] = str(tool_exc)
                        tool_calls_trace.append(tc_record)
                    # Depois da tool, pede a mensagem final ao cliente
                    response3 = await llm.ainvoke(lc_messages)
                    final_response = _extract_text(response3.content) or final_response
                else:
                    # Se mesmo assim o LLM se recusou a chamar a tool, deixamos
                    # uma mensagem clara para o cliente (não fingimos sucesso).
                    log.error("vendedor.preattendimento.force_call_failed")
                    final_response = (
                        "Tive um problema técnico ao registrar seu pedido agora. "
                        "Vou te transferir para um atendente humano completar."
                    )

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
        # Captura o erro real para o trace step (linha 744 abaixo) — sem isso o
        # turno fica indistinguível de um turno bem-sucedido nos agent_traces.
        import traceback as _tb
        _node_error = {
            "type":  type(exc).__name__,
            "msg":   str(exc),
            "stack": _tb.format_exc()[-1500:],  # corta pra caber em jsonb sem inflar
        }
        log.error("vendedor.failed", exc=str(exc), error_type=type(exc).__name__)
        # Mensagem genérica — o skill pode ter falhado em qualquer ponto
        # (consulta de catálogo, fechamento de pedido, validação, etc.).
        # Diferenciamos rate limit / overload do resto pra orientar o cliente.
        err_text = str(exc).lower()
        if "rate" in err_text or "429" in err_text or "overload" in err_text:
            final_response = (
                "Estou com muita demanda nesse momento. Pode me mandar de novo "
                "em alguns segundos?"
            )
        else:
            final_response = (
                "Desculpe, tive uma instabilidade aqui. Pode repetir sua última "
                "mensagem que eu sigo de onde paramos?"
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

    # ── Detecta pedido explícito de escalation humana ([[ESCALATE]]) ─────────
    # O agente em modo NORMAL pode marcar [[ESCALATE]] quando o cliente
    # pede atendente, há emergência ou ele não consegue resolver.
    final_response, explicit_escalate = _parse_escalate(final_response)
    if explicit_escalate:
        log.info("vendedor.explicit_escalate",
                 mode="pre_atendimento" if use_preattendimento else "normal",
                 schema=schema_name)

    # ── Detecta fim de atendimento sinalizado pelo agente ([[END]]) ──────────
    # Cliente se despediu / disse que era só isso, SEM pedido pendente. SEMPRE
    # limpamos o marcador do texto. Só propagamos o flag quando NÃO houve
    # balcão nem escalation (essas têm prioridade — já fecham via handoff).
    final_response, end_conversation = _parse_end(final_response)
    if balcao_called or explicit_escalate:
        end_conversation = False
    if end_conversation:
        log.info("vendedor.end_conversation",
                 mode="pre_atendimento" if use_preattendimento else "normal",
                 schema=schema_name)

    # ── Parseia handoff ──────────────────────────────────────────────────────
    # Mesmo em pré-atendimento (onde não roteamos pra outro skill), PRECISAMOS
    # rodar o parse pra LIMPAR o marcador [[HANDOFF:...]] do texto antes de
    # enviar ao cliente. O LLM ocasionalmente gera o marcador apesar do prompt
    # — se não removermos, o cliente vê lixo tipo "[[HANDOFF:farmaceutico:...]]"
    # no WhatsApp e o analyst reprova legitimamente.
    handoff_target: str | None = None
    handoff_ctx_new = ""
    if not received_handoff:
        final_response, parsed_target, parsed_ctx = _parse_handoff(final_response)
        # Em modo NORMAL: respeita o roteamento.
        if not use_preattendimento:
            handoff_target  = parsed_target
            handoff_ctx_new = parsed_ctx
        # Em pré-atendimento: por padrão só limpa o texto e descarta o target
        # (sem roteamento entre skills — a transferência ocorre via escalate
        # quando o balcão finaliza). EXCEÇÃO: quando a capability
        # `sales.pharmacist_validation` está ON, roteamos o handoff de validação
        # ao farmacêutico (single-hop), que confere o medicamento na bula da
        # ANVISA antes de o item entrar na coleta — evita anotar dosagem/
        # apresentação inventada. Só roteia se o farmacêutico estiver ativo no
        # tenant; senão degrada para o comportamento padrão (sem validação).
        elif (
            caps.get("pharmacist_validation")
            and parsed_target == "farmaceutico"
            and "farmaceutico" in set(state.get("available_skills", []))
        ):
            handoff_target  = parsed_target
            handoff_ctx_new = parsed_ctx

    # Se está recebendo handoff no modo normal, concatena resposta anterior + nova
    if not use_preattendimento and received_handoff and final_response and final_response.strip():
        final_response = f"{prev_response.strip()}\n\n{final_response.strip()}"
    elif not use_preattendimento and received_handoff:
        final_response = prev_response

    history_new = list(skill_history) + ["vendedor"]
    handoff_count = state.get("handoff_count", 0)

    # Determina o motivo da transferência (se houve) para auditoria.
    if balcao_called:
        escalate_reason = "balcao_preatendimento"
    elif explicit_escalate:
        escalate_reason = "explicit_escalate_llm"
    elif handoff_target:
        escalate_reason = f"handoff:{handoff_target}"
    else:
        escalate_reason = None

    # Última mensagem do cliente — contexto pra entender transferências "do nada".
    _last_user_msg = (state.get("current_message", "") or "")[:200]

    # Loga toda transferência com o gatilho, pra monitorar via docker logs.
    if escalate_reason:
        log.warning(
            "vendedor.transfer",
            reason=escalate_reason,
            trigger_msg=_last_user_msg,
            mode="pre_atendimento" if use_preattendimento else "normal",
            session=state.get("session_id", ""),
            schema=schema_name,
        )

    import time as _time
    _trace_data: dict = {
        "mode":        "pre_atendimento" if use_preattendimento else "normal",
        "cart_items":  len(cart.get("items", [])),
        "handoff_to":  handoff_target,
        "balcao":      balcao_called,
        "escalate":    bool(balcao_called or explicit_escalate),
        "escalate_reason": escalate_reason,
        "trigger_msg": _last_user_msg if escalate_reason else None,
        "iters":       locals().get("iters_used", 0),
        "tool_calls":  _trace_calls,
        "chars":       len(final_response or ""),
    }
    # Se o except global disparou, propaga o erro pro trace step para que
    # falhas do vendedor_node sejam visíveis em agent_traces.steps depois
    # do restart do worker (cf. tool-errors-invisible).
    _node_error_val = locals().get("_node_error")
    if _node_error_val:
        _trace_data["error"] = _node_error_val
    trace.append({
        "node": "skill:vendedor",
        "ts_ms": int(_time.time() * 1000),
        "data": _trace_data,
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
        # escalate=True dispara transfer_to_human no worker. Acionado quando:
        #   1) o agente pediu explicitamente via [[ESCALATE]]
        #   2) modo pré-atendimento concluiu com sucesso (balcão)
        "escalate":        balcao_called or explicit_escalate,
        # end_conversation=True faz o worker encerrar a sessão (end_session)
        # de forma determinística — cliente se despediu sem pedido pendente.
        "end_conversation": end_conversation,
    }
