"""
agents/tools/balcao.py

Tool: anotar_pedido_balcao

Cria um rascunho de pedido com status 'aguardando_balcao' sem validar
estoque ou preços. Usado pelo agente vendedor em modo pré-atendimento
(capability sales.stock_check desligada).

O pedido fica visível no portal em Vendas › Pedidos com badge distinto.
Após a tool retornar com sucesso, o node vendedor sinaliza escalate=True
para que o celery worker acione a transferência ao atendente humano.
"""
from __future__ import annotations

import uuid

import structlog
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from db.postgres import get_db_conn

log = structlog.get_logger()


# ── Pydantic schemas ─────────────────────────────────────────────────────────

class AnotarPedidoInput(BaseModel):
    itens: list[dict] = Field(
        description=(
            'Lista de itens pedidos pelo cliente. '
            'Cada item: {"name": "Dipirona 500mg", "qty": 2}. '
            "Use exatamente o nome que o cliente informou — sem abreviar."
        )
    )
    observacoes: str | None = Field(
        default=None,
        description=(
            "Informações extras: urgência, receita médica necessária, "
            "preferência de genérico, etc."
        ),
    )


class RegistrarItensInput(BaseModel):
    itens: list[dict] = Field(
        description=(
            'Lista ATUAL e completa dos itens que o cliente quer até agora. '
            'Cada item: {"name": "Dipirona 500mg", "qty": 2}. '
            "Use exatamente o nome que o cliente informou — sem abreviar. "
            "Informe a lista inteira a cada chamada (ela SUBSTITUI a anterior)."
        )
    )


# ── Core logic ───────────────────────────────────────────────────────────────

async def _anotar_pedido_balcao(
    schema_name: str,
    phone: str,
    customer: dict,
    cart: dict,
    itens: list[dict],
    observacoes: str | None,
) -> str:
    """
    Persiste o pedido sem validação de estoque/preço e retorna uma mensagem
    de confirmação para o LLM repassar ao cliente.

    Muta `cart` in-place (mesma ref do AgentState) para que o worker, ao
    despachar o handoff, consiga montar o resumo via `send_order_summary`
    — espelha o que `finalize_order_tool` faz no modo ERP.
    """
    if not itens:
        return (
            "Nenhum item foi informado. "
            "Pergunte ao cliente o que deseja antes de anotar o pedido."
        )

    # Normaliza a lista: garante que name/qty existam
    items_clean: list[dict] = []
    for raw in itens:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("produto") or "").strip()
        qty  = int(raw.get("qty") or raw.get("quantidade") or 1)
        if name:
            items_clean.append({
                "name": name,
                "qty":  max(1, qty),
                "price": 0.0,
                "prescription_required": False,
            })

    if not items_clean:
        return "Não entendi os itens informados. Tente novamente com nome e quantidade."

    order_id    = str(uuid.uuid4())
    customer_id = customer.get("id")  # pode ser None se cliente ainda não tem cadastro

    # Monta observações completas que ficam no campo notes do pedido
    items_summary = "\n".join(
        f"  • {i['qty']}x {i['name']}" for i in items_clean
    )
    full_notes = f"[Pré-atendimento via WhatsApp]\n{items_summary}"
    if observacoes and observacoes.strip():
        full_notes += f"\n\nObservações: {observacoes.strip()}"

    try:
        async with get_db_conn() as conn:
            await conn.execute(
                f"SET search_path = {schema_name}, public"
            )
            await conn.execute(
                """
                INSERT INTO orders (
                    id,
                    customer_id,
                    session_key,
                    status,
                    items,
                    subtotal,
                    discount,
                    total,
                    notes,
                    requires_prescription,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3,
                    'aguardando_balcao',
                    $4,
                    0, 0, 0,
                    $5,
                    FALSE,
                    NOW(), NOW()
                )
                """,
                order_id,
                customer_id,
                phone,
                items_clean,
                full_notes,
            )
        log.info(
            "balcao.pedido_anotado",
            order_id=order_id[:8],
            items=len(items_clean),
            schema=schema_name,
        )
    except Exception as exc:  # noqa: BLE001
        log.error("balcao.pedido_failed", exc=str(exc), schema=schema_name)
        return (
            "Tive um problema ao registrar o pedido agora. "
            "Pode tentar novamente ou chamar o atendente diretamente?"
        )

    # Popula o cart in-place para o worker conseguir montar o resumo após o
    # handoff (send_order_summary lê de cart.last_order > cart.items).
    # Sem preço em pré-atendimento — o template trata o caso preco=0.
    try:
        cart["items"] = [
            {"name": i["name"], "qty": i["qty"], "price": 0.0}
            for i in items_clean
        ]
        cart["subtotal"] = 0.0
        cart["just_finalized"] = True
        cart["last_order"] = {
            "id":       order_id,
            "items":    list(cart["items"]),
            "subtotal": 0.0,
            "discount": 0.0,
            "total":    0.0,
            "payment":  "balcao",
            "notes":    full_notes,
        }
    except Exception as _exc:  # noqa: BLE001
        log.warning("balcao.cart_mutation_failed", exc=str(_exc))

    # Mensagem de confirmação para o LLM incluir na resposta ao cliente
    items_list = "\n".join(f"• {i['qty']}x {i['name']}" for i in items_clean)
    return (
        f"PEDIDO_ANOTADO:OK\n"
        f"order_id:{order_id[:8]}\n"
        f"Itens:\n{items_list}"
    )


# ── Rascunho recuperável (pré-atendimento) ───────────────────────────────────

def _normalize_itens(itens: list[dict]) -> list[dict]:
    """Normaliza itens crus do LLM: garante name/qty, descarta vazios."""
    out: list[dict] = []
    for raw in itens or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or raw.get("produto") or "").strip()
        qty  = int(raw.get("qty") or raw.get("quantidade") or 1)
        if name:
            out.append({"name": name, "qty": max(1, qty), "price": 0.0})
    return out


def _registrar_itens_interesse(cart: dict, itens: list[dict]) -> str:
    """
    Persiste a lista de interesse do cliente no `cart` em memória SEM finalizar.

    Espelha `adicionar_ao_carrinho` do modo normal: muta `cart` in-place (mesma
    ref do AgentState) para que `save_context` grave uma linha em `{schema}.cart`
    com `items > 0`. Isso é o que torna o carrinho de PRÉ-ATENDIMENTO recuperável
    pelo job `recover_abandoned_carts` quando o cliente some antes de confirmar.

    NÃO seta `just_finalized`, NÃO cria `last_order`, NÃO escreve em `orders` —
    isso é exclusivo de `anotar_pedido_balcao` (fechamento + handoff).
    """
    items_clean = _normalize_itens(itens)
    if not items_clean:
        return (
            "Nenhum item válido informado. "
            "Pergunte ao cliente o que deseja antes de registrar."
        )

    # Dedup determinístico por turno — o LLM às vezes re-chama a tool com os
    # MESMOS args no mesmo turno. Sem isso, gravamos a mesma lista várias vezes
    # à toa. Reset entre turnos é automático (load_context reconstrói o cart do
    # banco e descarta esta key). Mesmo padrão de `adicionar_ao_carrinho`.
    sig = "|".join(f"{i['name'].lower()}:{i['qty']}" for i in items_clean)
    calls_seen = cart.setdefault("_calls_this_turn", [])
    if sig in calls_seen:
        return (
            "⚠️ Essa lista já foi registrada agora mesmo. "
            "NÃO chame esta tool de novo com os mesmos itens — só atualize "
            "quando o cliente mudar a lista."
        )
    calls_seen.append(sig)

    # SUBSTITUI a lista de interesse (a tool recebe sempre a lista completa).
    cart["items"]    = items_clean
    cart["subtotal"] = 0.0

    log.info("balcao.itens_interesse_registrados", items=len(items_clean))
    items_list = "\n".join(f"• {i['qty']}x {i['name']}" for i in items_clean)
    return (
        "ITENS_REGISTRADOS:OK (rascunho salvo — pedido NÃO finalizado)\n"
        f"Lista atual:\n{items_list}"
    )


# ── Factory ──────────────────────────────────────────────────────────────────

def make_registrar_itens_interesse_tool(
    schema_name: str,
    cart: dict,
) -> StructuredTool:
    """
    Tool de RASCUNHO para o vendedor em pré-atendimento.

    Salva a lista de itens que o cliente quer enquanto a coleta acontece, sem
    finalizar nem transferir. `cart` é mutado in-place (mesma ref do AgentState)
    e persistido por `save_context` — é o que permite recuperar o carrinho se o
    cliente sumir antes de confirmar. Distinta de `anotar_pedido_balcao`, que é
    terminal (cria order + handoff).
    """

    async def _run(itens: list[dict]) -> str:
        return _registrar_itens_interesse(cart, itens)

    return StructuredTool.from_function(
        coroutine=_run,
        name="registrar_itens_interesse",
        description=(
            "Salva/atualiza a lista de itens que o cliente quer ENQUANTO você "
            "coleta o pedido. NÃO finaliza e NÃO transfere ao balcão — serve só "
            "para registrar o interesse (e permitir recuperação se o cliente "
            "sumir). Chame sempre que o cliente acrescentar/mudar um item, "
            "passando a lista ATUAL completa. Para FECHAR o pedido, use "
            "`anotar_pedido_balcao` (essa sim é a tool terminal)."
        ),
        args_schema=RegistrarItensInput,
    )


def make_anotar_pedido_balcao_tool(
    schema_name: str,
    phone: str,
    customer: dict,
    cart: dict,
) -> StructuredTool:
    """
    Retorna a tool pronta para ser vinculada ao LLM do vendedor.

    O prefixo 'PEDIDO_ANOTADO:OK' no resultado é usado pelo vendedor_node
    para detectar que a tool rodou com sucesso e sinalizar escalate=True.

    `cart` é mutado in-place (mesma ref do AgentState) — sem isso o resumo
    do pedido no handoff sai vazio porque o cart fica sempre limpo em
    pré-atendimento.
    """

    async def _run(itens: list[dict], observacoes: str | None = None) -> str:
        return await _anotar_pedido_balcao(
            schema_name, phone, customer, cart, itens, observacoes
        )

    return StructuredTool.from_function(
        coroutine=_run,
        name="anotar_pedido_balcao",
        description=(
            "Registra a lista de itens pedidos pelo cliente para ser "
            "finalizado pelo atendente no balcão. "
            "USE APENAS após confirmar com o cliente que a lista está completa. "
            "Informe todos os itens de uma vez — não chame a tool por item."
        ),
        args_schema=AnotarPedidoInput,
    )
