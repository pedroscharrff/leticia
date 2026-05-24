"""
Tools opcionais do vendedor — só são vinculadas quando a capability
correspondente está habilitada para o tenant:

  • recomendar_complementos   → capability `sales.cross_sell`
  • calcular_frete            → capability `delivery.shipping_by_cep`
  • gerar_link_pix            → capability `payments.pix_asaas`

Tools de memória do cliente (sempre disponíveis quando a capability
`attendance.customer_memory` está ativa):

  • registrar_alergia
  • registrar_medicamento_continuo
  • registrar_preferencia
"""
from __future__ import annotations

import json
import re

import structlog
from langchain_core.tools import tool

log = structlog.get_logger()


# ── Cross-sell ──────────────────────────────────────────────────────────────

def make_cross_sell_tool(schema_name: str, *, min_weight: float, max_suggestions: int,
                        customer_allergies: list[str] | None = None):
    """Tool de recomendação de complementos.

    Args do factory:
        schema_name:         schema do tenant.
        min_weight:          confiança mínima (0-1) — vem do config da capability.
        max_suggestions:     quantos sugerir por chamada (1-3).
        customer_allergies:  alergias do cliente, para filtrar substâncias.
    """
    allergies_norm = [a.lower().strip() for a in (customer_allergies or [])]

    @tool
    async def recomendar_complementos(produto: str) -> str:
        """
        Sugere complementos para um produto que o cliente acabou de pedir.
        Use APÓS confirmar interesse num item para oferecer 1 complemento
        relevante. Respeita alergias declaradas pelo cliente.

        Args:
            produto: Nome ou parte do nome do produto principal que o
                     cliente pediu (ex.: "Dipirona", "Soro fisiológico").
        """
        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")
                # Acha o product_id do produto principal
                prod = await conn.fetchrow(
                    """
                    SELECT id FROM products
                     WHERE active = TRUE
                       AND (name ILIKE $1 OR principio_ativo ILIKE $1)
                     ORDER BY name LIMIT 1
                    """,
                    f"%{produto}%",
                )
                if not prod:
                    return f"Produto '{produto}' não encontrado para sugerir complementos."

                rows = await conn.fetch(
                    """
                    SELECT p.name, p.price, p.principio_ativo, pr.weight, pr.relation_type
                      FROM product_relations pr
                      JOIN products p ON p.id = pr.related_product_id
                     WHERE pr.product_id    = $1
                       AND pr.relation_type = 'complementar'
                       AND pr.weight        >= $2
                       AND p.active         = TRUE
                       AND p.stock_qty      > 0
                     ORDER BY pr.weight DESC
                     LIMIT $3
                    """,
                    prod["id"], float(min_weight), int(max_suggestions),
                )

            if not rows:
                return "Sem sugestões de complemento relevantes para este item."

            # Filtra contra alergias do cliente
            safe_rows = []
            for r in rows:
                pa = (r.get("principio_ativo") or "").lower()
                name_low = (r.get("name") or "").lower()
                if any(a and (a in pa or a in name_low) for a in allergies_norm):
                    log.info("cross_sell.skipped_allergy", produto=r["name"])
                    continue
                safe_rows.append(r)

            if not safe_rows:
                return ("Sem complementos seguros para este cliente "
                        "(filtrados por alergias declaradas).")

            lines = [
                f"• {r['name']} — R$ {float(r['price']):.2f} "
                f"(relevância {int(float(r['weight']) * 100)}%)"
                for r in safe_rows
            ]
            return ("Complementos sugeridos (escolha no máximo 1 para oferecer):\n"
                    + "\n".join(lines))

        except Exception as exc:
            log.warning("tool.recomendar_complementos.error",
                        produto=produto, exc=str(exc))
            return "Não consegui consultar sugestões de complemento agora."

    return recomendar_complementos


# ── Frete por CEP ───────────────────────────────────────────────────────────

_CEP_DIGITS = re.compile(r"\d")


def _cep_to_int(cep: str) -> int | None:
    digits = "".join(_CEP_DIGITS.findall(cep or ""))
    if len(digits) != 8:
        return None
    try:
        return int(digits)
    except ValueError:
        return None


def make_shipping_tool(tenant_id: str, *, default_eta_days: int, free_above: float):
    """Tool de cálculo de frete por CEP.

    Args do factory:
        tenant_id:        id do tenant para consultar tenant_shipping_rules.
        default_eta_days: prazo retornado quando nenhum range bate.
        free_above:       frete grátis acima de R$ X (0 = desativado).
    """

    @tool
    async def calcular_frete(cep: str, subtotal: float) -> str:
        """
        Calcula o frete para o CEP do cliente baseado nas regras cadastradas.
        Use SEMPRE que o cliente fornecer o CEP, antes de fechar o pedido.

        Args:
            cep:      CEP do cliente (com ou sem hífen).
            subtotal: Subtotal atual do carrinho em reais.
        """
        cep_int = _cep_to_int(cep)
        if cep_int is None:
            return f"CEP '{cep}' inválido. Peça ao cliente para reenviar."

        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                rows = await conn.fetch(
                    """
                    SELECT label, cep_start, cep_end, valor, prazo_dias, gratis_acima
                      FROM public.tenant_shipping_rules
                     WHERE tenant_id = $1 AND active = TRUE
                     ORDER BY sort_order, valor
                    """,
                    tenant_id,
                )
        except Exception as exc:
            log.warning("tool.calcular_frete.db_error", exc=str(exc))
            return "Não consegui calcular o frete agora — vou pedir para o atendente confirmar."

        match = None
        for r in rows:
            start = _cep_to_int(r["cep_start"])
            end   = _cep_to_int(r["cep_end"])
            if start is None or end is None:
                continue
            if start <= cep_int <= end:
                match = r
                break

        if not match:
            return (
                f"Não encontrei regra de entrega para o CEP {cep}. "
                f"Prazo estimado: {default_eta_days} dias úteis. "
                f"Confirmaremos o valor exato com o atendente."
            )

        valor = float(match["valor"] or 0)
        prazo = int(match["prazo_dias"] or default_eta_days)
        gratis_rule = match.get("gratis_acima")
        gratis_threshold = float(gratis_rule) if gratis_rule is not None else None
        # `free_above` global do tenant ganha de None local; threshold local sobrescreve.
        effective_free = gratis_threshold if gratis_threshold not in (None, 0) else (
            free_above if free_above > 0 else None
        )

        sub = float(subtotal or 0)
        if effective_free is not None and sub >= effective_free:
            return (f"Entrega GRÁTIS para {match['label']} (CEP {cep}) — "
                    f"prazo: {prazo} dias úteis. (Frete grátis acima de R$ {effective_free:.2f}.)")

        return (f"Frete para {match['label']} (CEP {cep}): "
                f"R$ {valor:.2f} — prazo: {prazo} dias úteis. "
                f"Novo total com frete: R$ {sub + valor:.2f}.")

    return calcular_frete


# ── PIX no chat (Asaas) ─────────────────────────────────────────────────────

def make_pix_tool(tenant_id: str, schema_name: str, phone: str,
                  customer: dict, *, expires_minutes: int = 60):
    """Tool de geração de cobrança PIX para um pedido recém-criado.

    Args do factory:
        tenant_id, schema_name: contexto do tenant.
        phone:                 telefone do cliente (key no Asaas).
        customer:              dict com name/doc/email do cliente.
        expires_minutes:       validade da cobrança (vem da config da capability).
    """

    @tool
    async def gerar_link_pix(numero_pedido: str, valor_total: float) -> str:
        """
        Gera um link PIX (com QR code) para um pedido já criado. Use APENAS
        após `finalizar_pedido` ter retornado um número de pedido com sucesso
        e o cliente tiver aceito pagar via PIX agora.

        Args:
            numero_pedido: ID do pedido retornado por finalizar_pedido.
            valor_total:   Valor total a cobrar (subtotal + frete).
        """
        try:
            from services.payments_asaas import create_pix_charge

            result = await create_pix_charge(
                tenant_id,
                order_id=numero_pedido,
                schema_name=schema_name,
                phone=phone,
                name=customer.get("name"),
                cpf_cnpj=customer.get("doc"),
                email=customer.get("email"),
                amount=float(valor_total or 0),
                description=f"Pedido #{str(numero_pedido)[:8]}",
                expires_minutes=int(expires_minutes),
            )
        except Exception as exc:
            log.warning("tool.gerar_link_pix.error", exc=str(exc))
            return ("Tive uma dificuldade para gerar o PIX. "
                    "Avisarei o atendente para confirmar o pagamento manualmente.")

        if "error" in result:
            return result["error"]

        # Mensagem composta para o LLM repassar ao cliente. Apresentamos o
        # copia-cola em bloco de código para facilitar copiar no app do banco.
        lines = [
            f"PIX gerado — pedido #{str(numero_pedido)[:8]} — valor R$ {result['amount']:.2f}.",
            "",
            "Cole no seu app do banco (Pix → Copia e Cola):",
            "```",
            result["qr_code"] or "(QR não disponível)",
            "```",
        ]
        if result.get("payment_url"):
            lines.append(f"Ou abra a página de pagamento: {result['payment_url']}")
        lines.append("Validade: 1 hora. Assim que recebermos eu te aviso por aqui!")
        return "\n".join(lines)

    return gerar_link_pix


# ── Memória de cliente ──────────────────────────────────────────────────────

def make_customer_memory_tools(schema_name: str, phone: str, customer: dict):
    """Cria tools que persistem memória do cliente (alergias, contínuos, prefs).

    O dicionário `customer` é atualizado in-place para o resto da conversa
    refletir imediatamente.
    """

    @tool
    async def registrar_alergia(principio_ativo_ou_medicamento: str) -> str:
        """
        Registra uma alergia declarada pelo cliente. Use SEMPRE que o cliente
        mencionar que é alérgico a algum medicamento ou substância
        (ex.: "sou alérgico a dipirona", "tenho alergia a penicilina").

        Args:
            principio_ativo_ou_medicamento: nome curto da substância/medicamento.
        """
        sub = (principio_ativo_ou_medicamento or "").strip().lower()
        if not sub or not phone:
            return "Não consegui registrar essa alergia."
        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")
                # array_append idempotente
                await conn.execute(
                    """
                    UPDATE customers
                       SET allergies = (
                         SELECT ARRAY(SELECT DISTINCT unnest(COALESCE(allergies, '{}') || ARRAY[$2]))
                       ),
                           updated_at = NOW()
                     WHERE phone = $1
                    """,
                    phone, sub,
                )
        except Exception as exc:
            log.warning("tool.registrar_alergia.error", exc=str(exc))
            return "Não consegui registrar a alergia agora."

        allergies = list(customer.get("allergies") or [])
        if sub not in allergies:
            allergies.append(sub)
        customer["allergies"] = allergies
        return f"Anotado: cliente é alérgico a {sub}. Vou evitar recomendar isso daqui em diante."

    @tool
    async def registrar_medicamento_continuo(
        medicamento: str,
        frequencia_dias: int,
    ) -> str:
        """
        Registra um medicamento de uso contínuo do cliente. Use quando o
        cliente mencionar que toma algo continuamente (ex.: "tomo Losartana
        50mg todos os dias", "uso anticoncepcional há 2 anos").

        Args:
            medicamento:     Nome do medicamento + dose (ex.: "Losartana 50mg").
            frequencia_dias: A cada quantos dias termina uma cartela/embalagem
                             (ex.: 30 para uma cartela mensal).
        """
        if not medicamento or not phone:
            return "Não consegui registrar o medicamento."
        try:
            from datetime import datetime, timezone
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")
                row = await conn.fetchrow(
                    "SELECT continuous_meds FROM customers WHERE phone = $1",
                    phone,
                )
                current = []
                if row and row["continuous_meds"]:
                    raw = row["continuous_meds"]
                    if isinstance(raw, str):
                        try: current = json.loads(raw)
                        except json.JSONDecodeError: current = []
                    elif isinstance(raw, list):
                        current = list(raw)

                med_norm = medicamento.strip()
                # Atualiza se já existe entrada com mesmo nome (case-insensitive)
                found = False
                for m in current:
                    if isinstance(m, dict) and (m.get("name", "").lower() == med_norm.lower()):
                        m["frequency_days"] = int(frequencia_dias)
                        m["last_refill_at"] = datetime.now(timezone.utc).date().isoformat()
                        found = True
                        break
                if not found:
                    current.append({
                        "name":           med_norm,
                        "frequency_days": int(frequencia_dias),
                        "last_refill_at": datetime.now(timezone.utc).date().isoformat(),
                    })

                await conn.execute(
                    "UPDATE customers SET continuous_meds = $2::jsonb, updated_at = NOW() "
                    "WHERE phone = $1",
                    phone, json.dumps(current),
                )
        except Exception as exc:
            log.warning("tool.registrar_med_continuo.error", exc=str(exc))
            return "Não consegui salvar o medicamento contínuo agora."

        customer["continuous_meds"] = current
        return (f"Anotado: cliente usa {medicamento} a cada {frequencia_dias} dias. "
                f"Vou lembrar disso e poder avisar quando estiver acabando.")

    @tool
    async def registrar_preferencia(chave: str, valor: str) -> str:
        """
        Registra uma preferência do cliente (ex.: prefere genérico, canal de
        contato, tom). Use quando o cliente expressar uma preferência explícita.

        Args:
            chave: identificador curto (ex.: "prefere_generico", "canal_pref").
            valor: valor (ex.: "true", "false", "whatsapp", "informal").
        """
        if not chave or not phone:
            return "Não consegui registrar a preferência."
        try:
            from db.postgres import get_db_conn
            async with get_db_conn() as conn:
                await conn.execute(f"SET search_path = {schema_name}, public")
                row = await conn.fetchrow(
                    "SELECT preferences FROM customers WHERE phone = $1", phone,
                )
                prefs = {}
                if row and row["preferences"]:
                    raw = row["preferences"]
                    if isinstance(raw, str):
                        try: prefs = json.loads(raw)
                        except json.JSONDecodeError: prefs = {}
                    elif isinstance(raw, dict):
                        prefs = dict(raw)

                # Normaliza true/false
                v_norm: object = valor
                if isinstance(valor, str):
                    low = valor.strip().lower()
                    if low in {"true", "sim", "yes"}:
                        v_norm = True
                    elif low in {"false", "nao", "não", "no"}:
                        v_norm = False
                prefs[chave.strip()] = v_norm

                await conn.execute(
                    "UPDATE customers SET preferences = $2::jsonb, updated_at = NOW() "
                    "WHERE phone = $1",
                    phone, json.dumps(prefs),
                )
        except Exception as exc:
            log.warning("tool.registrar_preferencia.error", exc=str(exc))
            return "Não consegui salvar a preferência agora."

        customer["preferences"] = prefs
        return f"Preferência registrada: {chave} = {valor}."

    return [registrar_alergia, registrar_medicamento_continuo, registrar_preferencia]
