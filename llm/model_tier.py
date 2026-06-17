"""
Classificação de capacidade do modelo — "strong" vs "weak".

Por que existe:
    O agente é multi-tenant: cada tenant escolhe provider/modelo livremente
    (Claude, GPT, Gemini, local). Modelos pequenos/baratos (Gemini *-flash/-lite,
    GPT *-mini/nano, Claude Haiku, modelos locais) seguem prompt complexo e —
    principalmente — CHAMAM TOOLS de forma muito menos confiável que os modelos
    grandes. Medição em prod (jun/2026): farmaceutico em gemini-2.5-flash-lite
    ficou com 82% dos turnos SEM chamar tool, alucinando produto/registro.

    Esta função é a FONTE ÚNICA de "este modelo precisa de andaime?". O caminho
    "strong" deve permanecer byte-idêntico ao comportamento histórico (sem
    andaime, sem bloco extra de prompt) — não quebrar cache nem o que já foi
    validado em tenants Claude/GPT. Andaime (force-call determinístico, bloco de
    disciplina de tool) é GATED por `tier == "weak"`.

Regra de classificação (conservadora):
    1. Provider local (ollama) → SEMPRE weak (modelos locais pequenos, tools
       pouco confiáveis).
    2. TOKEN "weak" no id vence (flash, lite, mini, nano, haiku, ...): o id é
       quebrado em tokens por delimitadores (`-`, `.`, `_`), então comparamos
       token-a-token — NÃO substring. Isso evita o falso-positivo clássico:
       "mini" é substring de "geMINI", mas NÃO é um token de "gemini-2.5-pro".
       Mesmo que o id também contenha um token forte (ex.: "gpt-4.1-mini" → weak).
    3. Substring "strong" → strong (sonnet, opus, gpt-4o/4.1/5, gemini-*-pro, o3/o4).
    4. Desconhecido → "strong" (default seguro: NÃO injeta andaime em modelo que
       não conhecemos, pra nunca alterar comportamento sem certeza). Modelo novo
       fraco que escape disso é coberto adicionando o marcador aqui.
"""
from __future__ import annotations

import re
from typing import Literal

Tier = Literal["strong", "weak"]

# Quebra o id em tokens: "gemini-2.5-flash-lite" → {gemini,2,5,flash,lite}.
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

# TOKENS que indicam modelo pequeno/barato → tool-calling não confiável.
# Comparação por TOKEN (não substring) para não casar "mini" dentro de "gemini".
_WEAK_TOKENS = frozenset({
    "flash",      # gemini-*-flash
    "lite",       # *-flash-lite
    "mini",       # gpt-4o-mini, gpt-4.1-mini, gpt-5-mini, o1-mini, o3-mini, o4-mini
    "nano",       # gpt-4.1-nano, gpt-5-nano
    "haiku",      # claude-haiku-*
    "llama",      # locais
    "mistral",
    "phi", "phi3",
    "gemma",
})

# Substrings que, presentes, marcam o id como weak independentemente de tokens
# (famílias legadas multi-parte). Checado antes dos fortes.
_WEAK_SUBSTR = ("gpt-3.5",)

# Substrings de modelo grande/capaz — seguem instrução complexa e chamam tools.
_STRONG_MARKERS = (
    "sonnet",
    "opus",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5",
    "gemini-2.5-pro",
    "gemini-1.5-pro",
    "-pro",        # genérico p/ famílias *-pro
    "o3",
    "o4",
)


def model_tier(provider: str | None, model: str | None) -> Tier:
    """Retorna "strong" | "weak" para o par (provider, model) resolvido.

    Conservador: na dúvida devolve "strong" (não injeta andaime → não muda
    comportamento de modelo desconhecido). Ver docstring do módulo.
    """
    prov = (provider or "").strip().lower()
    mid = (model or "").strip().lower()

    # 1. Local é sempre weak.
    if prov == "ollama":
        return "weak"

    if not mid:
        return "strong"

    # 2. Token weak vence (mini/nano/lite/flash/haiku...) + substr weak legada.
    tokens = set(_TOKEN_SPLIT.split(mid))
    if tokens & _WEAK_TOKENS or any(s in mid for s in _WEAK_SUBSTR):
        return "weak"

    # 3. Marcador strong.
    if any(m in mid for m in _STRONG_MARKERS):
        return "strong"

    # 4. Desconhecido → strong (default seguro).
    return "strong"


# Providers cujos modelos — EM QUALQUER tamanho — precisam de andaime de
# tool-calling. Medição em prod (jun/2026): mesmo `gemini-2.5-pro` (tier strong)
# dispara tools de fluxo (handoff/transferência) à toa e mistura tool de domínio
# + fluxo no mesmo turno, descartando o resultado. É comportamento da FAMÍLIA,
# não do tamanho. Anthropic/OpenAI grandes não apresentam isso.
_SCAFFOLD_PROVIDERS = frozenset({"google", "ollama"})


def needs_tool_scaffolding(provider: str | None, model: str | None) -> bool:
    """True quando o modelo precisa de andaime determinístico de tool-calling.

    GATE ÚNICO usado pelo runtime (guarda domínio+fluxo) e pelo PromptBuilder
    (bloco de disciplina de tool). Critério: tier weak OU provider notoriamente
    fraco em tool-calling (Google/Ollama, qualquer tamanho).

    Invariante: para modelos fortes de Anthropic/OpenAI retorna False → caminho
    histórico byte-idêntico (sem andaime, cache intacto).
    """
    prov = (provider or "").strip().lower()
    if prov in _SCAFFOLD_PROVIDERS:
        return True
    return model_tier(provider, model) == "weak"
