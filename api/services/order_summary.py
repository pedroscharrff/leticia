"""
services/order_summary.py

Resumo do pedido enviado ao cliente logo após a transferência para o
atendente humano (handoff). Espelha o padrão de `_send_pre_handoff_offers`:

  • Gated por capability `sales.order_summary_after_handoff` (default OFF).
  • Template 100% customizável por tenant (header / linha de item / total /
    rodapé) via config da capability.
  • NUNCA levanta exceção — o handoff já saiu, nada aqui pode quebrar o fluxo.
  • Sem itens no carrinho → no-op silencioso.

Fonte do dado: o `cart` do ConversationState, cujo formato é:
    {"items": [{"nome": str, "preco": float, "quantidade": int}],
     "subtotal": float}

Config esperada (capability_catalog.default_config / override do tenant):
    {
      "header_text":  "📋 *Resumo do seu pedido:*",
      "item_template": "• {quantidade}x {nome} — {preco_total}",
      "show_total":   true,
      "total_label":  "*Total*",
      "footer_text":  "Um atendente vai confirmar disponibilidade e finalizar. 😊"
    }

Placeholders disponíveis em `item_template` (todos opcionais — placeholder
desconhecido nunca quebra, vira string vazia):
    {nome}         — nome do produto
    {quantidade}   — quantidade
    {preco_unit}   — preço unitário formatado (R$ x,xx)
    {preco_total}  — preço unitário × quantidade formatado
    {preco}        — alias de {preco_unit}

Blocos DETERMINÍSTICOS de pagamento e entrega (não dependem do LLM):
  • Forma de pagamento — sai de `cart["payment"]` (gravado por
    `finalizar_pedido` → `cart.last_order.payment`). Renderizada como
    "{payment_label}: {payment}" SÓ quando há valor (omitida em
    pré-atendimento, onde não há forma de pagamento real).
  • Endereço de entrega — sai de `cart["address"]` (montado deterministicamente
    pelo worker a partir do cadastro do cliente em `_cart_for_summary`).
    Renderizada como "{address_label}: {address}" SÓ quando há endereço.
Ambos são "quando houver": string vazia → linha omitida, sem ruído.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger()

CAPABILITY_KEY = "sales.order_summary_after_handoff"

# Defaults usados quando a config do tenant não traz o campo (defesa extra —
# o catálogo já tem default_config, mas isto garante robustez se vier vazio).
_DEFAULTS = {
    "header_text":   "📋 *Resumo do seu pedido:*",
    "item_template": "• {quantidade}x {nome} — {preco_total}",
    "show_total":    True,
    "total_label":   "*Total*",
    "footer_text":   "",
    # Linhas determinísticas opcionais (renderizadas só quando há dado).
    "show_payment":  True,
    "payment_label": "*Pagamento*",
    "show_address":  True,
    "address_label": "*Entrega*",
}


class _SafeDict(dict):
    """dict que devolve '' para chaves ausentes — evita KeyError no .format()."""
    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


def _fmt_brl(v: float) -> str:
    try:
        return f"R$ {float(v):.2f}".replace(".", ",")
    except (TypeError, ValueError):
        return "R$ 0,00"


def _coerce_items(cart: dict | None) -> list[dict]:
    if not cart or not isinstance(cart, dict):
        return []
    items = cart.get("items")
    if not isinstance(items, list):
        return []
    clean: list[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        nome = (it.get("nome") or "").strip()
        if not nome:
            continue
        clean.append(it)
    return clean


def _all_prices_zero(items: list[dict]) -> bool:
    """True quando nenhum item tem preço — caso típico de pré-atendimento,
    onde `anotar_pedido_balcao` registra só nome/qty (preço fica no balcão).
    """
    for it in items:
        try:
            if float(it.get("preco") or 0) > 0:
                return False
        except (TypeError, ValueError):
            continue
    return True


def build_summary_text(cart: dict | None, config: dict | None) -> str | None:
    """Monta o texto do resumo a partir do carrinho + template.

    Retorna None quando não há itens — o caller deve então não enviar nada.
    Função PURA: sem I/O, fácil de testar.

    Quando TODOS os preços vêm zerados (pré-atendimento: tool de balcão
    não tem catálogo), troca automaticamente o item_template por um sem
    `{preco_*}` e suprime a linha do Total — mostrar "R$ 0,00" ficaria
    pior do que omitir. O operador não precisa configurar nada extra.
    """
    items = _coerce_items(cart)
    if not items:
        return None

    cfg = {**_DEFAULTS, **(config or {})}
    item_tpl = cfg.get("item_template") or _DEFAULTS["item_template"]

    no_prices = _all_prices_zero(items)
    if no_prices:
        # Esconde qualquer placeholder de preço sem precisar reescrever a
        # config do tenant. Substitui por "" antes do .format_map.
        item_tpl = (
            item_tpl
            .replace("— {preco_total}", "")
            .replace("- {preco_total}", "")
            .replace("({preco_total})", "")
            .replace(" {preco_total}", "")
            .replace("{preco_total}", "")
            .replace("— {preco_unit}", "")
            .replace("- {preco_unit}", "")
            .replace("({preco_unit})", "")
            .replace(" {preco_unit}", "")
            .replace("{preco_unit}", "")
            .replace("— {preco}", "")
            .replace("- {preco}", "")
            .replace("({preco})", "")
            .replace(" {preco}", "")
            .replace("{preco}", "")
            .rstrip(" -—")
        )

    lines: list[str] = []
    header = (cfg.get("header_text") or "").strip()
    if header:
        lines.append(header)

    computed_total = 0.0
    for it in items:
        nome = (it.get("nome") or "").strip()
        qtd = it.get("quantidade") or 1
        preco_unit = it.get("preco") or 0.0
        try:
            preco_total_val = float(preco_unit) * int(qtd)
        except (TypeError, ValueError):
            preco_total_val = 0.0
        computed_total += preco_total_val

        ctx = _SafeDict(
            nome=nome,
            quantidade=qtd,
            preco_unit=_fmt_brl(preco_unit),
            preco=_fmt_brl(preco_unit),
            preco_total=_fmt_brl(preco_total_val),
        )
        try:
            line = item_tpl.format_map(ctx)
        except Exception:  # noqa: BLE001 — template inválido nunca derruba
            line = f"• {qtd}x {nome}"
        lines.append(line)

    # Sem preço em nenhum item → omite o Total mesmo com show_total=true.
    # "Total: R$ 0,00" é pior do que nada nesse contexto.
    if cfg.get("show_total") and not no_prices:
        # Usa o subtotal do carrinho se presente; senão o total calculado.
        subtotal = cart.get("subtotal") if isinstance(cart, dict) else None
        total_val = subtotal if isinstance(subtotal, (int, float)) and subtotal else computed_total
        total_label = (cfg.get("total_label") or "Total").strip()
        lines.append(f"{total_label}: {_fmt_brl(total_val)}")

    # ── Forma de pagamento (DETERMINÍSTICA — "quando houver") ────────────────
    # Vem de `cart["payment"]` (finalizar_pedido grava em last_order.payment).
    # Pré-atendimento não tem forma real → string vazia → linha omitida.
    payment = (cart.get("payment") or "").strip() if isinstance(cart, dict) else ""
    if cfg.get("show_payment", True) and payment:
        pay_label = (cfg.get("payment_label") or "Pagamento").strip()
        lines.append(f"{pay_label}: {payment}")

    # ── Endereço de entrega (DETERMINÍSTICO — "quando houver") ───────────────
    # Vem de `cart["address"]`, montado pelo worker a partir do cadastro do
    # cliente. Sem endereço (retirada / cadastro vazio) → linha omitida.
    address = (cart.get("address") or "").strip() if isinstance(cart, dict) else ""
    if cfg.get("show_address", True) and address:
        addr_label = (cfg.get("address_label") or "Entrega").strip()
        lines.append(f"{addr_label}: {address}")

    footer = (cfg.get("footer_text") or "").strip()
    if footer:
        lines.append(footer)

    return "\n".join(lines)


async def send_order_summary(
    tenant_id: str,
    *,
    phone: str,
    cart: dict | None,
    text_sender,  # async (text: str) -> None
) -> None:
    """Envia o resumo do pedido (se a capability estiver ON e houver itens).

    NUNCA levanta exceção. Espelha o contrato de `_send_pre_handoff_offers`.
    """
    try:
        from services import capabilities as cap_svc

        if not await cap_svc.is_enabled(tenant_id, CAPABILITY_KEY):
            return

        config = await cap_svc.get_config(tenant_id, CAPABILITY_KEY) or {}
        text = build_summary_text(cart, config)
        if not text:
            log.info("order_summary.skipped_empty_cart", tenant=tenant_id)
            return

        await text_sender(text)
        log.info("order_summary.sent",
                 tenant=tenant_id, phone_prefix=str(phone)[:4],
                 items=len(_coerce_items(cart)))
    except Exception as exc:  # noqa: BLE001
        log.warning("order_summary.failed", tenant=tenant_id, exc=str(exc))
