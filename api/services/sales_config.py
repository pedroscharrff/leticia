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

SALES_CONFIG_DEFAULTS: dict[str, Any] = {
    "required_fields": ["nome"],
    "max_attempts": 3,
    "fallback_message": DEFAULT_FALLBACK,
}


async def load_sales_config(tenant_id: str) -> dict:
    """Returns the tenant's sales config (with defaults filled in)."""
    async with get_db_conn() as conn:
        row = await conn.fetchrow(
            "SELECT required_fields, max_attempts, fallback_message "
            "FROM public.tenant_sales_config WHERE tenant_id = $1",
            tenant_id,
        )
    if not row:
        return dict(SALES_CONFIG_DEFAULTS)
    return {
        "required_fields": list(row["required_fields"] or []),
        "max_attempts": int(row["max_attempts"] or 3),
        "fallback_message": row["fallback_message"] or DEFAULT_FALLBACK,
    }


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
