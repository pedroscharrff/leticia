"""
agents/prompts/builder.py

`PromptBuilder` — montagem DECLARATIVA do system prompt de um skill, preservando
a separação estável/volátil exigida pelo prompt caching (SPEC 08,
[[reference_prompt_caching_volatile_split]]).

Antes, cada skill montava `parts`/`volatile_parts` à mão, com a ordem e a
classificação estável-vs-volátil repetidas em vendedor.py, _base.py, etc. — fácil
jogar um bloco volátil (carrinho) no prefixo estável e estourar o custo de cache.
O builder torna a intenção explícita: `.section()` = estável (cacheado);
`.volatile()` = por-turno (após o marcador de cache).

`.build()` devolve `(system_prompt, volatile_prompt)` — exatamente a mesma tupla
que `_build_messages(state, system_prompt, volatile_prompt=...)` já consome. Não
muda nada no caching; só organiza a montagem.

Persona: `_persona_prefix` continua sendo a ÚNICA porta de render da persona
([[project_persona_phantom_fields]], SPEC 02). O builder a chama em `.core()`
via import preguiçoso (evita ciclo prompts↔_base).
"""
from __future__ import annotations


class PromptBuilder:
    """Montador fluente de prompt de skill.

    Uso típico (ver vendedor.py / farmaceutico.py):

        system, volatile = (
            PromptBuilder(persona, "vendedor",
                          override=skill_prompts.get("vendedor"),
                          extra=skill_instructions.get("vendedor"))
            .core(_SYSTEM)
            .section(commerce.cross_sell_block() if caps["cross_sell"] else None)
            .flow(allowed_targets, handoff=True)
            .extra_instructions()
            .volatile(cart_block)
            .build()
        )
    """

    def __init__(
        self,
        persona: dict | None,
        skill_name: str,
        *,
        override: str | None = None,
        extra: str | None = None,
    ) -> None:
        self._persona = persona or {}
        self._skill = skill_name
        # `override` = prompt custom do tenant (SUBSTITUI o base do skill).
        # `extra`    = extra_instructions do tenant (ACRESCENTA).
        self._override = override
        self._extra = extra
        self._stable: list[str] = []
        self._volatile: list[str] = []
        self._core_done = False

    # ── Estável (prefixo cacheado) ────────────────────────────────────────────

    def core(self, base_system: str) -> "PromptBuilder":
        """Persona (porta única) + prompt base do skill (ou override do tenant).

        Deve ser chamado UMA vez, antes das demais seções estáveis.
        """
        if self._core_done:
            raise RuntimeError("PromptBuilder.core() chamado mais de uma vez")
        # Import preguiçoso: _persona_prefix vive em _base.py, que importa daqui.
        from agents.nodes.skills._base import _persona_prefix
        persona_txt = _persona_prefix(self._persona)
        if persona_txt:
            self._stable.append(persona_txt)
        self._stable.append(self._override or base_system)
        self._core_done = True
        return self

    def section(self, text: str | None) -> "PromptBuilder":
        """Acrescenta um bloco estável (ignora vazio/None)."""
        if text and text.strip():
            self._stable.append(text)
        return self

    def flow(
        self,
        allowed_targets: tuple[str, ...] = (),
        *,
        handoff: bool = True,
        escalate: bool = True,
        end: bool = True,
    ) -> "PromptBuilder":
        """Bloco de controle de fluxo (handoff/escalate/end) gerado do contrato
        das tools de fluxo. Single source: muda a tool, muda o texto."""
        from agents.prompts.flow import flow_instructions
        return self.section(
            flow_instructions(
                allowed_targets, handoff=handoff, escalate=escalate, end=end
            )
        )

    def extra_instructions(self) -> "PromptBuilder":
        """Camada de extra_instructions do dono da farmácia (estável)."""
        if self._extra and self._extra.strip():
            self._stable.append(
                "[INSTRUÇÕES EXTRAS DO DONO DA FARMÁCIA — sobreponha qualquer "
                f"comportamento padrão]\n{self._extra}"
            )
        return self

    # ── Volátil (após o marcador de cache) ────────────────────────────────────

    def volatile(self, text: str | None) -> "PromptBuilder":
        """Acrescenta um bloco volátil — estado por-turno (carrinho, memória do
        cliente, contexto de handoff, sentimento). NUNCA vai no prefixo estável."""
        if text and text.strip():
            self._volatile.append(text)
        return self

    # ── Saída ─────────────────────────────────────────────────────────────────

    def build(self) -> tuple[str, str]:
        """Retorna (system_prompt estável, volatile_prompt)."""
        return "\n\n".join(self._stable), "\n\n".join(self._volatile)
