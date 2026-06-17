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
#
# Camada 2 (normalização) é AGNÓSTICA DE PROVEDOR: roda no LLM do próprio tenant
# (respeita BYOK / OpenAI / Gemini / Ollama) via a factory get_llm/_for_tenant.
# Camada 3 (web search) é específica da Anthropic (web search nativo) e só roda
# quando há uma chave Anthropic disponível (a do tenant se BYOK-Anthropic, senão
# a da plataforma). Sem chave Anthropic, pula a web — camadas 1+2 cobrem.

_SUGGEST_SYSTEM = (
    "Você corrige nomes de MEDICAMENTOS brasileiros escritos com erro de "
    "digitação por clientes de farmácia no WhatsApp. Receberá um termo "
    "possivelmente errado. Responda APENAS com os nomes de medicamento "
    "(marca comercial ou princípio ativo) mais prováveis que a pessoa quis "
    "dizer — no máximo {n}, um por linha, sem numeração, sem explicação, sem "
    "dosagem. Se o termo claramente NÃO for um medicamento (comida, objeto, "
    "etc.), responda exatamente: NENHUM."
)


def _parse_names(text: str, max_names: int) -> list[str]:
    text = (text or "").strip()
    if not text or "NENHUM" in text.upper():
        return []
    names: list[str] = []
    for line in text.splitlines():
        cand = line.strip().lstrip("-•*0123456789. ").strip()
        if cand and cand.upper() != "NENHUM":
            names.append(cand)
    return names[:max_names]


async def _resolve_tenant_llm(tenant_id: str | None) -> dict:
    """
    Resolve o provedor/modelo/credencial de SKILL do tenant (reusa o mesmo
    helper que os skills usam). Defensivo: em qualquer falha cai nos defaults da
    plataforma. Acrescenta `anthropic_key` — a chave a usar na camada web.
    """
    cfg: dict = {}
    if tenant_id:
        try:
            from services.llm_config import load_tenant_llm_config
            cfg = await load_tenant_llm_config(tenant_id) or {}
        except Exception as exc:  # noqa: BLE001
            log.warning("medicamento_suggest.llm_config.error", exc=str(exc))
    provider = cfg.get("default_skill_provider") or settings.default_skill_provider
    model    = cfg.get("default_skill_model") or settings.default_skill_model
    mode     = cfg.get("llm_mode") or "credits"
    api_key  = cfg.get("llm_api_key")
    base_url = cfg.get("llm_base_url")
    # Camada web: chave do tenant só se ele é BYOK-Anthropic; senão plataforma.
    if provider == "anthropic" and mode == "byok" and api_key:
        anthropic_key = api_key
    else:
        anthropic_key = settings.anthropic_api_key
    return {
        "provider": provider, "model": model, "mode": mode,
        "api_key": api_key, "base_url": base_url, "anthropic_key": anthropic_key,
    }


async def _normalize_with_llm(termo: str, llmcfg: dict, max_names: int) -> list[str]:
    """
    Camada 2 — normalização da grafia no provedor do TENANT (provider-agnóstico,
    via factory). Token tracking acontece pelo callback padrão do LangChain.
    """
    try:
        from langchain_core.messages import SystemMessage, HumanMessage
        from llm.providers import get_llm, get_llm_for_tenant
        if llmcfg["mode"] == "byok" and llmcfg["api_key"]:
            llm = get_llm_for_tenant(
                llmcfg["provider"], llmcfg["model"],
                llmcfg["api_key"], llmcfg.get("base_url"),
            )
        else:
            llm = get_llm(llmcfg["provider"], llmcfg["model"])
        resp = await llm.ainvoke([
            SystemMessage(content=_SUGGEST_SYSTEM.format(n=max_names)),
            HumanMessage(content=termo),
        ])
    except Exception as exc:  # noqa: BLE001
        log.warning("medicamento_suggest.normalize.error",
                    termo=termo, provider=llmcfg.get("provider"), exc=str(exc))
        return []
    content = getattr(resp, "content", "")
    if isinstance(content, list):  # alguns providers retornam blocos
        content = " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return _parse_names(str(content), max_names)


async def _web_search_names(termo: str, anthropic_key: str, max_names: int) -> list[str]:
    """
    Camada 3 — busca web nativa do Claude (web_search_20250305). Específica da
    Anthropic; usa o SDK direto (não há abstração de server-tool na factory).
    Não passa pelo callback de tokens do LangChain → usage logada aqui.
    Defensivo: sem SDK / web desabilitada na conta / timeout → [].
    """
    if not anthropic_key:
        return []
    try:
        from anthropic import AsyncAnthropic
    except Exception as exc:  # noqa: BLE001
        log.warning("medicamento_suggest.web.no_sdk", exc=str(exc))
        return []

    system = _SUGGEST_SYSTEM.format(n=max_names)
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 2}]
    client = AsyncAnthropic(api_key=anthropic_key,
                            timeout=float(settings.llm_timeout_seconds))
    try:
        resp = await client.messages.create(
            model=_SUGGEST_MODEL, max_tokens=512, system=system, tools=tools,
            messages=[{"role": "user", "content": termo}],
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("medicamento_suggest.web.error", termo=termo, exc=str(exc))
        return []
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    try:
        u = getattr(resp, "usage", None)
        if u is not None:
            log.info("medicamento_suggest.web.usage", termo=termo, model=_SUGGEST_MODEL,
                     input_tokens=getattr(u, "input_tokens", None),
                     output_tokens=getattr(u, "output_tokens", None))
    except Exception:  # noqa: BLE001
        pass

    text = " ".join(
        getattr(b, "text", "") for b in (resp.content or [])
        if getattr(b, "type", "") == "text"
    )
    return _parse_names(text, max_names)


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
    tenant_id: str | None = None,
    max_candidates: int = 3,
    enable_web: bool = True,
) -> list[Candidate]:
    """
    Devolve até `max_candidates` candidatos de correção para `termo`, deduplicados
    e priorizados (camada 1 primeiro). Nunca lança — retorna [] em qualquer falha.

    Camada 1 é agnóstica de provedor (SQL puro). Camada 2 (normalização) roda no
    LLM do `tenant_id` (respeita BYOK/OpenAI/Gemini/Ollama). Camada 3 (web) usa a
    chave Anthropic disponível. O AGENTE só sugere e pede confirmação — nunca
    substitui o nome sozinho.
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

    async def _verify_and_add(names: list[str]) -> None:
        for nome in names:
            if len(results) >= max_candidates:
                break
            verified = await _verify_against_anvisa(nome)
            if verified is not None:
                _add(verified)

    # Camada 1 — determinística (qualquer provedor).
    for c in await _fuzzy_candidates(termo_norm, fetch_n):
        _add(c)

    # Camadas 2/3 — só se a camada 1 não encheu (economiza custo: o caso comum é
    # typo leve, resolvido pelo trigram). LLM resolvido uma vez.
    used_web = False
    if len(results) < max_candidates:
        llmcfg = await _resolve_tenant_llm(tenant_id)

        # Camada 2 — normalização no provedor do tenant.
        await _verify_and_add(
            await _normalize_with_llm(termo, llmcfg, max_candidates)
        )

        # Camada 3 — web search (Anthropic nativo), só se ainda faltam e há chave.
        if len(results) < max_candidates and enable_web and llmcfg["anthropic_key"]:
            used_web = True
            await _verify_and_add(
                await _web_search_names(termo, llmcfg["anthropic_key"], max_candidates)
            )

    log.info("medicamento_suggest.done", termo=termo_norm, n=len(results),
             tenant_id=tenant_id, web_used=used_web)
    return results[:max_candidates]
