"""
Tools de inventário e vendas usadas pelo skill vendedor:
  • buscar_produto      — busca no catálogo
  • adicionar_ao_carrinho — adiciona item ao carrinho
  • finalizar_pedido    — cria o pedido no DB e limpa o carrinho
"""
from __future__ import annotations

import json
import structlog
from langchain_core.tools import tool

log = structlog.get_logger()


def make_inventory_tool(schema_name: str):
    """
    Factory — retorna uma tool com o schema do tenant injetado via closure.
    """
    @tool
    async def buscar_produto(nome: str) -> str:
        """
        Busca um produto no catálogo da farmácia pelo nome.
        Retorna lista de produtos com preço e disponibilidade.
        Use sempre que o cliente perguntar sobre disponibilidade ou preço de um produto.

        Args:
            nome: Nome do produto, medicamento ou categoria a buscar.
        """
        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")
                rows = await conn.fetch(
                    """
                    SELECT name, price, stock_qty, unit, active, principio_ativo, fabricante
                    FROM products
                    WHERE active = TRUE
                      AND (
                          name ILIKE $1
                          OR description ILIKE $1
                          OR principio_ativo ILIKE $1
                          OR barcode = $2
                      )
                    ORDER BY name
                    LIMIT 10
                    """,
                    f"%{nome}%",
                    nome,
                )

            if not rows:
                return f"Produto '{nome}' não encontrado no catálogo."

            lines = []
            for r in rows:
                qty_info = f"{r['stock_qty']} {r['unit']}" if r["stock_qty"] is not None else "disponível"
                lines.append(f"• {r['name']} — R$ {r['price']:.2f} ({qty_info})")

            return "Produtos encontrados:\n" + "\n".join(lines)

        except Exception as exc:
            log.warning("tool.buscar_produto.error", nome=nome, exc=str(exc))
            return f"Não foi possível consultar o catálogo no momento."

    return buscar_produto


def make_add_to_cart_tool(schema_name: str, cart: dict):
    """
    Factory — retorna tool de adicionar ao carrinho com estado injetado.
    O carrinho é mutado in-place e depois refletido no estado do grafo.
    """
    @tool
    async def adicionar_ao_carrinho(produto: str, quantidade: int = 1) -> str:
        """
        Adiciona um produto ao carrinho do cliente.
        Use quando o cliente confirmar que quer comprar um produto.

        Args:
            produto: Nome exato do produto a adicionar.
            quantidade: Quantidade desejada (padrão: 1).
        """
        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")
                row = await conn.fetchrow(
                    "SELECT name, price FROM products WHERE (name ILIKE $1 OR principio_ativo ILIKE $1) AND active = TRUE LIMIT 1",
                    f"%{produto}%",
                )

            if not row:
                return f"Produto '{produto}' não encontrado. Verifique o nome e tente novamente."

            # Atualiza carrinho in-place
            items = cart.setdefault("items", [])
            for item in items:
                if item["name"].lower() == row["name"].lower():
                    item["qty"] += quantidade
                    break
            else:
                items.append({"name": row["name"], "price": float(row["price"]), "qty": quantidade})

            cart["subtotal"] = sum(i["price"] * i["qty"] for i in items)

            return (
                f"✓ {quantidade}x {row['name']} (R$ {row['price']:.2f}/un) adicionado ao carrinho.\n"
                f"Subtotal: R$ {cart['subtotal']:.2f}"
            )

        except Exception as exc:
            log.warning("tool.adicionar_ao_carrinho.error", produto=produto, exc=str(exc))
            return "Não foi possível adicionar ao carrinho. Tente novamente."

    return adicionar_ao_carrinho


# Formas de pagamento aceitas
_VALID_PAYMENT = {
    "pix":              "PIX",
    "cartao_credito":   "Cartão de crédito",
    "cartao_debito":    "Cartão de débito",
    "dinheiro":         "Dinheiro",
    "boleto":           "Boleto",
}
# Desconto padrão por forma de pagamento (em %). Pode ser sobrescrito por tenant_sales_config.
_PAYMENT_DISCOUNT = {"pix": 0.10}


def make_finalize_order_tool(
    schema_name: str,
    cart: dict,
    session_key: str,
    phone: str,
):
    """
    Factory — retorna tool que cria o pedido no DB.
    Mutua o `cart` em memória (esvazia) após salvar com sucesso.
    """
    @tool
    async def finalizar_pedido(
        forma_pagamento: str = "pix",
        observacoes: str = "",
    ) -> str:
        """
        Cria o pedido no sistema com os itens do carrinho atual, marcando como
        pendente para o atendente humano confirmar. Aplica desconto da forma
        de pagamento se houver. Limpa o carrinho ao final.

        Use quando o cliente confirmar explicitamente que quer fechar o pedido.

        Args:
            forma_pagamento: uma de "pix", "cartao_credito", "cartao_debito",
                             "dinheiro", "boleto". Padrão: "pix".
            observacoes: notas opcionais (endereço de entrega, instruções).
        """
        if not cart.get("items"):
            return "Carrinho está vazio — adicione um produto antes de finalizar."

        forma = forma_pagamento.lower().strip()
        if forma not in _VALID_PAYMENT:
            return (
                f"Forma de pagamento '{forma_pagamento}' inválida. "
                f"Use: {', '.join(_VALID_PAYMENT.keys())}."
            )

        items_snapshot = list(cart["items"])  # snapshot p/ resumo após limpar
        subtotal = float(cart.get("subtotal", 0) or 0)
        discount_rate = _PAYMENT_DISCOUNT.get(forma, 0.0)
        discount = round(subtotal * discount_rate, 2)
        total = round(subtotal - discount, 2)

        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")

                # Garante customer pelo phone
                customer_row = await conn.fetchrow(
                    """
                    INSERT INTO customers (phone) VALUES ($1)
                    ON CONFLICT (phone) DO UPDATE SET updated_at = NOW()
                    RETURNING id
                    """,
                    phone,
                )
                customer_id = customer_row["id"]

                # Compõe notas com forma de pagamento + observações livres
                notes_full = f"pagamento={_VALID_PAYMENT[forma]}"
                if observacoes:
                    notes_full += f" | obs: {observacoes}"

                # Cria pedido (status=pending → atendente humano confirma depois)
                order_row = await conn.fetchrow(
                    """
                    INSERT INTO orders
                        (customer_id, session_key, items, subtotal, discount, total, status, notes)
                    VALUES ($1, $2, $3::jsonb, $4, $5, $6, 'pending', $7)
                    RETURNING id
                    """,
                    customer_id, session_key,
                    json.dumps(items_snapshot),
                    subtotal, discount, total, notes_full,
                )
                order_id = str(order_row["id"])

                # Atualiza histórico do cliente
                await conn.execute(
                    """
                    UPDATE customers
                    SET total_orders   = total_orders + 1,
                        total_spent    = total_spent + $2,
                        last_contact_at = NOW(),
                        updated_at     = NOW()
                    WHERE id = $1
                    """,
                    customer_id, total,
                )

                # Esvazia o carrinho no DB
                await conn.execute(
                    "DELETE FROM cart WHERE session_key = $1", session_key,
                )

            # Esvazia o carrinho em memória para refletir no state
            cart["items"] = []
            cart["subtotal"] = 0.0

            # Monta resumo para mostrar ao cliente
            lines = [
                f"✅ *Pedido confirmado!* Número: #{order_id[:8]}",
                "",
                "*Itens:*",
            ]
            for i in items_snapshot:
                lines.append(f"  • {i['qty']}x {i['name']} — R$ {i['price']:.2f}")
            lines.append("")
            lines.append(f"Subtotal: R$ {subtotal:.2f}")
            if discount > 0:
                lines.append(f"Desconto ({_VALID_PAYMENT[forma]}): -R$ {discount:.2f}")
            lines.append(f"*Total: R$ {total:.2f}*")
            lines.append(f"Pagamento: {_VALID_PAYMENT[forma]}")
            lines.append("")
            lines.append("Um atendente vai confirmar e te avisar quando estiver pronto.")
            return "\n".join(lines)

        except Exception as exc:
            log.error("tool.finalizar_pedido.error", phone=phone, exc=str(exc))
            return (
                "Tive um problema técnico ao registrar o pedido. "
                "Pode aguardar um instante e tentar de novo?"
            )

    return finalizar_pedido

