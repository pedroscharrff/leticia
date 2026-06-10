"""
Tool: consultar_base_conhecimento

Permite ao agente (farmacêutico) consultar a base de conhecimento curada pelo
admin geral: PDFs e textos sobre sítios de ligação, interações medicamentosas,
literatura técnica, dosagem pediátrica etc.

Diferenças importantes:
  * `consultar_bula(termo)` → registro ANVISA de um produto específico.
  * `consultar_base_conhecimento(consulta, categoria?)` → literatura curada
    (global, não ANVISA), busca semântica em pgvector.

Implementação em services/training_kb.py:
    upload admin → chunk + embed → busca por cosine similarity.
"""
from __future__ import annotations

import structlog
from langchain_core.tools import tool

log = structlog.get_logger()


def _format(chunks) -> str:
    """Concatena trechos retornados em um bloco legível pro LLM."""
    if not chunks:
        return "Sem resultado na base de conhecimento curada."
    lines = ["Base de conhecimento curada — trechos relevantes:"]
    for c in chunks:
        header = f"[{c.document_title}"
        if c.category:
            header += f" — {c.category}"
        header += f" — trecho {c.chunk_index}]"
        lines.append(f"\n{header}\n{c.content.strip()}")
    lines.append(
        "\n\n(Estes trechos vêm de literatura curada pela farmácia. "
        "Cite o que está aqui — não complemente com info externa.)"
    )
    return "\n".join(lines)


def make_consultar_base_conhecimento_tool():
    """Factory — sem closure de tenant (base é GLOBAL).

    Existe como factory para consistência com as outras tools do projeto
    (inventory, customer, balcao, bulario) e para facilitar mock em testes.
    """
    @tool
    async def consultar_base_conhecimento(
        consulta: str,
        categoria: str | None = None,
    ) -> str:
        """
        Consulta a base de conhecimento curada pela farmácia (literatura
        técnica: sítios de ligação, interações medicamentosas, farmacologia
        avançada, dosagem pediátrica/geriátrica). Use SEMPRE que a pergunta
        envolver:
          - Interação entre 2+ medicamentos (ex.: "omeprazol + clopidogrel").
          - Sítio de ligação de um fármaco.
          - Mecanismo de ação em nível farmacológico.
          - Dosagem em populações especiais não cobertas pela bula.

        Não use para consulta a um produto específico da ANVISA — para isso
        use `consultar_bula` / `consultar_bula_secao`.

        Args:
            consulta: pergunta em linguagem natural. Ex.: "interação
                omeprazol e clopidogrel", "sítio de ligação da warfarina",
                "dose de ibuprofeno em criança de 8 kg".
            categoria: opcional — filtra por categoria do documento
                (ex.: "interacoes", "sitios_ligacao", "dosagem_pediatrica").
                Use APENAS quando souber que o admin organizou docs nessa
                categoria; senão deixe vazio.
        """
        try:
            from services.training_kb import retrieve
            chunks = await retrieve(consulta, categoria=categoria, k=4)
        except Exception as exc:  # noqa: BLE001
            log.warning("tool.consultar_base.error", consulta=consulta[:100], exc=str(exc))
            return (
                "Não consegui consultar a base de conhecimento agora. "
                "Use seu conhecimento geral com cautela."
            )
        return _format(chunks)

    return consultar_base_conhecimento
