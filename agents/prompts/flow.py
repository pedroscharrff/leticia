"""
agents/prompts/flow.py

Instruções de CONTROLE DE FLUXO (handoff / escalation / fim de atendimento)
geradas a partir do CONTRATO das tools em `agents/tools/flow_control.py`.

Fonte ÚNICA: antes, cada skill carregava no `_SYSTEM` um bloco gigante e
divergente ensinando a sintaxe dos marcadores `[[HANDOFF:...]]`/`[[ESCALATE]]`/
`[[END]]` — e o código parseava esses marcadores em outro lugar. Quando um
mudava e o outro não, vazava marcador pro cliente. Agora o prompt é DERIVADO dos
mesmos nomes de tool que o runtime detecta (`HANDOFF_TOOL_NAME`, etc.) — mudou a
tool, mudou o texto, sem divergência possível.

O texto fala em CHAMAR A TOOL (caminho primário), não em escrever marcador. O
parser de marcadores continua como rede de segurança no _base.py, mas não é mais
ensinado ao LLM aqui.
"""
from __future__ import annotations

from agents.tools.flow_control import (
    HANDOFF_TOOL_NAME,
    ESCALATE_TOOL_NAME,
    END_TOOL_NAME,
)

_DIV = "═══════════════════════════════════════════════════════════════════════"


def handoff_block(allowed_targets: tuple[str, ...]) -> str:
    """Instrução de handoff interno entre especialidades.

    Vazio quando o skill não tem destinos (não recebe a tool de handoff).
    `allowed_targets` deve casar com o `Literal` do `make_handoff_tool`.
    """
    if not allowed_targets:
        return ""
    targets = ", ".join(allowed_targets)
    return (
        f"{_DIV}\n"
        f"PASSAR PARA OUTRA ESPECIALIDADE (interno — invisível ao cliente)\n"
        f"{_DIV}\n"
        f"Para o cliente você é UMA pessoa só. Quando o assunto sai da sua "
        f"especialidade, CHAME a tool `{HANDOFF_TOOL_NAME}` com o destino e um "
        f"contexto curto — NÃO escreva despedida nem 'vou te passar para'. A "
        f"outra especialidade CONTINUA a mesma resposta.\n"
        f"Destinos disponíveis: {targets}.\n"
        f"Não chame a tool quando você mesmo já consegue responder, nem quando "
        f"já está complementando um handoff recebido."
    )


def escalate_block() -> str:
    """Instrução de transferência para atendente humano."""
    return (
        f"{_DIV}\n"
        f"TRANSFERIR PARA ATENDENTE HUMANO\n"
        f"{_DIV}\n"
        f"CHAME a tool `{ESCALATE_TOOL_NAME}` (com um motivo curto) SOMENTE "
        f"quando:\n"
        f"• O cliente pedir explicitamente atendente/humano/balcão;\n"
        f"• Houver emergência médica;\n"
        f"• Houver reclamação grave fora do seu escopo, ou você não resolver "
        f"após tentar.\n"
        f"Antes de chamar, diga UMA frase curta de transição. A frase sozinha "
        f"NÃO transfere — só a tool transfere de verdade.\n"
        f"🛑 NÃO transfira quando o cliente só está comprando, confirmando, "
        f"agradecendo ou se despedindo. Na dúvida, continue você mesmo."
    )


def end_block() -> str:
    """Instrução de fim de atendimento."""
    return (
        f"{_DIV}\n"
        f"ENCERRAR O ATENDIMENTO\n"
        f"{_DIV}\n"
        f"Quando o cliente sinalizar que terminou e NÃO há pedido pendente nem "
        f"nada a transferir ('era só isso', 'obrigado, mais nada', 'tchau', "
        f"'valeu'), dê uma despedida curta e cordial e CHAME a tool "
        f"`{END_TOOL_NAME}`.\n"
        f"NÃO encerre se há itens não anotados, se o cliente pediu atendente, "
        f"ou se ainda há dúvida em aberto."
    )


def flow_instructions(
    allowed_targets: tuple[str, ...] = (),
    *,
    handoff: bool = True,
    escalate: bool = True,
    end: bool = True,
) -> str:
    """Monta o bloco completo de controle de fluxo para um skill.

    Flags permitem desligar partes (ex.: vendedor em pré-atendimento usa
    escalation+end mas não handoff entre skills da mesma forma que o modo ERP).
    """
    blocks: list[str] = []
    if handoff:
        hb = handoff_block(allowed_targets)
        if hb:
            blocks.append(hb)
    if escalate:
        blocks.append(escalate_block())
    if end:
        blocks.append(end_block())
    return "\n\n".join(blocks)
