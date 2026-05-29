"""
Per-tenant sales configuration: which customer fields the vendedor agent
must collect before closing an order, and how to behave when the user
refuses to provide them.

Used by:
  - load_state node (to feed ConversationState["sales_config"])
  - vendedor skill (to inject required-fields block into the prompt)
  - criar_pedido tool (to block order creation + count attempts)
  - portal/admin routers (CRUD)
"""
from __future__ import annotations

from typing import Any

from db.postgres import get_db_conn


# Whitelist of customer fields tenants can require. Each maps to the column(s)
# read on the customers row (see api/db/migrations/009_customers_v2.sql).
ALLOWED_FIELDS: dict[str, dict[str, Any]] = {
    "nome":         {"label": "Nome completo",   "columns": ["name"]},
    "cpf_cnpj":     {"label": "CPF / CNPJ",      "columns": ["doc"]},
    "email":        {"label": "E-mail",          "columns": ["email"]},
    "telefone":     {"label": "Telefone",        "columns": ["phone"]},
    "cep":          {"label": "CEP",             "columns": ["cep"]},
    "rua":          {"label": "Rua / logradouro","columns": ["street"]},
    "numero":       {"label": "Número",          "columns": ["street_number"]},
    "complemento":  {"label": "Complemento",     "columns": ["complement"]},
    "bairro":       {"label": "Bairro",          "columns": ["neighborhood"]},
    "cidade":       {"label": "Cidade",          "columns": ["city"]},
    "estado":       {"label": "Estado (UF)",     "columns": ["state"]},
    "observacoes":  {"label": "Observações",     "columns": ["notes"]},
}

DEFAULT_FALLBACK = (
    "Para finalizar o pedido eu preciso desses dados. Quando puder me passar, "
    "é só me chamar de volta que finalizo na hora!"
)

# Métodos de pagamento que o tenant pode aceitar (label legível p/ o agente)
PAYMENT_METHODS: dict[str, str] = {
    "pix":             "PIX",
    "cartao_credito":  "Cartão de crédito",
    "cartao_debito":   "Cartão de débito",
    "dinheiro":        "Dinheiro",
    "boleto":          "Boleto",
}
ALL_PAYMENT_METHODS = list(PAYMENT_METHODS.keys())

SALES_CONFIG_DEFAULTS: dict[str, Any] = {
    "required_fields": ["nome"],
    "max_attempts": 3,
    "fallback_message": DEFAULT_FALLBACK,
    "checkout_mode": "completo",   # 'coleta' | 'completo'
    "ask_payment": True,
    "ask_delivery": False,
    "accepted_payment_methods": list(ALL_PAYMENT_METHODS),
}


async def load_sales_config(tenant_id: str) -> dict:
    """Returns the tenant's sales config (with defaults filled in).

    Defensivo a schema drift: se as colunas de checkout_mode ainda não
    existirem (migration 041 não rodada), cai no SELECT antigo + defaults.
    """
    async with get_db_conn() as conn:
        try:
            row = await conn.fetchrow(
                "SELECT required_fields, max_attempts, fallback_message, "
                "checkout_mode, ask_payment, ask_delivery, accepted_payment_methods "
                "FROM public.tenant_sales_config WHERE tenant_id = $1",
                tenant_id,
            )
        except Exception:  # noqa: BLE001 — colunas novas ausentes (schema drift)
            try:
                row = await conn.fetchrow(
                    "SELECT required_fields, max_attempts, fallback_message, "
                    "checkout_mode, ask_payment, ask_delivery "
                    "FROM public.tenant_sales_config WHERE tenant_id = $1",
                    tenant_id,
                )
            except Exception:  # noqa: BLE001
                row = await conn.fetchrow(
                    "SELECT required_fields, max_attempts, fallback_message "
                    "FROM public.tenant_sales_config WHERE tenant_id = $1",
                    tenant_id,
                )
    if not row:
        return dict(SALES_CONFIG_DEFAULTS)
    cfg = {
        "required_fields": list(row["required_fields"] or []),
        "max_attempts": int(row["max_attempts"] or 3),
        "fallback_message": row["fallback_message"] or DEFAULT_FALLBACK,
        "checkout_mode": "completo",
        "ask_payment": True,
        "ask_delivery": False,
        "accepted_payment_methods": list(ALL_PAYMENT_METHODS),
    }
    # Campos novos (podem não existir no row dependendo do SELECT usado)
    keys = row.keys()
    if "checkout_mode" in keys:
        cfg["checkout_mode"] = row["checkout_mode"] or "completo"
    if "ask_payment" in keys:
        cfg["ask_payment"] = bool(row["ask_payment"])
    if "ask_delivery" in keys:
        cfg["ask_delivery"] = bool(row["ask_delivery"])
    if "accepted_payment_methods" in keys:
        methods = [m for m in (row["accepted_payment_methods"] or []) if m in PAYMENT_METHODS]
        cfg["accepted_payment_methods"] = methods or list(ALL_PAYMENT_METHODS)
    return cfg


def _customer_value(customer: dict, field_key: str) -> Any:
    """Return the relevant value from a customer dict for a required-field key."""
    spec = ALLOWED_FIELDS.get(field_key)
    if not spec:
        return None
    # The customer dict from load_state uses CRM-friendly keys, but tools may
    # also pass DB rows. Try both.
    aliases = {
        "name": ["name", "nome"],
        "doc": ["doc", "cpf", "cpf_cnpj"],
        "email": ["email"],
        "phone": ["phone", "telefone"],
        "cep": ["cep"],
        "street": ["street", "rua"],
        "street_number": ["street_number", "numero"],
        "complement": ["complement", "complemento"],
        "neighborhood": ["neighborhood", "bairro"],
        "city": ["city", "cidade"],
        "state": ["state", "estado"],
        "notes": ["notes", "observacoes"],
    }
    for col in spec["columns"]:
        for alias in aliases.get(col, [col]):
            v = customer.get(alias)
            if v is not None and str(v).strip() != "":
                return v
    return None


def missing_required_fields(config: dict, customer: dict | None) -> list[str]:
    """Return the list of required field keys that are missing/empty."""
    required = config.get("required_fields") or []
    cust = customer or {}
    return [f for f in required if not _customer_value(cust, f)]


def _format_known_address(customer: dict | None) -> str:
    """Monta uma linha legível com o endereço já salvo do cliente, se houver."""
    cust = customer or {}
    street = (cust.get("street") or "").strip()
    number = (cust.get("street_number") or "").strip()
    comp = (cust.get("complement") or "").strip()
    bairro = (cust.get("neighborhood") or "").strip()
    city = (cust.get("city") or "").strip()
    uf = (cust.get("state") or "").strip()
    cep = (cust.get("cep") or "").strip()
    if not any([street, bairro, city, cep]):
        return ""
    parts = []
    if street:
        parts.append(street + (f", {number}" if number else ""))
    if comp:
        parts.append(comp)
    if bairro:
        parts.append(bairro)
    if city:
        parts.append(city + (f"/{uf}" if uf else ""))
    if cep:
        parts.append(f"CEP {cep}")
    return ", ".join(parts)


def build_known_address_hint(config: dict, customer: dict | None) -> str:
    """Linha VOLÁTIL (depende do customer) com o endereço já cadastrado, para o
    agente confirmar em vez de pedir do zero. Só vale no modo completo c/
    ask_delivery. Vai no bloco volátil do prompt (não cacheado)."""
    if (config.get("checkout_mode") or "completo").lower() != "completo":
        return ""
    if not config.get("ask_delivery", False):
        return ""
    known = _format_known_address(customer)
    if not known:
        return ""
    return (
        "[ENTREGA — endereço já cadastrado deste cliente]\n"
        f"«{known}»\n"
        "Se o cliente escolher ENTREGA, confirme se é nesse mesmo endereço em "
        "vez de pedir tudo de novo. Só peça dados novos se ele quiser outro "
        "endereço (aí use `salvar_dados_cliente`)."
    )


def build_checkout_flow_block(config: dict) -> str:
    """
    Bloco que dita a PROFUNDIDADE do fechamento — definido pela farmácia,
    não pela intuição do agente. Sobrepõe o comportamento de fechamento
    padrão do prompt do vendedor.

    100% ESTÁVEL (depende só da config do tenant) → fica no prefixo cacheado.
    O endereço conhecido do cliente sai em build_known_address_hint (volátil).
    """
    mode = (config.get("checkout_mode") or "completo").lower()

    if mode == "coleta":
        return (
            "═══════════════════════════════════════════════════════════════\n"
            "MODO DE FECHAMENTO: COLETA SIMPLES (definido pela farmácia)\n"
            "═══════════════════════════════════════════════════════════════\n"
            "Esta farmácia NÃO quer que você conduza pagamento nem entrega. "
            "Seu papel é só montar o pedido e encaminhar ao balcão.\n"
            "Quando o cliente confirmar os itens (\"é só isso\", \"pode fechar\"):\n"
            "• Chame `finalizar_pedido(forma_pagamento=\"a_combinar\")` direto.\n"
            "• NUNCA pergunte forma de pagamento.\n"
            "• NUNCA pergunte entrega, retirada, endereço ou frete.\n"
            "• Após criar o pedido, confirme o número e diga que um atendente "
            "vai dar sequência para combinar pagamento e entrega.\n"
            "Isto SOBREPÕE qualquer instrução de fechamento padrão acima."
        )

    # modo "completo"
    ask_payment = config.get("ask_payment", True)
    ask_delivery = config.get("ask_delivery", False)
    accepted = [m for m in (config.get("accepted_payment_methods") or ALL_PAYMENT_METHODS)
                if m in PAYMENT_METHODS]
    if not accepted:
        accepted = list(ALL_PAYMENT_METHODS)

    lines = [
        "═══════════════════════════════════════════════════════════════",
        "MODO DE FECHAMENTO: COMPLETO (definido pela farmácia)",
        "═══════════════════════════════════════════════════════════════",
        "Conduza o cliente até o fechamento do pedido.",
    ]
    if ask_payment:
        labels = ", ".join(PAYMENT_METHODS[m] for m in accepted)
        lines.append(
            f"• Pergunte a forma de pagamento quando for fechar. Esta farmácia "
            f"aceita APENAS: {labels}. NUNCA ofereça um método fora dessa lista. "
            f"Passe a escolha (chave) para `finalizar_pedido`."
        )
        # Dica de mapeamento label→chave para o agente
        keymap = " | ".join(f"{PAYMENT_METHODS[m]}={m}" for m in accepted)
        lines.append(f"  (chaves válidas: {keymap})")
    else:
        lines.append(
            "• NÃO pergunte forma de pagamento — chame "
            "`finalizar_pedido(forma_pagamento=\"a_combinar\")`."
        )
    if ask_delivery:
        lines.append(
            "• Antes de fechar, pergunte se é ENTREGA ou RETIRADA na loja. "
            "Se for entrega, peça o endereço e salve com `salvar_dados_cliente` "
            "— mas ANTES verifique se já há um endereço cadastrado (pode aparecer "
            "um bloco \"[ENTREGA — endereço já cadastrado]\" abaixo) e confirme "
            "esse em vez de pedir do zero."
        )
    else:
        lines.append(
            "• NÃO pergunte sobre entrega, retirada ou endereço — isso é "
            "tratado no balcão. Apenas feche o pedido."
        )
    return "\n".join(lines)


def build_sales_config_block(config: dict, customer: dict | None) -> str:
    """
    Builds the dynamic prompt block injected into the vendedor agent system
    prompt. Lists the required fields, their current state, and the retry
    policy so the model knows what to ask for.
    """
    required = config.get("required_fields") or []
    if not required:
        return ""

    cust = customer or {}
    lines = ["## Dados obrigatórios para fechar o pedido"]
    lines.append(
        "Antes de chamar `criar_pedido`, garanta que TODOS os campos abaixo "
        "estão preenchidos no cadastro do cliente. Use `salvar_dados_cliente` "
        "assim que o cliente informar."
    )
    for key in required:
        spec = ALLOWED_FIELDS.get(key, {"label": key})
        present = _customer_value(cust, key)
        status = "✓ já temos" if present else "✗ faltando — peça"
        lines.append(f"- **{spec['label']}** — {status}")

    max_att = int(config.get("max_attempts") or 3)
    lines.append(
        f"\nSe o cliente não quiser fornecer um campo, insista até **{max_att} "
        f"vezes no total** (somando todas as tentativas), de forma educada e "
        f"sem repetir as mesmas palavras. Não chame `criar_pedido` enquanto "
        f"faltar campo obrigatório — a tool vai retornar erro e contar "
        f"tentativa."
    )
    fallback = (config.get("fallback_message") or "").strip()
    if fallback:
        lines.append(
            "\nApós esgotar as tentativas (a tool retorna `max_attempts_reached`), "
            "responda EXATAMENTE com a mensagem abaixo (sem inventar nada):\n"
            f"> {fallback}"
        )
    return "\n".join(lines)
