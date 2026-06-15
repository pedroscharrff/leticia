"""
Tool: sugerir_nome_medicamento

Recurso "Você quis dizer…?". Quando `consultar_bula` não localiza um
medicamento (provável erro de digitação do cliente), o agente chama esta tool
para obter CANDIDATOS de correção e oferecê-los ao cliente — em vez de só dizer
"não encontrei" ou, pior, inventar.

Gated pela capability `attendance.medication_name_suggestion` (ON por default).
A checagem de capability é feita no skill (que só inclui a tool quando ligada) E
aqui dentro como defesa em profundidade (cf. SPEC 03 invariante 6).

A lógica de verdade mora em `services/medicamento_suggest.py` (pipeline em
camadas: fuzzy nas bases reais → Haiku → web search nativo, sempre verificando
contra a ANVISA). Esta tool só formata o resultado para o LLM.
"""
from __future__ import annotations

import structlog
from langchain_core.tools import tool

log = structlog.get_logger()


def make_sugerir_nome_medicamento_tool(
    *,
    tenant_id: str | None = None,
    max_candidates: int = 3,
    enable_web: bool = True,
):
    """
    Factory — retorna a tool. Recebe a config da capability por closure
    (resolvida no skill): `max_candidates` e `enable_web`. `tenant_id` é usado
    só para a checagem defensiva de capability.
    """
    @tool
    async def sugerir_nome_medicamento(termo: str) -> str:
        """
        Sugere o NOME CORRETO de um medicamento quando o cliente provavelmente
        escreveu errado. Use SEMPRE que `consultar_bula` não encontrar o
        medicamento e houver chance de ser um erro de digitação (ex.: cliente
        escreveu "rivotrio", "buscopam", "neimosulida", "dipirina").

        Retorna uma lista curta de nomes prováveis para você OFERECER ao
        cliente — NUNCA escolha por ele. Pergunte "Você quis dizer X?" e só
        siga depois que o cliente confirmar qual era. Se a tool não retornar
        candidatos, NÃO invente: peça ao cliente para reenviar o nome ou
        descrever o medicamento (caixa, princípio ativo, para que serve).

        Args:
            termo: o que o cliente escreveu, como veio. Ex: "rivotrio",
                   "buscopam composto", "neimosulida".
        """
        # Defesa em profundidade: respeita o gate mesmo se o skill incluiu a
        # tool por engano. Falha "ON" — em dúvida, sugere (recurso é seguro).
        try:
            from services import capabilities as cap_svc
            if tenant_id and not await cap_svc.is_enabled(
                tenant_id, "attendance.medication_name_suggestion"
            ):
                return (
                    f"Não localizei '{termo}'. Peça ao cliente para reenviar o "
                    "nome do medicamento ou descrevê-lo. NÃO invente."
                )
        except Exception:  # noqa: BLE001
            pass  # check falhou → segue (recurso seguro por construção)

        try:
            from services.medicamento_suggest import sugerir_nomes
            cands = await sugerir_nomes(
                termo, max_candidates=max_candidates, enable_web=enable_web,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("tool.sugerir_nome_medicamento.error", termo=termo, exc=str(exc))
            return (
                f"Não consegui buscar correções para '{termo}' agora. Peça ao "
                "cliente para reenviar o nome do medicamento. NÃO invente."
            )

        if not cands:
            return (
                f"Nenhuma correção encontrada para '{termo}'. NÃO invente um "
                "medicamento. Peça ao cliente para reenviar o nome ou descrever "
                "o remédio (o que está escrito na caixa, para que serve)."
            )

        linhas = [
            f"Possíveis correções para '{termo}' (OFEREÇA ao cliente e peça "
            f"confirmação — não escolha por ele):"
        ]
        for c in cands:
            linhas.append(f"• {c.label()}")
        linhas.append(
            "\nPergunte algo como: \"Você quis dizer "
            + " ou ".join(c.nome for c in cands)
            + "?\". Só prossiga com o que o cliente confirmar."
        )
        return "\n".join(linhas)

    return sugerir_nome_medicamento
