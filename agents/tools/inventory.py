"""
Tool: buscar produto no catálogo / estoque do tenant.
"""
from __future__ import annotations

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
                    SELECT name, price, stock_qty, unit, active
                    FROM inventory
                    WHERE active = TRUE
                      AND (
                          name ILIKE $1
                          OR description ILIKE $1
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
                    "SELECT name, price FROM inventory WHERE name ILIKE $1 AND active = TRUE LIMIT 1",
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
