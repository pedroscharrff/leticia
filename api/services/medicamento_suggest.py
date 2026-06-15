"""
Serviço de sugestão de nome de medicamento — o motor do recurso
"Você quis dizer…?".

Entra em cena quando o cliente escreve o nome de um remédio com erro de
digitação forte o suficiente para o bulário (corte trigram 0.45 em
`bulario_repo`) e a própria busca da ANVISA não casarem. Em vez de o agente
dizer só "não encontrei", este serviço devolve uma lista curta de CANDIDATOS
de correção, que o agente oferece ao cliente para confirmação.

Pipeline em camadas (determinístico-first, cf. CLAUDE.md §10 princípio 5):

  camada 1  fuzzy nas bases REAIS já existentes (medicamentos_anvisa +
            medicamentos_referencia) via pg_trgm. Cobre a maioria dos typos,
            instantâneo, sem custo, sem dependência externa.
  camada 2  normalização por LLM leve (Haiku) — para grafias muito distorcidas
            que o trigram não alcança.
  camada 3  busca web nativa do Claude (web_search_20250305), opcional — último
            recurso para nomes que o LLM sozinho não reconhece.

⚠️ INVARIANTE DE SEGURANÇA: os candidatos das camadas 2 e 3 são SEMPRE
verificados contra a base da ANVISA (`bulario_repo.get_or_fetch`) antes de
serem devolvidos. Nunca confiamos cegamente na grafia que o LLM/web produz —
medicamento errado é risco clínico. A tool/o prompt, por sua vez, garantem que
o agente apenas SUGIRA (pergunte "você quis dizer X?") e nunca substitua o
nome sozinho.

Observabilidade: a camada LLM usa o SDK `anthropic` direto (não passa pelo
callback de tokens do LangChain), então a usage é logada aqui de forma
estruturada (`medicamento_suggest.llm.usage`). Volume é baixo — só roda no
caminho "não encontrei".
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog

from config import settings
from db.postgres import get_db_conn

log = structlog.get_logger()

# Modelo leve para a normalização/busca (canonical em llm/providers.py::HAIKU).
_SUGGEST_MODEL = "claude-haiku-4-5-20251001"

# Piso de similaridade trigram para a camada 1. Mais frouxo que o
# MIN_SIMILARITY=0.45 do bulário DE PROPÓSITO: aqui queremos exatamente os
# "quase-matches" que o bulário descartou (typos), para oferecê-los como
# sugestão (com confirmação do cliente), não como resposta autoritativa.
_FUZZY_FLOOR = 0.30


@dataclass(frozen=True)
class Candidate:
    """Um candidato de correção, já normalizado para exibição."""
    nome: str                 # nome a sugerir ao cliente (limpo)
    principio_ativo: str | None
    origem: str               # 'bulario' | 'referencia' | 'llm' | 'web'

    def label(self) -> str:
        if self.principio_ativo and self.principio_ativo.lower() not in self.nome.lower():
            return f"{self.nome} ({self.principio_ativo})"
        return self.nome


def _display(nome: str | None) -> str:
    """Limpa um nome para exibição. Os nomes da ANVISA vêm em CAIXA ALTA."""
    n = (nome or "").strip()
    if not n:
        return n
    # Só title-case quando está todo em maiúsculas (evita estragar grafias mistas).
    if n.isupper():
        n = n.title()
    return n


def _dedup_key(nome: str) -> str:
    return " ".join((nome or "").lower().split())


# ── Camada 1: fuzzy nas bases reais ─────────────────────────────────────────

async def _fuzzy_candidates(termo_norm: str, limit: int) -> list[Candidate]:
    """
    Busca candidatos por similaridade trigram em medicamentos_anvisa e
    medicamentos_referencia. O operador `%` gera candidatos pelo índice GIN
    (similarity ≥ 0.30); rankeamos por GREATEST(similarity, word_similarity)
    para favorecer typos que são "uma palavra dentro" do nome.
    """
    out: list[Candidate] = []
    try:
        async with get_db_conn() as conn:
            bul = await conn.fetch(
                """
                SELECT nome_produto, principio_ativo,
                       GREATEST(similarity(nome_produto_norm, $1),
                                word_similarity($1, nome_produto_norm)) AS sim
                  FROM public.medicamentos_anvisa
                 WHERE nome_produto_norm % $1
                 ORDER BY sim DESC NULLS LAST
                 LIMIT $2
                """,
                termo_norm, limit,
            )
            ref = await conn.fetch(
                """
                SELECT nome_referencia, principio_ativo,
                       GREATEST(similarity(COALESCE(nome_referencia_norm, ''), $1),
                                similarity(principio_ativo_norm, $1)) AS sim
                  FROM public.medicamentos_referencia
                 WHERE COALESCE(nome_referencia_norm, '') % $1
                    OR principio_ativo_norm % $1
                 ORDER BY sim DESC NULLS LAST
                 LIMIT $2
                """,
                termo_norm, limit,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("medicamento_suggest.fuzzy.error", termo=termo_norm, exc=str(exc))
        return []

    for r in bul:
        if (r["sim"] or 0) < _FUZZY_FLOOR:
            continue
        nome = _display(r["nome_produto"])
        if nome:
            out.append(Candidate(nome=nome,
                                 principio_ativo=_display(r["principio_ativo"]) or None,
                                 origem="bulario"))
    for r in ref:
        if (r["sim"] or 0) < _FUZZY_FLOOR:
            continue
        nome = _display(r["nome_referencia"]) or _display(r["principio_ativo"])
        if nome:
            out.append(Candidate(nome=nome,
                                 principio_ativo=_display(r["principio_ativo"]) or None,
                                 origem="referencia"))
    return out


# ── Camadas 2/3: normalização por LLM + busca web (verificadas) ─────────────

async def _llm_candidate_names(termo: str, *, enable_web: bool, max_names: int) -> list[str]:
    """
    Pede a um modelo leve (Haiku) os nomes de medicamento mais prováveis para
    um termo possivelmente mal escrito. Quando `enable_web`, habilita a busca
    web nativa do Claude para grafias que o modelo sozinho não reconhece.

    Retorna apenas a LISTA DE NOMES sugeridos pelo LLM — a verificação contra a
    ANVISA é feita pelo chamador. Defensivo: qualquer falha (sem chave, SDK
    indisponível, timeout) retorna [] e o pipeline degrada para só a camada 1.
    """
    if not settings.anthropic_api_key:
        return []
    try:
        from anthropic import AsyncAnthropic
    except Exception as exc:  # noqa: BLE001
        log.warning("medicamento_suggest.llm.no_sdk", exc=str(exc))
        return []

    system = (
        "Você corrige nomes de MEDICAMENTOS brasileiros escritos com erro de "
        "digitação por clientes de farmácia no WhatsApp. Receberá um termo "
        "possivelmente errado. Responda APENAS com os nomes de medicamento "
        "(marca comercial ou princípio ativo) mais prováveis que a pessoa quis "
        f"dizer — no máximo {max_names}, um por linha, sem numeração, sem "
        "explicação, sem dosagem. Se o termo claramente NÃO for um medicamento "
        "(comida, objeto, etc.), responda exatamente: NENHUM."
    )
    tools = []
    if enable_web:
        tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]

    # Com web search ligado o turno pode gastar tokens nos blocos de busca antes
    # do texto final; folga maior evita truncar no meio do loop. Sem web, a
    # resposta é só os nomes — 150 basta.
    max_toks = 512 if tools else 150

    client = AsyncAnthropic(api_key=settings.anthropic_api_key,
                            timeout=float(settings.llm_timeout_seconds))
    try:
        kwargs: dict = dict(
            model=_SUGGEST_MODEL,
            max_tokens=max_toks,
            system=system,
            messages=[{"role": "user", "content": termo}],
        )
        if tools:
            kwargs["tools"] = tools
        resp = await client.messages.create(**kwargs)
    except Exception as exc:  # noqa: BLE001
        # web_search pode não estar habilitado na conta → tenta de novo sem ele.
        if tools:
            try:
                resp = await client.messages.create(
                    model=_SUGGEST_MODEL, max_tokens=150, system=system,
                    messages=[{"role": "user", "content": termo}],
                )
            except Exception as exc2:  # noqa: BLE001
                log.warning("medicamento_suggest.llm.error", termo=termo, exc=str(exc2))
                return []
        else:
            log.warning("medicamento_suggest.llm.error", termo=termo, exc=str(exc))
            return []
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    # Usage estruturada (não passa pelo callback de tokens do LangChain).
    try:
        u = getattr(resp, "usage", None)
        if u is not None:
            log.info("medicamento_suggest.llm.usage", termo=termo,
                     model=_SUGGEST_MODEL,
                     input_tokens=getattr(u, "input_tokens", None),
                     output_tokens=getattr(u, "output_tokens", None),
                     web=bool(tools))
    except Exception:  # noqa: BLE001
        pass

    # Extrai o texto dos blocos de resposta (ignora blocos de tool/web_search).
    text = " ".join(
        getattr(b, "text", "") for b in (resp.content or [])
        if getattr(b, "type", "") == "text"
    ).strip()
    if not text or "NENHUM" in text.upper():
        return []

    names: list[str] = []
    for line in text.splitlines():
        cand = line.strip().lstrip("-•*0123456789. ").strip()
        if cand and cand.upper() != "NENHUM":
            names.append(cand)
    return names[:max_names]


async def _verify_against_anvisa(nome: str) -> Candidate | None:
    """
    Confirma que o nome sugerido pelo LLM/web existe de fato na ANVISA.
    Reusa o caminho canônico do bulário (cache → local → ANVISA). Devolve o
    Candidate com o nome CANÔNICO da ANVISA (não a grafia do LLM), ou None.
    """
    try:
        from services.bulario_repo import get_or_fetch
        rows = await get_or_fetch(nome, limit=1)
    except Exception as exc:  # noqa: BLE001
        log.warning("medicamento_suggest.verify.error", nome=nome, exc=str(exc))
        return None
    if not rows:
        return None
    r = rows[0]
    return Candidate(
        nome=_display(r.get("nome_produto")) or _display(nome),
        principio_ativo=_display(r.get("principio_ativo")) or None,
        origem="llm",
    )


# ── Orquestração ────────────────────────────────────────────────────────────

async def sugerir_nomes(
    termo: str,
    *,
    max_candidates: int = 3,
    enable_web: bool = True,
) -> list[Candidate]:
    """
    Devolve até `max_candidates` candidatos de correção para `termo`, deduplicados
    e priorizados (camada 1 primeiro). Nunca lança — retorna [] em qualquer falha.

    O chamador (a tool) formata para o agente. O AGENTE deve apenas sugerir e
    pedir confirmação — nunca substituir o nome sozinho.
    """
    termo_norm = " ".join((termo or "").lower().split())
    if not termo_norm:
        return []

    # over-fetch um pouco para sobreviver à dedup.
    fetch_n = max(max_candidates * 2, 4)
    results: list[Candidate] = []
    seen: set[str] = set()

    def _add(c: Candidate) -> None:
        k = _dedup_key(c.nome)
        if k and k != termo_norm and k not in seen:
            seen.add(k)
            results.append(c)

    # Camada 1 — determinística.
    for c in await _fuzzy_candidates(termo_norm, fetch_n):
        _add(c)

    # Camadas 2/3 — só se a camada 1 não deu candidatos suficientes (economiza
    # custo: o caso comum é typo leve, resolvido pelo trigram).
    if len(results) < max_candidates:
        names = await _llm_candidate_names(
            termo, enable_web=enable_web, max_names=max_candidates,
        )
        for nome in names:
            verified = await _verify_against_anvisa(nome)
            if verified is not None:
                _add(verified)
            if len(results) >= max_candidates:
                break

    log.info("medicamento_suggest.done", termo=termo_norm,
             n=len(results), web=enable_web)
    return results[:max_candidates]
