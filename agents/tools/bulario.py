"""
Tool: consultar_bula

Permite ao agente farmacêutico consultar informações regulatórias da ANVISA
sobre um medicamento: nome comercial, princípio ativo, fabricante e classes
terapêuticas. Diferente de `buscar_produto` (estoque/preço do tenant), esta
consulta é GLOBAL — a base é compartilhada entre todos os tenants.

Estratégia da implementação está em services/bulario_repo.py:
  • cache de query → local fuzzy → ANVISA (com upsert + top-3 details)
"""
from __future__ import annotations

import structlog
from langchain_core.tools import tool

log = structlog.get_logger()


def _format_row(r: dict) -> str:
    """Linha curta legível para o agente."""
    nome = r.get("nome_produto") or "?"
    pa   = r.get("principio_ativo") or "—"
    raz  = r.get("razao_social") or "—"
    classes = r.get("classes_terapeuticas") or []
    cls_txt = ", ".join(classes[:2]) if classes else "—"
    return f"• {nome} | princípio ativo: {pa} | fabricante: {raz} | classe: {cls_txt}"


def make_consultar_bula_tool(not_found_message: str | None = None):
    """
    Factory — retorna a tool. Sem closure de tenant porque a base é global.

    Args:
        not_found_message: quando fornecido, o "nada encontrado" passa a
            instruir o agente a NÃO inventar dosagem/apresentação e a perguntar
            ao cliente usando exatamente esta frase (guard-rail de pré-atendimento,
            configurável por tenant via `sales.pharmacist_validation`). Quando
            None, mantém o comportamento histórico (mensagem genérica).

    Existe como factory para consistência com as outras tools do projeto
    (inventory, customer, balcao) e para facilitar mock em testes.
    """
    @tool
    async def consultar_bula(termo: str) -> str:
        """
        Consulta a base regulatória da ANVISA (bulário) sobre um medicamento.
        Retorna nome, princípio ativo, fabricante e classe terapêutica dos
        produtos que batem com o termo. Use SEMPRE que o cliente perguntar
        sobre composição, princípio ativo, fabricante ou para confirmar
        identidade de um medicamento. Não retorna preço nem disponibilidade
        local — para isso, use `buscar_produto`.

        Args:
            termo: nome do medicamento ou princípio ativo. Ex: "dipirona",
                   "paracetamol 750", "losartana".
        """
        try:
            from services.bulario_repo import get_or_fetch
            rows = await get_or_fetch(termo, limit=5)
        except Exception as exc:  # noqa: BLE001
            log.warning("tool.consultar_bula.error", termo=termo, exc=str(exc))
            return (
                "Não consegui consultar o bulário da ANVISA agora. "
                "Use seu conhecimento geral com cautela e sugira que o cliente "
                "confira a bula impressa."
            )

        if not rows:
            # Guard-rail: medicamento não está no bulário da ANVISA. O agente
            # NÃO pode inventar apresentação/dosagem nem afirmar disponibilidade.
            # Quando o tenant configurou a validação farmacêutica, devolvemos a
            # frase EXATA que o agente deve dizer ao cliente para coletar a
            # dosagem/apresentação desejada.
            if not_found_message:
                log.info("tool.consultar_bula.not_found_guardrail", termo=termo)
                return (
                    f"BULÁRIO: nenhum registro para '{termo}' na ANVISA. NÃO invente "
                    f"dosagem, apresentação ou marca, e NÃO diga que a farmácia tem "
                    f"ou não tem. Responda ao cliente EXATAMENTE com esta frase "
                    f"(adapte só o nome do remédio se fizer sentido): "
                    f"\"{not_found_message}\""
                )
            return f"Nenhum medicamento encontrado no bulário para '{termo}'."

        lines = [f"Bulário ANVISA — '{termo}':"]
        lines.extend(_format_row(r) for r in rows[:5])
        return "\n".join(lines)

    return consultar_bula


# Slugs aceitos pelo argumento `secao` da consulta de bula. Mantém em sincronia
# com bula_extractor._SECTION_PATTERNS.
_SECAO_LABELS = {
    "indicacoes":       "Indicações",
    "mecanismo":        "Como funciona",
    "contraindicacoes": "Contraindicações",
    "precaucoes":       "Precauções / advertências",
    "interacoes":       "Interações medicamentosas",
    "armazenamento":    "Armazenamento",
    "posologia":        "Posologia / como usar",
    "esquecimento":     "Se esqueceu de tomar",
    "reacoes_adversas": "Reações adversas",
    "superdosagem":     "Superdosagem",
    "composicao":       "Composição",
    "completa":         "Bula completa",
}


def make_consultar_bula_secao_tool():
    """
    Factory — tool de busca textual no conteúdo das bulas (FTS Portuguese).

    Recebe (termo_medicamento, pergunta) e retorna os trechos mais relevantes
    da bula, com a passagem em destaque. Permite ao agente CITAR a bula em
    vez de inventar.
    """
    @tool
    async def consultar_bula_secao(termo_medicamento: str, pergunta: str) -> str:
        """
        Busca trechos REAIS da bula da ANVISA sobre uma pergunta específica
        de um medicamento. Use SEMPRE que o cliente perguntar sobre:
        indicações ("para que serve"), posologia/dose, interações,
        contraindicações, gravidez/amamentação, efeitos colaterais,
        armazenamento, ou qualquer detalhe clínico.

        Sempre cite o trecho que a tool retornou — não invente.

        Args:
            termo_medicamento: nome do medicamento ou princípio ativo.
                Ex: "dipirona", "losartana".
            pergunta: a pergunta do cliente em poucas palavras-chave.
                Ex: "dose máxima criança", "interação com warfarina",
                "pode tomar grávida".
        """
        try:
            from services.bula_repo import search_bula
            rows = await search_bula(termo_medicamento, pergunta, limit=3)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "tool.consultar_bula_secao.error",
                termo=termo_medicamento, pergunta=pergunta, exc=str(exc),
            )
            return (
                "Não consegui acessar o texto da bula agora. "
                "Sugira ao cliente conferir a bula impressa."
            )

        if not rows:
            # Bula ainda não extraída pra esse medicamento. Força a
            # extração agora (detail fresco + download PDF + parse + upsert)
            # e tenta a busca de novo.
            try:
                from services.bulario_repo import ensure_bulas_for_termo
                n = await ensure_bulas_for_termo(termo_medicamento, top_n=3)
                if n > 0:
                    log.info(
                        "tool.consultar_bula_secao.bulas_extracted_on_demand",
                        termo=termo_medicamento, n=n,
                    )
                    rows = await search_bula(termo_medicamento, pergunta, limit=3)
            except Exception as exc:  # noqa: BLE001
                log.warning("tool.consultar_bula_secao.ensure_failed", exc=str(exc))

        if not rows:
            return (
                f"Não encontrei trecho da bula sobre '{pergunta}' para "
                f"'{termo_medicamento}'. Confirme se o nome está correto "
                "ou peça ao cliente para reformular a pergunta."
            )

        out = [f"Bula ANVISA — '{termo_medicamento}' / '{pergunta}':"]
        for r in rows:
            label = _SECAO_LABELS.get(r["secao"], r["secao"])
            out.append(
                f"\n[{label} — {r['nome_produto']}]\n{r['trecho']}"
            )
        out.append(
            "\n\n(Trechos extraídos da bula registrada na ANVISA. "
            "Cite na resposta o que está aqui — não complemente com info externa.)"
        )
        return "\n".join(out)

    return consultar_bula_secao
