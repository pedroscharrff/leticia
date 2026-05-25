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


def make_consultar_bula_tool():
    """
    Factory — retorna a tool. Sem closure de tenant porque a base é global.

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
            return f"Nenhum medicamento encontrado no bulário para '{termo}'."

        lines = [f"Bulário ANVISA — '{termo}':"]
        lines.extend(_format_row(r) for r in rows[:5])
        return "\n".join(lines)

    return consultar_bula
