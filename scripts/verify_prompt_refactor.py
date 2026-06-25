"""
scripts/verify_prompt_refactor.py

Verificação da modularização de prompts / runtime / flow-control (2026-06).

Roda em DOIS níveis:
  • Sem langchain (qualquer máquina): checa as derivações do skills_registry e a
    igualdade byte-a-byte dos blocos de capability em prompts/commerce.py contra
    o texto original inline do vendedor.py (garante que a extração foi refactor
    PURO — mesma saída).
  • Com langchain (ambiente do worker / Docker): adiciona o smoke das tools de
    fluxo (Literal de destino) e a renderização do PromptBuilder.

Uso:
    python scripts/verify_prompt_refactor.py
Sai com código !=0 se algum check falhar.
"""
from __future__ import annotations

import os
import sys

# Bootstrap: roda a partir de scripts/ — garante o root do projeto no path.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


# ── 1. Registry (sem langchain) ───────────────────────────────────────────────
def verify_registry() -> None:
    from agents.skills_registry import (
        KNOWN_SKILLS, PLAN_GATED_SKILLS, valid_handoff_targets,
        allowed_handoffs_for, skill_descriptions, SKILLS,
    )
    check("registry: guardrails é safety net (fora de plan-gated)",
          "guardrails" in KNOWN_SKILLS and "guardrails" not in PLAN_GATED_SKILLS)
    check("registry: 6 skills plan-gated", len(PLAN_GATED_SKILLS) == 6,
          str(PLAN_GATED_SKILLS))
    check("registry: farmaceutico handoffs",
          allowed_handoffs_for("farmaceutico") == ("vendedor", "genericos", "principio_ativo"))
    check("registry: vendedor handoffs",
          allowed_handoffs_for("vendedor") == ("farmaceutico", "genericos", "principio_ativo"))
    check("registry: saudacao sem handoff", allowed_handoffs_for("saudacao") == ())
    # _VALID_HANDOFF_TARGETS (parser fallback) == antigo set hardcoded
    check("registry: parser-fallback == plan-gated (permissivo, como antes)",
          set(PLAN_GATED_SKILLS) == {"saudacao", "farmaceutico", "principio_ativo",
                                     "genericos", "vendedor", "recuperador"})
    check("registry: descrições para todos os skills",
          set(skill_descriptions().keys()) == set(KNOWN_SKILLS))
    check("registry: node_path resolvível (shape)",
          all(":" in d.node_path for d in SKILLS.values()))


# ── 2. Commerce blocks == original inline (refactor puro) ─────────────────────
def verify_commerce_equality() -> None:
    from agents.prompts import commerce as c
    D = "═" * 63

    def orig_cross(max_sug):
        return (
            D + "\n" + "CROSS-SELL ATIVO (ofereça complementos)\n" + D + "\n"
            "Você TEM a tool `recomendar_complementos(produto)`. Sempre que o cliente\n"
            "adicionar um item ao carrinho com `adicionar_ao_carrinho`, CHAME\n"
            "`recomendar_complementos` com o mesmo produto e ofereça NO MÁXIMO\n"
            f"{max_sug} sugestão por turno com framing de valor (ex.: 'quem leva X\n"
            "costuma levar Y para potencializar'). Nunca empurre — pergunte e siga\n"
            "o ritmo do cliente."
        )

    def orig_ship():
        return (
            D + "\n" + "FRETE POR CEP ATIVO\n" + D + "\n"
            "Você TEM a tool `calcular_frete(cep, subtotal)`. Sempre que o cliente\n"
            "fornecer o CEP de entrega, ANTES de fechar o pedido, CHAME essa tool\n"
            "passando o CEP e o subtotal atual do carrinho. Comunique valor + prazo\n"
            "+ total final em UMA frase. Se o tool retornar 'frete grátis', destaque\n"
            "isso para o cliente.\n"
            "IMPORTANTE: informe EXATAMENTE o valor e o prazo que o tool retornar — "
            "NUNCA estime, arredonde ou prometa frete grátis por conta própria. "
            "Se o tool disser que o CEP está FORA DA ÁREA de entrega, NÃO invente um "
            "valor: avise o cliente que vai confirmar a entrega com o atendente."
        )

    def orig_mem():
        return (
            D + "\n" + "MEMÓRIA DE CLIENTES ATIVA\n" + D + "\n"
            "Você TEM as tools `registrar_alergia(...)`, `registrar_medicamento_continuo(...)`\n"
            "e `registrar_preferencia(...)`. Use SEMPRE que o cliente declarar\n"
            "uma alergia, mencionar medicamento de uso contínuo, ou expressar\n"
            "uma preferência. NÃO confirme com mensagens longas — só registre e\n"
            "siga o atendimento naturalmente."
        )

    def orig_pix(auto):
        return (
            D + "\n" + "PIX NO CHAT ATIVO (Asaas)\n" + D + "\n"
            "Você TEM a tool `gerar_link_pix(numero_pedido, valor_total)`.\n"
            + ("Sempre que `finalizar_pedido` retornar um número de pedido com\n"
               "sucesso E o cliente tiver escolhido pagamento PIX, CHAME essa tool\n"
               "imediatamente passando o número do pedido e o valor total\n"
               "(incluindo frete se aplicável). Repasse ao cliente o copia-cola\n"
               "PIX retornado.\n"
               if auto else
               "Quando o cliente PEDIR explicitamente o PIX (ex.: \"manda o PIX\"),\n"
               "CHAME essa tool com o número do pedido e o valor total.\n")
            + "Se a tool retornar uma mensagem pedindo CPF, peça o CPF ao cliente,\n"
              "salve com `salvar_dados_cliente` e tente novamente.\n"
              "Após o cliente pagar, o sistema avisará automaticamente — você não\n"
              "precisa ficar perguntando se pagou."
        )

    cases = [
        ("cross_sell(1)", c.cross_sell_block(1), orig_cross(1)),
        ("cross_sell(2)", c.cross_sell_block(2), orig_cross(2)),
        ("shipping", c.shipping_block(), orig_ship()),
        ("memory", c.customer_memory_block(), orig_mem()),
        ("pix(auto)", c.pix_block(True), orig_pix(True)),
        ("pix(manual)", c.pix_block(False), orig_pix(False)),
    ]
    for name, new, old in cases:
        check(f"commerce == original: {name}", new == old)


# ── 3. Flow tools + PromptBuilder (precisa de langchain) ──────────────────────
def verify_flow_tools() -> None:
    try:
        from agents.tools.flow_control import (
            make_flow_control_tools, FLOW_CONTROL_TOOL_NAMES, HANDOFF_TOOL_NAME,
        )
    except ModuleNotFoundError as exc:
        print(f"[SKIP] flow tools / PromptBuilder — {exc} (rode no ambiente do worker)")
        return

    tools = make_flow_control_tools(("vendedor", "genericos"))
    names = [t.name for t in tools]
    check("flow: 3 tools quando há handoff", len(tools) == 3, str(names))
    handoff = next(t for t in tools if t.name == HANDOFF_TOOL_NAME)
    enum = handoff.args_schema.model_json_schema()["properties"]["target_skill"].get("enum")
    check("flow: Literal de destino == allowed", enum == ["vendedor", "genericos"], str(enum))
    check("flow: nomes registrados em FLOW_CONTROL_TOOL_NAMES",
          set(names) <= set(FLOW_CONTROL_TOOL_NAMES))
    no_handoff = make_flow_control_tools(())
    check("flow: sem handoff quando lista vazia",
          HANDOFF_TOOL_NAME not in {t.name for t in no_handoff})

    # PromptBuilder render
    from agents.prompts import PromptBuilder
    persona = {"agent_name": "Ana", "pharmacy_name": "Farmácia Teste"}
    sys_p, vol_p = (
        PromptBuilder(persona, "vendedor", extra="seja gentil")
        .core("BASE DO SKILL")
        .flow(("farmaceutico",), handoff=True, escalate=True, end=True)
        .extra_instructions()
        .volatile("CARRINHO: 2x Dipirona")
        .build()
    )
    check("builder: persona no estável", "Ana" in sys_p)
    check("builder: core no estável", "BASE DO SKILL" in sys_p)
    check("builder: flow no estável", "transferir_para_especialidade" in sys_p)
    check("builder: extra no estável", "seja gentil" in sys_p)
    check("builder: carrinho só no volátil",
          "CARRINHO" in vol_p and "CARRINHO" not in sys_p)


def main() -> int:
    print("== Verificação: modularização de prompts / runtime / flow-control ==\n")
    verify_registry()
    print()
    verify_commerce_equality()
    print()
    verify_flow_tools()
    print()
    if FAILS:
        print(f"RESULTADO: {len(FAILS)} FALHA(S) — {', '.join(FAILS)}")
        return 1
    print("RESULTADO: tudo OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
