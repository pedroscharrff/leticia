"""
Tools de cadastro de cliente e gerenciamento de pedidos pendentes.

  • salvar_dados_cliente — UPSERT em customers conforme cliente informa
                            (nome, CPF, endereço, etc.)
  • consultar_pedido     — busca o status de um pedido pelo código único
  • cancelar_pedido      — marca pedido pending/confirmed como cancelled
  • editar_pedido        — altera itens/observações de pedido pending
"""
from __future__ import annotations

import json
from typing import Any

import structlog
from langchain_core.tools import tool

log = structlog.get_logger()


# Status técnico → frase amigável para o cliente no WhatsApp.
_STATUS_LABEL = {
    "pending":           "Recebido — aguardando confirmação do atendente",
    "aguardando_balcao": "Recebido — o atendente vai finalizar no balcão",
    "confirmed":         "Confirmado — em preparação",
    "processing":        "Em preparação",
    "shipped":           "Saiu para entrega",
    "delivered":         "Entregue",
    "cancelled":         "Cancelado",
}


# Mapa: chave amigável (usada pela tool) → coluna na tabela customers
_FIELD_TO_COLUMN = {
    "nome":         "name",
    "name":         "name",
    "email":        "email",
    "cpf":          "doc",
    "cpf_cnpj":     "doc",
    "doc":          "doc",
    "cep":          "cep",
    "rua":          "street",
    "street":       "street",
    "numero":       "street_number",
    "street_number":"street_number",
    "complemento":  "complement",
    "complement":   "complement",
    "bairro":       "neighborhood",
    "neighborhood": "neighborhood",
    "cidade":       "city",
    "city":         "city",
    "estado":       "state",
    "state":        "state",
    "observacoes":  "notes",
    "notes":        "notes",
}


def make_save_customer_tool(schema_name: str, phone: str, customer: dict):
    """
    Salva/atualiza dados do cliente no cadastro (tabela customers).
    Muta `customer` in-place para refletir no AgentState e no próximo
    turno (load_context recarrega do DB, mas mutamos pra mesma execução).
    """
    @tool
    async def salvar_dados_cliente(campos: dict[str, Any]) -> str:
        """
        Salva dados do cliente no cadastro. Chame esta tool assim que o
        cliente informar QUALQUER dado pessoal (nome, CPF, endereço, etc.),
        antes de tentar finalizar o pedido.

        Args:
            campos: dicionário com pares chave→valor. Chaves aceitas:
                nome, email, cpf, cep, rua, numero, complemento, bairro,
                cidade, estado, observacoes.
                Exemplo: {"nome": "João Silva", "cpf": "12345678900"}

        Returns:
            Confirmação do que foi salvo + estado atual do cadastro.
        """
        if not campos:
            return "Nenhum dado fornecido para salvar."

        # Normaliza chaves e descarta inválidas
        updates: dict[str, Any] = {}
        rejected: list[str] = []
        for k, v in (campos or {}).items():
            col = _FIELD_TO_COLUMN.get(str(k).lower().strip())
            if not col:
                rejected.append(k)
                continue
            if v is None or str(v).strip() == "":
                continue
            updates[col] = str(v).strip()

        if not updates:
            return (
                f"Nenhum campo válido informado. "
                f"Chaves aceitas: {', '.join(sorted(set(_FIELD_TO_COLUMN.keys())))}."
            )

        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")

                # UPSERT por phone
                cols = ", ".join(updates.keys())
                placeholders = ", ".join(f"${i+2}" for i in range(len(updates)))
                set_clause = ", ".join(
                    f"{c} = EXCLUDED.{c}" for c in updates.keys()
                )
                values = list(updates.values())
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO customers (phone, {cols}, auto_created)
                    VALUES ($1, {placeholders}, TRUE)
                    ON CONFLICT (phone) DO UPDATE
                    SET {set_clause}, updated_at = NOW()
                    RETURNING id, name, doc, email, cep, street, street_number,
                              complement, neighborhood, city, state, notes
                    """,
                    phone, *values,
                )

            # Atualiza dict em memória para refletir no resto do turno
            if row:
                fresh = dict(row)
                fresh["id"] = str(fresh["id"])
                customer.clear()
                customer.update(fresh)
                customer["phone"] = phone

            saved = ", ".join(f"{k}={v!r}" for k, v in updates.items())
            return f"✓ Dados salvos: {saved}"

        except Exception as exc:
            log.error("tool.salvar_dados_cliente.error",
                      phone=phone, exc=str(exc))
            return "Não consegui salvar os dados agora. Tente novamente."

    return salvar_dados_cliente


def make_consultar_pedido_tool(schema_name: str, phone: str):
    """
    Consulta o status de um pedido pelo código único (o ID curto entregue ao
    cliente no fechamento, ex.: '7e2a5b91') ou pelo ID completo.

    Escopo SEMPRE no phone do cliente — um cliente só enxerga os próprios
    pedidos, mesmo que informe um código de outra pessoa.
    """
    @tool
    async def consultar_pedido(codigo: str = "") -> str:
        """
        Consulta o status e os detalhes de um pedido do cliente pelo código
        único. Use quando o cliente perguntar "cadê meu pedido", "qual o status
        do pedido X", "meu pedido já saiu", etc.

        Args:
            codigo: código único do pedido (ID curto, ex.: '7e2a5b91') ou
                    completo. Se vazio, retorna o pedido mais recente do cliente.
        """
        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")

                if codigo and codigo.strip():
                    needle = codigo.strip().lstrip("#").lower()
                    row = await conn.fetchrow(
                        """
                        SELECT o.id, o.status, o.items, o.total, o.created_at, o.notes
                        FROM orders o
                        JOIN customers c ON c.id = o.customer_id
                        WHERE c.phone = $1 AND o.id::text ILIKE $2
                        ORDER BY o.created_at DESC LIMIT 1
                        """,
                        phone, f"{needle}%",
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT o.id, o.status, o.items, o.total, o.created_at, o.notes
                        FROM orders o
                        JOIN customers c ON c.id = o.customer_id
                        WHERE c.phone = $1
                        ORDER BY o.created_at DESC LIMIT 1
                        """,
                        phone,
                    )

            if not row:
                if codigo and codigo.strip():
                    return (
                        f"Não encontrei nenhum pedido com o código '{codigo.strip()}' "
                        "nos seus registros. Confere se o código está certo?"
                    )
                return "Não encontrei nenhum pedido seu no sistema."

            short_id = str(row["id"])[:8]
            status_label = _STATUS_LABEL.get(row["status"], row["status"])

            items = row["items"] or []
            if isinstance(items, str):
                items = json.loads(items)

            lines = [
                f"Pedido #{short_id}",
                f"Status: {status_label}",
            ]
            if items:
                lines.append("Itens:")
                for it in items:
                    qty = it.get("qty", 1)
                    name = it.get("name", "?")
                    lines.append(f"  • {qty}x {name}")
            total = float(row["total"] or 0)
            if total > 0:
                lines.append(f"Total: R$ {total:.2f}")
            if row["created_at"]:
                lines.append(f"Feito em: {row['created_at']:%d/%m/%Y %H:%M}")
            return "\n".join(lines)

        except Exception as exc:
            log.error("tool.consultar_pedido.error", phone=phone, exc=str(exc))
            return "Não consegui consultar o pedido agora. Tente novamente em instantes."

    return consultar_pedido


def make_cancel_order_tool(schema_name: str, phone: str):
    """
    Cancela um pedido (status pending ou confirmed) do cliente.
    Não permite cancelar pedidos em processing/shipped/delivered.
    """
    @tool
    async def cancelar_pedido(numero_pedido: str = "") -> str:
        """
        Cancela um pedido do cliente. Se `numero_pedido` não for fornecido,
        cancela o último pedido pendente. Use quando o cliente disser
        "cancela o pedido" ou "não quero mais".

        Args:
            numero_pedido: ID curto (ex: '7e2a5b91') ou completo do pedido.
                           Se vazio, cancela o último pendente.
        """
        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")

                # Encontra o pedido alvo pelo customer.phone
                if numero_pedido:
                    needle = numero_pedido.strip().lower()
                    row = await conn.fetchrow(
                        """
                        SELECT o.id, o.status, o.total
                        FROM orders o
                        JOIN customers c ON c.id = o.customer_id
                        WHERE c.phone = $1
                          AND o.id::text ILIKE $2
                        ORDER BY o.created_at DESC LIMIT 1
                        """,
                        phone, f"{needle}%",
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT o.id, o.status, o.total
                        FROM orders o
                        JOIN customers c ON c.id = o.customer_id
                        WHERE c.phone = $1
                          AND o.status IN ('pending', 'confirmed')
                        ORDER BY o.created_at DESC LIMIT 1
                        """,
                        phone,
                    )

                if not row:
                    return "Não encontrei pedido para cancelar."
                if row["status"] in ("processing", "shipped", "delivered"):
                    return (
                        f"Pedido #{str(row['id'])[:8]} já está em "
                        f"'{row['status']}' — não posso cancelar pelo chat. "
                        "Vou pedir um atendente humano para você."
                    )
                if row["status"] == "cancelled":
                    return f"Pedido #{str(row['id'])[:8]} já está cancelado."

                await conn.execute(
                    "UPDATE orders SET status = 'cancelled', updated_at = NOW() "
                    "WHERE id = $1",
                    row["id"],
                )

            return (
                f"✓ Pedido #{str(row['id'])[:8]} cancelado "
                f"(R$ {float(row['total']):.2f}). "
                "Posso ajudar com mais alguma coisa?"
            )

        except Exception as exc:
            log.error("tool.cancelar_pedido.error", phone=phone, exc=str(exc))
            return "Não consegui cancelar o pedido agora. Tente novamente."

    return cancelar_pedido


def make_edit_order_tool(schema_name: str, phone: str):
    """
    Edita itens ou observações de um pedido com status `pending`.
    Não permite editar depois de confirmed/processing/etc.
    """
    @tool
    async def editar_pedido(
        numero_pedido: str = "",
        adicionar: list[dict] | None = None,
        remover: list[str] | None = None,
        nova_observacao: str = "",
    ) -> str:
        """
        Edita um pedido pendente do cliente.

        Args:
            numero_pedido: ID curto (ex: '7e2a5b91'). Vazio = último pending.
            adicionar: lista de itens a adicionar, ex:
                       [{"name": "Dipirona 500mg", "qty": 2}]
                       Preço é resolvido do catálogo automaticamente.
            remover: lista de nomes (ou substrings) a remover do pedido.
                     Ex: ["Dipirona"]
            nova_observacao: substitui o campo de observações (opcional).
        """
        if not adicionar and not remover and not nova_observacao:
            return "Nenhuma alteração informada (adicionar/remover/nova_observacao)."

        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")

                if numero_pedido:
                    needle = numero_pedido.strip().lower()
                    row = await conn.fetchrow(
                        """
                        SELECT o.id, o.status, o.items, o.subtotal, o.discount, o.notes
                        FROM orders o
                        JOIN customers c ON c.id = o.customer_id
                        WHERE c.phone = $1 AND o.id::text ILIKE $2
                        ORDER BY o.created_at DESC LIMIT 1
                        """,
                        phone, f"{needle}%",
                    )
                else:
                    row = await conn.fetchrow(
                        """
                        SELECT o.id, o.status, o.items, o.subtotal, o.discount, o.notes
                        FROM orders o
                        JOIN customers c ON c.id = o.customer_id
                        WHERE c.phone = $1 AND o.status = 'pending'
                        ORDER BY o.created_at DESC LIMIT 1
                        """,
                        phone,
                    )

                if not row:
                    return "Não encontrei pedido pendente para editar."
                if row["status"] != "pending":
                    return (
                        f"Pedido já está '{row['status']}' — não dá pra editar pelo "
                        "chat. Vou chamar um atendente."
                    )

                items = row["items"] or []
                if isinstance(items, str):
                    items = json.loads(items)

                # Remoção
                if remover:
                    needles = [r.lower().strip() for r in remover]
                    items = [
                        it for it in items
                        if not any(n in it["name"].lower() for n in needles)
                    ]

                # Adição (resolve preço no catálogo)
                if adicionar:
                    for add in adicionar:
                        name = (add.get("name") or "").strip()
                        qty  = int(add.get("qty") or 1)
                        if not name or qty <= 0:
                            continue
                        prod = await conn.fetchrow(
                            "SELECT name, price FROM products "
                            "WHERE (name ILIKE $1 OR principio_ativo ILIKE $1) "
                            "AND active = TRUE LIMIT 1",
                            f"%{name}%",
                        )
                        if not prod:
                            continue
                        for it in items:
                            if it["name"].lower() == prod["name"].lower():
                                it["qty"] += qty
                                break
                        else:
                            items.append({
                                "name": prod["name"],
                                "price": float(prod["price"]),
                                "qty": qty,
                            })

                # Recalcula totais (mantém discount proporcional)
                new_subtotal = round(sum(i["price"] * i["qty"] for i in items), 2)
                old_subtotal = float(row["subtotal"] or 0)
                old_discount = float(row["discount"] or 0)
                disc_rate = (old_discount / old_subtotal) if old_subtotal > 0 else 0
                new_discount = round(new_subtotal * disc_rate, 2)
                new_total = round(new_subtotal - new_discount, 2)

                new_notes = nova_observacao.strip() or row["notes"]

                await conn.execute(
                    """
                    UPDATE orders
                    SET items    = $2::jsonb,
                        subtotal = $3,
                        discount = $4,
                        total    = $5,
                        notes    = $6,
                        updated_at = NOW()
                    WHERE id = $1
                    """,
                    row["id"], json.dumps(items),
                    new_subtotal, new_discount, new_total, new_notes,
                )

            summary = [f"✓ Pedido #{str(row['id'])[:8]} atualizado."]
            if items:
                summary.append("Itens agora:")
                for i in items:
                    summary.append(f"  • {i['qty']}x {i['name']} — R$ {i['price']:.2f}")
                summary.append(f"Total: R$ {new_total:.2f}")
            else:
                summary.append("(Pedido ficou sem itens — considere cancelar)")
            return "\n".join(summary)

        except Exception as exc:
            log.error("tool.editar_pedido.error", phone=phone, exc=str(exc))
            return "Não consegui editar o pedido agora. Tente novamente."

    return editar_pedido
