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

from agents.state import AgentState
from agents.nodes.skills._base import (
    _build_messages, _parse_handoff, _parse_escalate,
    _parse_end, _extract_text, _strip_leaked_tool_markup,
)
from agents.prompts import PromptBuilder
from agents.prompts import commerce as _commerce

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
   - Busca não encontrou → ANTES de desistir/transferir: (a) refaça com o nome
     base (REGRA 12); (b) se a ferramenta `sugerir_nome_medicamento` estiver
     disponível, chame-a e OFEREÇA o nome correto ("Você quis dizer X?") — NÃO
     troque sozinho, espere o cliente confirmar e então busque de novo.
   - Fora de estoque (ou nada casou após o acima) → transfira para a
     especialidade `genericos` (tool de transferência interna) buscar
     alternativa. Não invente alternativa.

2. Adicionar ao carrinho:
   - ANTES de adicionar, confirme a QUANTIDADE. Se o cliente ainda NÃO disse
     quantas unidades quer, pergunte "Quantas unidades?" e ESPERE a resposta —
     NÃO assuma 1. Um "sim/quero/pode" confirma o PRODUTO, não a quantidade.
   - Com produto E quantidade definidos → `adicionar_ao_carrinho(produto, qty)`.
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

(As instruções de COMO transferir — para outra especialidade ou para atendente
humano — e de encerrar o atendimento vêm em seções próprias abaixo, via tools de
transferência. Quando transferir: sintoma sem produto → `farmaceutico`; pedido de
genérico/mais barato → `genericos`; dúvida de princípio ativo → `principio_ativo`.
NÃO transfira quando já está respondendo um handoff recebido — você verá o bloco
"[CONTINUAÇÃO INTERNA]" no system prompt — nem quando o cliente só confirmou ou
tirou dúvida simples.)

🛑 TRAVA ANTI-TRANSFERÊNCIA INDEVIDA (CRÍTICA):
NÃO transfira (nem para especialidade, nem para atendente) fora das situações
previstas. Especificamente, NUNCA transfira quando:
• o cliente está apenas comprando, confirmando ou respondendo "sim/não/ok";
• você conseguiu responder normalmente (achou produto, fechou pedido);
• o cliente só agradeceu ou se despediu.
Na dúvida, NÃO transfira — continue o atendimento você mesmo. Transferir sem
motivo real atrapalha o cliente e sobrecarrega o balcão. Só transfira se um
gatilho real ocorreu na ÚLTIMA mensagem do cliente.

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
   OU descrever sintoma, transfira ao FARMACEUTICO (tool de transferência
   interna, destino `farmaceutico`) ANTES de anotar — o farmacêutico confirma
   na bula da ANVISA se a apresentação existe e evita anotar dosagens/marcas
   inexistentes. Itens claramente não-medicamento (fralda, xampu, bala, soro,
   álcool) podem ir direto à coleta sem transferir. (Se a tool de transferência
   ao farmacêutico não estiver disponível, apenas siga a coleta — não invente
   dosagem/marca: anote o que o cliente disse e o balcão confere.)
5. Quando o cliente declarar nome/CPF/CEP/endereço, chame
   `salvar_dados_cliente` IMEDIATAMENTE — sem texto antes.
6. ANTES de qualquer mensagem que LISTE ou REPITA itens do pedido (ex:
   "então temos:", "vou confirmar seu pedido:", "pode confirmar?"), você
   DEVE chamar `registrar_itens_interesse(itens=[...])` no MESMO turno
   com a lista ATUAL completa. Sem isso o rascunho não é salvo — se o
   cliente sumir, ninguém consegue retomar o pedido. A tool é SILENCIOSA:
   não diga "anotei/registrado" por causa dela.

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
    PRIMEIRO transfira ao farmacêutico (regra 4, tool de transferência) — ele
    confere a apresentação na bula. Só itens claramente NÃO-medicamento
    (fralda, soro, xampu, álcool, bala) é que você anota direto, "como o
    cliente disse". Em dúvida se é medicamento, transfira — é seguro.
  • Para itens não-medicamento: anote o nome e a quantidade EXATAMENTE como o
    cliente disse.
  • ⚠️ OBRIGATÓRIO (Regra 6): A CADA item que o cliente acrescentar/mudar
    (já validado), chame `registrar_itens_interesse(itens=[{name,qty},...])` com
    a lista ATUAL completa ANTES de escrever qualquer texto de resposta. Isso
    só SALVA a lista (rascunho) — NÃO finaliza, NÃO transfere e NÃO precisa de
    confirmação. É o que permite retomar o cliente caso ele suma antes de fechar.
    NÃO diga "anotei/registrado" ao cliente por causa dessa tool — ela é
    silenciosa para você. Se esquecer, o sistema vai extrair automaticamente, mas
    é mais lento e menos preciso.
  • Após cada item (já validado/registrado): "Mais alguma coisa?"
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
QUANDO TRANSFERIR PARA ATENDENTE (em vez de coletar pedido)
═══════════════════════════════════════════════════════════════════════
Transfira para atendente humano (tool de transferência ao atendente, SEM chamar
`anotar_pedido_balcao`) quando:
• Cliente pedir EXPLICITAMENTE atendente humano antes de listar itens.
• Cliente relatar EMERGÊNCIA médica.
• Cliente fizer reclamação grave de pedido anterior, problema com entrega,
  cobrança ou similar — algo fora do escopo de coletar pedido.

🛑 Antes de encerrar o atendimento (tool de encerrar): só quando o cliente se
despediu e NÃO há itens pendentes de anotar. Se há itens pedidos mas ainda não
anotados via `anotar_pedido_balcao`, conclua a Etapa 3 primeiro; se o cliente
pediu atendente, transfira (não encerre); se ainda pode querer mais algo,
pergunte antes. (As seções de COMO transferir/encerrar vêm abaixo via tools.)

═══════════════════════════════════════════════════════════════════════
FERRAMENTAS (tools)
═══════════════════════════════════════════════════════════════════════
• salvar_dados_cliente(campos)
    Ex: campos={"nome":"João Silva","cpf":"12345678900","cep":"01310-100"}
• consultar_pedido(codigo)
    Use quando o cliente perguntar o status/andamento de um pedido já feito.
    `codigo` é o número do pedido (ex: '7e2a5b91'). Vazio = pedido mais recente.
• registrar_itens_interesse(itens)
    itens: [{"name":"Dipirona 500mg","qty":2}, {"name":"Soro","qty":1}]
    RASCUNHO durante a coleta (Etapa 2). Salva/atualiza a lista de interesse.
    NÃO finaliza, NÃO transfere, NÃO confirma. Chame a cada mudança da lista
    passando-a INTEIRA (substitui a anterior). Não é a tool de fechar.
• anotar_pedido_balcao(itens, observacoes)
    itens: [{"name":"Dipirona 500mg","qty":2}, {"name":"Soro","qty":1}]
    observacoes: texto livre (ex: "tem receita", "urgente", "prefere genérico")
    TERMINAL (Etapa 3): registra o pedido e transfere ao balcão. Use SOMENTE
    após o cliente confirmar a lista completa. SEM essa chamada o pedido NÃO
    existe. NÃO finja que chamou.

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

# ── Camada 3: métrica de fallback de rascunho ───────────────────────────────
try:
    from prometheus_client import Counter
    _DRAFT_FALLBACK = Counter(
        "preattend_draft_fallback_total",
        "Times the deterministic fallback had to extract cart items because "
        "the LLM skipped registrar_itens_interesse",
        ["tenant_id"],
    )
except Exception:  # noqa: BLE001
    class _StubCounter:
        def labels(self, **_kw):  # type: ignore[override]
            return self
        def inc(self, _amount: int = 1) -> None: ...  # noqa: E704
    _DRAFT_FALLBACK = _StubCounter()  # type: ignore[assignment]

# ── Heurística para detectar listagem de itens em texto do LLM ───────────────
import re as _re
_ITEM_LINE_RE = _re.compile(
    r"(?:^|\n)\s*[•\-\*·]\s*\d+\s*x\s+\S"      # • 2x Dipirona
    r"|(?:^|\n)\s*\d+\s*x\s+\S"                  # 2x Dipirona (sem bullet)
    r"|\d+\s*(?:caixa|frasco|comprimido|unidade)" # 2 caixas
    , _re.IGNORECASE,
)
_LISTING_PHRASES = (
    "então temos", "entao temos", "vou confirmar", "pode confirmar",
    "seu pedido", "lista completa", "itens do pedido",
)

def _detect_item_listing(text: str) -> bool:
    lower = text.lower()
    if any(phrase in lower for phrase in _LISTING_PHRASES):
        return True
    matches = _ITEM_LINE_RE.findall(text)
    return len(matches) >= 2


# ── Detecção de QUANTIDADE declarada pelo cliente (gate determinístico) ──────
# Numeral por extenso (até dezenas — suficiente p/ farmácia). "um/uma" conta como
# quantidade 1 declarada (não pesteia quem disse "quero uma dipirona").
_QTY_WORD_RE = _re.compile(
    r"\b(um|uma|uns|umas|dois|duas|tr[eê]s|quatro|cinco|seis|sete|oito|nove|dez|"
    r"d[uú]zia|d[uú]zias|vinte|trinta|quarenta|cinquenta|cem|cento)\b",
    _re.IGNORECASE,
)
# Unidade de DOSAGEM logo após um número → é dose, NÃO quantidade ("500mg").
_DOSAGE_AFTER_RE = _re.compile(r"^\s*(mg|mcg|ml|g|grama|gramas|mililitros?)\b",
                               _re.IGNORECASE)


def _stated_quantity(text: str) -> bool:
    """Heurística determinística: o cliente declarou uma QUANTIDADE neste texto?
    Dígito que NÃO seja dosagem (exclui '500mg') OU numeral por extenso. Usado
    pelo gate de quantidade do vendedor (modo normal, gated weak)."""
    t = (text or "").lower()
    if not t:
        return False
    for m in _re.finditer(r"\d+", t):
        if _DOSAGE_AFTER_RE.match(t[m.end():m.end() + 8]):
            continue  # número de dosagem, não quantidade
        return True
    return bool(_QTY_WORD_RE.search(t))


# ── Extração estruturada via Haiku (schema forçado, sem agência) ─────────────
_EXTRACT_PROMPT = """\
Extraia do diálogo abaixo a lista ATUAL de itens que o cliente quer comprar.
Retorne APENAS um JSON array. Cada elemento: {"name":"<nome exato>","qty":<int>}.
Se não houver itens claros, retorne [].
NÃO invente itens. NÃO adicione preço. NÃO inclua explicações.
"""

async def _extract_items_from_dialog(
    lc_messages: list,
    llm_factory,
) -> list[dict]:
    import json as _json
    from llm.providers import get_llm, HAIKU
    from langchain_core.messages import HumanMessage as _HM, SystemMessage as _SM

    haiku = get_llm(*HAIKU)

    # Últimas 10 mensagens do diálogo (suficiente para a lista atual)
    dialog_lines: list[str] = []
    for m in lc_messages[-10:]:
        role = getattr(m, "type", "unknown")
        content = getattr(m, "content", "")
        if isinstance(content, str) and content.strip():
            dialog_lines.append(f"[{role}] {content[:500]}")

    resp = await haiku.ainvoke([
        _SM(content=_EXTRACT_PROMPT),
        _HM(content="\n".join(dialog_lines)),
    ])
    raw = _extract_text(resp.content).strip()
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

    items = _json.loads(raw)
    if not isinstance(items, list):
        return []

    clean: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        qty = int(it.get("qty") or 1)
        if name:
            clean.append({"name": name, "qty": max(1, qty), "price": 0.0})
    return clean


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
        # Saudação no período correto do dia (bom dia / boa tarde / boa noite /
        # boa madrugada). Injeta bloco volátil com hora + período.
        "time_aware_greeting": False,
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
            "time_aware_greeting":   "attendance.time_aware_greeting",
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

    # Andaime de tool-calling (Fase C) — gated por modelo/provider fraco
    # (Gemini/weak). Resolvido CEDO para injetar o reforço de venda no prompt e
    # ligar a guarda domínio+fluxo do runtime. No-op para Claude/GPT forte.
    from agents.nodes.skills._base import resolve_skill_tier
    from llm.model_tier import needs_tool_scaffolding
    _vp, _vm, _ = resolve_skill_tier(llm_factory, "skill")
    _v_scaffold = needs_tool_scaffolding(_vp, _vm)

    # Reforço SÓ para modelos fracos: eles pulam a Etapa 2 ("Mais alguma coisa?")
    # e fecham/anotam no primeiro item — disparando o transfer determinístico
    # (worker order_finalized → atendente). Bloco VOLÁTIL e GATED → prompt do
    # Claude/GPT byte-idêntico. Medido em prod com gemini-2.5-pro.
    _SALES_DISCIPLINE = (
        "[DISCIPLINA DE VENDA — crítico]\n"
        "• Colete o pedido COMPLETO antes de fechar. Depois de cada item "
        "definido, pergunte 'Mais alguma coisa?' e ESPERE a resposta.\n"
        "• Um 'Sim' que responde a uma pergunta SUA de dado (dosagem, "
        "apresentação, 'é isso mesmo?') NÃO é permissão para fechar o pedido — "
        "é só a confirmação daquele dado. NÃO chame a tool de anotar por causa "
        "dele.\n"
        "• Antes de anotar/finalizar, faça SEMPRE o fechamento explícito: "
        "repita o PEDIDO COMPLETO em uma mensagem ('Vou anotar seu pedido: "
        "3x Neosaldina, 2x Amoxicilina 500mg. Posso fechar?') e ESPERE o "
        "cliente confirmar ESSE fechamento.\n"
        "• Só chame a tool de anotar/finalizar APÓS essa confirmação explícita "
        "do pedido completo. NUNCA finalize no primeiro item nem no meio da "
        "coleta de um novo item.\n"
        "• QUANTIDADE: NUNCA adicione/anote um item assumindo 1 unidade. Um "
        "'sim/quero/pode' confirma o PRODUTO, não a QUANTIDADE. Se o cliente "
        "não disse QUANTAS unidades quer, PERGUNTE 'Quantas unidades?' e ESPERE "
        "a resposta antes de adicionar ou anotar."
    )

    # ── Plano B: gate DETERMINÍSTICO de confirmação de pedido (gated weak) ──────
    # O reforço de prompt acima ajuda, mas modelo fraco ainda pode anotar cedo
    # (ex.: lê um "Sim" de confirmação de dosagem como "fechar"). `anotar_pedido_
    # balcao` INSERE em `orders` + dispara o transfer determinístico do worker —
    # reverter depois seria apagar pedido do banco. Então BLOQUEAMOS antes de
    # executar: a 1ª chamada com uma lista nova é vetada (modelo é instruído a
    # confirmar o pedido completo); só a 2ª chamada, num turno POSTERIOR, com a
    # MESMA lista (cliente confirmou) executa. Lista diferente → re-bloqueia
    # (auto-corretivo). Snapshot no Redis sobrevive entre turnos; set local
    # impede "confirmar" no mesmo turno. Falha de Redis = falha aberta (não pior
    # que hoje). Claude/forte: gate desligado (_v_scaffold False).
    _order_gate = None
    if _v_scaffold:
        from db.redis_client import get_redis as _get_redis_gate

        def _snap_order(itens) -> str:
            return "|".join(sorted(
                f"{str(i.get('name', '')).strip().lower()}#{i.get('qty')}"
                for i in (itens or []) if isinstance(i, dict)
            ))

        _confirm_key = f"order_confirm:{tenant_id}:{phone_num}"
        _blocked_this_turn: set[str] = set()

        def _block_msg(itens) -> str:
            lista = "\n".join(
                f"• {i.get('qty')}x {i.get('name')}"
                for i in (itens or []) if isinstance(i, dict)
            )
            return (
                "PEDIDO NÃO REGISTRADO — confirmação pendente. Você ainda NÃO "
                "confirmou o pedido COMPLETO com o cliente. NÃO registre agora. "
                "Envie ao cliente a lista completa e pergunte 'Posso fechar o "
                f"pedido?':\n{lista}\nSó chame anotar_pedido_balcao DE NOVO depois "
                "que o cliente confirmar explicitamente ESTE fechamento."
            )

        def _qty_block_msg(produto) -> str:
            p = str(produto or "esse item").strip() or "esse item"
            return (
                f"QUANTIDADE PENDENTE — NÃO adicione ainda. O cliente confirmou o "
                f"PRODUTO ({p}), mas NÃO disse QUANTAS unidades quer. Um 'sim/"
                f"quero/pode' confirma o produto, não a quantidade. Pergunte ao "
                f"cliente 'Quantas unidades de {p} você quer?' e ESPERE a resposta. "
                f"Só chame adicionar_ao_carrinho DEPOIS que ele informar a quantidade."
            )

        async def _order_gate(tc):  # noqa: ANN001
            name = tc.get("name")
            # ── Gate de quantidade (modo normal) — andaime weak ─────────────
            # Veta adicionar_ao_carrinho de item NOVO quando o cliente não
            # declarou a quantidade: a LLM fraca pula a etapa e assume qty=1
            # (sintoma real). qty>1 ou item já no carrinho (ajuste) passam livres.
            if name == "adicionar_ao_carrinho":
                args = tc.get("args") or {}
                try:
                    _qty = int(args.get("quantidade") or 1)
                except (TypeError, ValueError):
                    _qty = 1
                _produto = str(args.get("produto") or "").strip().lower()
                _in_cart = any(
                    _produto and _produto in str(it.get("name", "")).lower()
                    for it in (cart.get("items") or [])
                )
                if _qty > 1 or _in_cart:
                    return None  # quantidade explícita (>1) ou ajuste de item
                if _stated_quantity(state.get("current_message", "")):
                    return None  # cliente declarou a quantidade nesta mensagem
                log.info("vendedor.qty_gate.blocked",
                         tenant_id=tenant_id, produto=_produto)
                return _qty_block_msg(args.get("produto"))
            if name != "anotar_pedido_balcao":
                return None
            itens = (tc.get("args") or {}).get("itens") or []
            snap = _snap_order(itens)
            # Mesmo turno: se já bloqueamos esta lista agora, NÃO liberar — a
            # confirmação tem que vir do cliente num próximo turno.
            if snap in _blocked_this_turn:
                return _block_msg(itens)
            try:
                r = _get_redis_gate()
                prev = await r.get(_confirm_key)
                if isinstance(prev, bytes):
                    prev = prev.decode()
                if prev and prev == snap:
                    await r.delete(_confirm_key)  # cliente confirmou ESTA lista
                    return None
                await r.setex(_confirm_key, 900, snap)
            except Exception as _gexc:  # noqa: BLE001
                log.warning("vendedor.order_gate_redis_failed", exc=str(_gexc))
                return None  # falha aberta — não bloqueia
            _blocked_this_turn.add(snap)
            log.info("vendedor.order_confirm_gate.blocked",
                     tenant_id=tenant_id, items=len(itens))
            return _block_msg(itens)

    # Pré-atendimento: handoff (single-hop) ao farmacêutico p/ validar na bula
    # só quando a capability está ON E o farmacêutico está ativo no tenant.
    # Usado tanto no prompt (.flow) quanto na escolha das tools de fluxo.
    available_set = set(state.get("available_skills", []))
    preattend_handoff_enabled = bool(
        caps.get("pharmacist_validation") and "farmaceutico" in available_set
    )

    # Defaults dos sinais de fluxo — garantem que o pós-processamento funcione
    # mesmo se o except de setup disparar antes do run_tool_loop.
    sig_handoff_to: str | None = None
    sig_handoff_ctx = ""
    sig_escalate = False
    sig_end = False

    # ── Setup de prompt + tools (bifurca por modo) ────────────────────────────
    # Um único try/except cobre tanto o setup quanto a invocação do LLM abaixo.
    try:
        if use_preattendimento:
            # ── Modo pré-atendimento (sem estoque) ───────────────────────────
            # PromptBuilder: .core/.section = ESTÁVEL (cacheado);
            # .volatile = por-turno (após o marker de cache).
            pb = PromptBuilder(
                persona, "vendedor",
                override=skill_prompts.get("vendedor_preattendimento") or None,
            )
            pb.core(_SYSTEM_PRE_ATENDIMENTO)
            # Controle de fluxo via tools: escalate + end sempre; handoff ao
            # farmacêutico só no single-hop de validação (cap ON + ativo).
            pb.flow(
                ("farmaceutico",) if preattend_handoff_enabled else (),
                handoff=preattend_handoff_enabled, escalate=True, end=True,
            )

            skill_extra = skill_instructions.get("vendedor", "")
            if skill_extra:
                pb.section("[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA]\n" + skill_extra)

            # ── Volátil: status do cliente + contexto de handoff ─────────────
            try:
                customer_block = _build_preattendimento_customer_block(
                    sales_config,
                    customer,
                    skip_known_field_confirmation=caps["skip_known_field_confirmation"],
                )
                pb.volatile(customer_block)
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.pre_customer_block_failed", exc=str(_exc))

            # Contexto temporal (hora + período) — cap attendance.time_aware_greeting
            if caps["time_aware_greeting"]:
                try:
                    from services.time_context import build_time_context_block
                    pb.volatile(build_time_context_block())
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.pre_time_block_failed", exc=str(_exc))

            if received_handoff and handoff_context:
                pb.volatile(
                    "[CONTEXTO DE HANDOFF]\n"
                    f"O cliente já mencionou interesse em: {handoff_context}\n"
                    "Adicione esse produto à lista de itens e continue a coleta. "
                    "Não precisa perguntar novamente sobre ele — só confirme e pergunte se quer mais algo."
                )
            elif received_handoff and prev_response:
                pb.volatile(
                    "[CONTEXTO DE HANDOFF]\n"
                    f"Continuando atendimento iniciado: {prev_response[:200]}"
                )

            # Rascunho atual do pedido — VOLÁTIL. No pré-atendimento o cart NÃO
            # era mostrado ao modelo (diferente do modo normal), então ele
            # dependia só do histórico e ESQUECIA itens já coletados. Renderizar
            # a lista corrente (de registrar_itens_interesse) é o que mantém o
            # modelo ciente do pedido inteiro — base para listar/confirmar certo.
            _draft = (cart.get("items") or []) if isinstance(cart, dict) else []
            if _draft:
                _draft_lines = "\n".join(
                    f"  • {i.get('qty')}x {i.get('name')}"
                    for i in _draft if isinstance(i, dict) and i.get("name")
                )
                pb.volatile(
                    "PEDIDO EM ANDAMENTO (rascunho já coletado — itens que o "
                    "cliente JÁ pediu nesta conversa; não os perca):\n"
                    f"{_draft_lines}"
                )

            if _v_scaffold:
                pb.volatile(_SALES_DISCIPLINE)
                # Allowlist estrita de dados (modelo fraco ignora "NÃO pergunte
                # endereço"). No pré-atendimento NUNCA há pagamento/entrega — tudo
                # isso é resolvido no balcão. Gated → caminho forte intacto.
                try:
                    from services.sales_config import build_field_discipline_block
                    pb.volatile(build_field_discipline_block(
                        sales_config, allow_payment=False, allow_delivery=False,
                    ))
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.pre_field_discipline_failed", exc=str(_exc))

            system_prompt, volatile_prompt = pb.build()
            messages = _build_messages(state, system_prompt, volatile_prompt=volatile_prompt)

            from agents.tools.customer import (
                make_save_customer_tool,
                make_consultar_pedido_tool,
            )
            from agents.tools.balcao import (
                make_anotar_pedido_balcao_tool,
                make_registrar_itens_interesse_tool,
            )
            tools = [
                make_save_customer_tool(schema_name, phone_num, customer),
                make_consultar_pedido_tool(schema_name, phone_num),
                make_registrar_itens_interesse_tool(schema_name, cart, merge=_v_scaffold),
                make_anotar_pedido_balcao_tool(schema_name, phone_num, customer, cart),
            ]

            # Pré-atendimento marca o carrinho como 'balcao' para o save_context
            # persistir esse modo (o portal/relatórios diferenciam de 'catalogo').
            # É o cart-rascunho gravado por `registrar_itens_interesse` que torna
            # a recuperação possível neste modo.
            state = {**state, "stock_mode": "balcao"}

        else:
            # ── Modo normal (com consulta de estoque) ────────────────────────
            # PromptBuilder: .core/.section = ESTÁVEL (cacheado);
            # .volatile = por-turno. Blocos de capability vivem em prompts/commerce.py.
            pb = PromptBuilder(
                persona, "vendedor",
                override=skill_prompts.get("vendedor") or None,
                extra=skill_instructions.get("vendedor") or None,
            )
            pb.core(_SYSTEM)

            if caps["cross_sell"]:
                max_sug = int(cap_config.get("cross_sell", {}).get("max_suggestions_per_turn", 1))
                pb.section(_commerce.cross_sell_block(max_sug))

            if caps["shipping"]:
                pb.section(_commerce.shipping_block())

            # Autocompletar endereço por CEP (ViaCEP) — ativa quando a farmácia
            # coleta endereço: CEP é campo obrigatório, entrega ativa no
            # fechamento, ou frete por CEP ligado. Bloco ESTÁVEL (depende só da
            # config do tenant) → prefixo cacheado.
            _collects_address = (
                "cep" in (sales_config.get("required_fields") or [])
                or bool(sales_config.get("ask_delivery"))
                or caps["shipping"]
            )
            if _collects_address:
                pb.section(_commerce.cep_lookup_block())

            if caps["pix"]:
                pix_cfg = cap_config.get("pix", {})
                auto_send = pix_cfg.get("auto_send_after_confirm", True)
                pb.section(_commerce.pix_block(auto_send))

            if caps["customer_memory"]:
                pb.section(_commerce.customer_memory_block())

            # Modo de fechamento (coleta vs completo) — definido pela farmácia,
            # sobrepõe a intuição do agente sobre perguntar pagamento/entrega.
            # É ESTÁVEL (depende só da config do tenant) → fica no prefixo cacheado.
            try:
                from services.sales_config import build_checkout_flow_block
                pb.section(build_checkout_flow_block(sales_config))
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.checkout_flow_block_failed", exc=str(_exc))

            # Controle de fluxo via tools (handoff p/ farmaceutico/genericos/
            # principio_ativo + escalation humana + fim de atendimento).
            from agents.skills_registry import allowed_handoffs_for
            pb.flow(
                allowed_handoffs_for("vendedor"),
                handoff=True, escalate=True, end=True,
            )

            # extra_instructions do dono (mesmo formato "— sobreponha..." do _base).
            pb.extra_instructions()

            # ── VOLÁTIL (após o marcador de cache) ───────────────────────────
            # Dados do cliente (memória) — mudam conforme o cadastro.
            if caps["customer_memory"]:
                try:
                    from services.persona import build_customer_memory_block
                    pb.volatile(build_customer_memory_block(customer))
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.memory_block_failed", exc=str(_exc))

            # Status dos campos obrigatórios ("✓ temos / ✗ falta") — muda a cada
            # dado que o cliente fornece → volátil.
            try:
                from services.sales_config import build_sales_config_block
                pb.volatile(build_sales_config_block(sales_config, customer))
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.sales_config_block_failed", exc=str(_exc))

            # Endereço já cadastrado (modo completo + ask_delivery) — depende do
            # cliente → volátil. Permite confirmar em vez de pedir do zero.
            try:
                from services.sales_config import build_known_address_hint
                pb.volatile(build_known_address_hint(sales_config, customer))
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.address_hint_failed", exc=str(_exc))

            # Contexto temporal (hora + período) — cap attendance.time_aware_greeting
            if caps["time_aware_greeting"]:
                try:
                    from services.time_context import build_time_context_block
                    pb.volatile(build_time_context_block())
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.time_block_failed", exc=str(_exc))

            if received_handoff:
                pb.volatile(
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
                pb.volatile(
                    "Carrinho atual do cliente:\n" + "\n".join(cart_lines)
                    + f"\n  Subtotal: R$ {cart.get('subtotal', 0):.2f}"
                )

            if _v_scaffold:
                pb.volatile(_SALES_DISCIPLINE)
                # Allowlist estrita de dados (modelo fraco ignora "NÃO pergunte
                # endereço" do build_checkout_flow_block). Respeita o checkout_mode
                # e os flags ask_payment/ask_delivery do tenant. Gated → caminho
                # forte intacto.
                try:
                    from services.sales_config import build_field_discipline_block
                    _co_mode = (sales_config.get("checkout_mode") or "completo").lower()
                    _allow_pay = _co_mode != "coleta" and bool(sales_config.get("ask_payment", True))
                    _allow_del = _co_mode != "coleta" and bool(sales_config.get("ask_delivery", False))
                    pb.volatile(build_field_discipline_block(
                        sales_config, allow_payment=_allow_pay, allow_delivery=_allow_del,
                    ))
                except Exception as _exc:  # noqa: BLE001
                    log.warning("vendedor.field_discipline_failed", exc=str(_exc))

            system_prompt, volatile_prompt = pb.build()
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
                make_consultar_cep_tool,
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
            # Autocompletar endereço por CEP — só quando a farmácia coleta
            # endereço (mesmo gate do bloco de prompt cep_lookup_block).
            if _collects_address:
                tools.append(make_consultar_cep_tool(schema_name, phone_num, customer))

            # Correção de nome ("Você quis dizer…?") — OPCIONAL por tenant via a
            # capability `attendance.medication_name_suggestion` (ON por default).
            # No fluxo de vendas ajuda quando nem a busca fuzzy do catálogo achou:
            # oferece o nome correto do remédio (verificado na ANVISA) antes de
            # desistir/transferir. Mesmo padrão de `principio_ativo`. Só sugere,
            # nunca auto-corrige (invariante de segurança).
            try:
                from services import capabilities as cap_svc
                if await cap_svc.is_enabled(tenant_id, "attendance.medication_name_suggestion"):
                    _scfg = await cap_svc.get_config(tenant_id, "attendance.medication_name_suggestion") or {}
                    try:
                        _max_c = int(_scfg.get("max_candidates", 3))
                    except (TypeError, ValueError):
                        _max_c = 3
                    from agents.tools.medicamento_suggest import make_sugerir_nome_medicamento_tool
                    tools.append(make_sugerir_nome_medicamento_tool(
                        tenant_id=tenant_id,
                        max_candidates=_max_c,
                        enable_web=bool(_scfg.get("enable_web_search", True)),
                    ))
            except Exception as _exc:  # noqa: BLE001
                log.warning("vendedor.medication_suggest_tool_failed", exc=str(_exc))

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
                        cart=cart,
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

        # ── Tools de controle de fluxo (handoff/escalate/end) por modo ───────
        from agents.tools.flow_control import (
            make_handoff_tool, make_escalate_tool, make_end_tool,
        )
        flow_tools = [make_escalate_tool(), make_end_tool()]
        if use_preattendimento:
            if preattend_handoff_enabled:
                ht = make_handoff_tool(("farmaceutico",))
                if ht is not None:
                    flow_tools.insert(0, ht)
        else:
            from agents.skills_registry import allowed_handoffs_for
            ht = make_handoff_tool(allowed_handoffs_for("vendedor"))
            if ht is not None:
                flow_tools.insert(0, ht)
        tools = list(tools) + flow_tools

        # ── post_loop_hook: travas históricas do pré-atendimento ─────────────
        # Roda DENTRO do run_tool_loop, após o loop. Preserva 1:1 o force-call de
        # anotar_pedido_balcao e o draft-fallback Haiku ([[project_preattend_draft_fallback]],
        # [[project_balcao_cart_mutation]]). NÃO remover.
        async def _vendedor_post_hook(*, lc_messages, llm, llm_with_tools, tools, result):
            if not use_preattendimento:
                return
            from langchain_core.messages import HumanMessage as _HM, ToolMessage as _TM

            # Force-call: "fechou" sem anotar_pedido_balcao → o pedido não existe.
            balcao_ok = any(
                tc.get("name") == "anotar_pedido_balcao"
                and "PEDIDO_ANOTADO:OK" in str(tc.get("result_preview", ""))
                for tc in result.tool_calls_trace
            )
            lower_resp = (result.final_text or "").lower()
            if any(h in lower_resp for h in _CLOSING_HINTS) and not balcao_ok:
                log.warning("vendedor.preattendimento.closing_without_tool",
                            final_response_preview=result.final_text[:200])
                # HumanMessage (não SystemMessage): Anthropic rejeita system
                # não-consecutiva depois de Human/AI. (Regressão histórica.)
                lc_messages.append(_HM(content=(
                    "[INSTRUÇÃO INTERNA DO SISTEMA — não é o cliente falando]\n"
                    "⚠️ VOCÊ ESQUECEU DE CHAMAR A TOOL `anotar_pedido_balcao`.\n"
                    "Sua resposta dá a entender que o pedido foi anotado, mas "
                    "a tool NÃO FOI CHAMADA — o pedido NÃO existe no sistema.\n\n"
                    "AGORA: chame `anotar_pedido_balcao` IMEDIATAMENTE passando "
                    "todos os itens que o cliente pediu nesta conversa. Use o "
                    "formato itens=[{\"name\":\"...\",\"qty\":N}, ...]. "
                    "NÃO escreva texto antes — só a tool call."
                )))
                response2 = await llm_with_tools.ainvoke(lc_messages)
                if response2.tool_calls:
                    lc_messages.append(response2)
                    tool_map = {t.name: t for t in tools}
                    for tc in response2.tool_calls:
                        tool = tool_map.get(tc["name"])
                        rec: dict = {"iter": "forced", "name": tc.get("name"), "args": tc.get("args")}
                        # O force-call NÃO pode furar o gate de confirmação: se o
                        # pedido não foi confirmado, vetamos a execução forçada e o
                        # modelo é levado a pedir a confirmação (response3 abaixo).
                        _gated = await _order_gate(tc) if _order_gate is not None else None
                        if _gated is not None:
                            rec["gated"] = True
                            rec["result_preview"] = str(_gated)[:300]
                            lc_messages.append(_TM(content=str(_gated), tool_call_id=tc["id"]))
                            result.tool_calls_trace.append(rec)
                            continue
                        if tool:
                            try:
                                r = await tool.ainvoke(tc["args"])
                                result.last_tool_result = str(r)
                                rec["result_preview"] = str(r)[:300]
                                lc_messages.append(_TM(content=str(r), tool_call_id=tc["id"]))
                            except Exception as tool_exc:  # noqa: BLE001
                                rec["error"] = str(tool_exc)
                        result.tool_calls_trace.append(rec)
                    response3 = await llm.ainvoke(lc_messages)
                    result.final_text = _extract_text(response3.content) or result.final_text
                else:
                    log.error("vendedor.preattendimento.force_call_failed")
                    result.final_text = (
                        "Tive um problema técnico ao registrar seu pedido agora. "
                        "Vou te transferir para um atendente humano completar."
                    )

            # Draft-fallback: itens listados em texto sem tool de cart → extrai
            # via Haiku e grava rascunho (mesma camada 1 de antes).
            _any_cart_tool = any(
                tc.get("name") in ("registrar_itens_interesse", "anotar_pedido_balcao")
                for tc in result.tool_calls_trace
            )
            if not _any_cart_tool and result.final_text and _detect_item_listing(result.final_text):
                try:
                    extracted = await _extract_items_from_dialog(lc_messages, llm_factory)
                    if extracted:
                        cart["items"] = extracted
                        cart["subtotal"] = 0.0
                        _DRAFT_FALLBACK.labels(tenant_id=tenant_id or "unknown").inc()
                        log.info("vendedor.draft.extracted_by_fallback",
                                 items=len(extracted), session=session_key)
                except Exception as _fb_exc:  # noqa: BLE001
                    log.warning("vendedor.draft.fallback_failed", exc=str(_fb_exc))

        # ── Tool-loop compartilhado (runtime) ────────────────────────────────
        # `_v_scaffold` (andaime p/ Gemini/weak) já resolvido no topo do skill.
        # Liga a guarda domínio+fluxo do runtime (não descartar resultado de tool
        # ao transferir no mesmo turno). No-op para Claude/GPT forte.
        from agents.runtime import run_tool_loop, StockRecall, ClaimGrounding
        from config import settings
        llm = llm_factory("skill")
        # Force-recall de estoque (andaime weak): só no modo normal (ERP,
        # stock_check ON) — em pré-atendimento não há catálogo autoritativo.
        # Suprime quando carrinho/pedido foi mexido no turno (item já validado).
        _stock_recall = None
        if _v_scaffold and not use_preattendimento:
            _stock_recall = StockRecall(
                search_tool="buscar_produto",
                suppress_tools=("adicionar_ao_carrinho", "finalizar_pedido"),
            )
        # Grounding de fato farmacológico (andaime weak): o vendedor não binda a
        # base de referência, então sem tool de fonte o runtime cai numa fala
        # segura quando o modelo voluntaria genérico/composição de memória.
        # No-op p/ Claude/GPT forte. Vale nos dois modos (normal e pré-atendimento).
        _claim_grounding = ClaimGrounding() if _v_scaffold else None
        result = await run_tool_loop(
            llm, list(messages), tools, settings.skill_max_tool_iterations,
            post_loop_hook=_vendedor_post_hook,
            defer_premature_flow=_v_scaffold,
            domain_tool_gate=_order_gate,
            stock_recall=_stock_recall,
            claim_grounding=_claim_grounding,
        )
        final_response   = result.final_text
        tool_calls_trace = result.tool_calls_trace
        iters_used       = result.iters_used
        last_tool_result = result.last_tool_result
        _node_error      = result.node_error
        # Sinais de fluxo vindos das TOOLS (handoff/escalate/end).
        sig_handoff_to   = result.handoff_to
        sig_handoff_ctx  = result.handoff_context
        sig_escalate     = result.escalate
        sig_end          = result.end_conversation

        # Último fallback vendedor: se ainda vazio, usa o último tool result.
        # NÃO aplica quando há handoff (a outra especialidade fala) — senão
        # poluiria a resposta concatenada com "Pronto! Algo mais?". Com marcador
        # antigo isso não acontecia (o texto vinha não-vazio antes do parse);
        # com a tool de handoff o texto pode vir vazio de propósito.
        if (not final_response or not final_response.strip()) and not sig_handoff_to:
            final_response = last_tool_result or "Pronto! Algo mais que posso ajudar?"

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

    # ── Markup nativo de tool-call vazado (DeepSeek/weak) ────────────────────
    # Limpa o markup que escapou como texto E recupera o sinal de fluxo perdido
    # (quando o modelo serializa a tool em texto, `response.tool_calls` vem vazio
    # e os `sig_*` ficam zerados). Roda ANTES dos parsers de marcador legado.
    # Mesma rede de segurança de run_skill. Cf. _strip_leaked_tool_markup.
    final_response, mk_handoff, mk_ctx, mk_escalate, mk_end = (
        _strip_leaked_tool_markup(final_response)
    )

    # ── Escalation humana: TOOL (sig_escalate) OU marcador [[ESCALATE]] ──────
    # Caminho primário = tool `transferir_para_atendente`; marcador é fallback.
    final_response, parsed_escalate = _parse_escalate(final_response)
    explicit_escalate = sig_escalate or mk_escalate or parsed_escalate
    if explicit_escalate:
        log.info("vendedor.explicit_escalate",
                 mode="pre_atendimento" if use_preattendimento else "normal",
                 schema=schema_name)

    # ── Fim de atendimento: TOOL (sig_end) OU marcador [[END]] ───────────────
    # SEMPRE limpamos o marcador do texto. Só propagamos o flag quando NÃO houve
    # balcão nem escalation (essas têm prioridade — já fecham via handoff).
    final_response, parsed_end = _parse_end(final_response)
    end_conversation = sig_end or mk_end or parsed_end
    if balcao_called or explicit_escalate:
        end_conversation = False
    if end_conversation:
        log.info("vendedor.end_conversation",
                 mode="pre_atendimento" if use_preattendimento else "normal",
                 schema=schema_name)

    # ── Handoff: TOOL (sig_handoff_to) com prioridade, marcador como fallback ─
    # SEMPRE rodamos _parse_handoff para LIMPAR qualquer marcador residual do
    # texto antes de enviar ao cliente (mesmo em pré-atendimento, onde por padrão
    # não roteamos). O SINAL da tool de fluxo vence; o marcador é a rede de
    # segurança.
    handoff_target: str | None = None
    handoff_ctx_new = ""
    if not received_handoff:
        final_response, parsed_target, parsed_ctx = _parse_handoff(final_response)
        if not use_preattendimento:
            # Modo NORMAL: tool, markup vazado ou marcador roteiam livremente.
            handoff_target  = sig_handoff_to or mk_handoff or parsed_target
            handoff_ctx_new = sig_handoff_ctx or mk_ctx or parsed_ctx
        else:
            # Pré-atendimento: handoff SÓ ao farmacêutico (single-hop de
            # validação na bula). A tool de fluxo já só foi bindada quando
            # `preattend_handoff_enabled` — então sig_handoff_to só pode ser
            # "farmaceutico". O marcador (fallback) respeita o mesmo gating
            # explicitamente. Cf. [[reference_three_operating_modes]].
            if sig_handoff_to == "farmaceutico":
                handoff_target  = "farmaceutico"
                handoff_ctx_new = sig_handoff_ctx
            elif mk_handoff == "farmaceutico":
                # Markup vazado recuperado — mesmo single-hop gating.
                handoff_target  = "farmaceutico"
                handoff_ctx_new = mk_ctx
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
