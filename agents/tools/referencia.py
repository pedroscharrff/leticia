"""
Tool: consultar_medicamento_referencia

Consulta a base curada de medicamentos de REFERÊNCIA (marca original ↔ princípio
ativo), ingerida do "Guia de Medicamentos Genéricos". Responde "qual o original
da Buspirona?" / "qual o genérico do Dobutrex?" — vínculo que a ANVISA não expõe
bem.

A info clínica (indicações, posologia, etc.) só aparece quando a seção foi
REVISADA e ativada no painel superadmin (status='active'); o filtro é feito no
referencia_repo, então a tool nunca recebe seção não-curada.
"""
from __future__ import annotations

import structlog
from langchain_core.tools import tool

log = structlog.get_logger()

# Métrica: quantas vezes uma seção clínica curada foi anexada à resposta —
# alimenta o feedback loop de "o que vale a pena revisar".
try:
    from prometheus_client import Counter
    _REF_CLINICAL_USED = Counter(
        "saas_reference_clinical_used_total",
        "Times an active (curated) clinical section from the reference guide "
        "was surfaced to the pharmacist agent",
        ["secao"],
    )
except Exception:  # noqa: BLE001
    class _StubCounter:
        def labels(self, **_kw):  # type: ignore[override]
            return self
        def inc(self, _amount: int = 1) -> None: ...  # noqa: E704
    _REF_CLINICAL_USED = _StubCounter()  # type: ignore[assignment]


# Slug → rótulo legível (mantém em sincronia com a ingestão e o painel).
_SECAO_LABELS = {
    "indicacoes":       "Indicações",
    "posologia":        "Posologia",
    "contraindicacoes": "Contraindicações",
    "efeitos_adversos": "Efeitos adversos",
    "interacoes":       "Interações",
    "precaucoes":       "Precauções",
}


def _format_mapping(r: dict) -> str:
    pa   = r.get("principio_ativo") or "?"
    ref  = r.get("nome_referencia") or "—"
    forma = r.get("forma_farmaceutica") or "—"
    cat  = r.get("categoria")
    linha = f"• {pa} | referência (original): {ref} | forma: {forma}"
    if cat:
        linha += f" | categoria: {cat}"
    return linha


def make_consultar_medicamento_referencia_tool():
    """
    Factory — retorna a tool. Sem closure de tenant porque a base é global
    (compartilhada por todas as farmácias).
    """
    @tool
    async def consultar_medicamento_referencia(termo: str) -> str:
        """
        Consulta a base curada de medicamentos de REFERÊNCIA (marca original).
        Use quando o cliente perguntar:
        • "qual o medicamento de referência / o original de <genérico>?"
        • "qual o genérico de <marca>?"
        • para confirmar o vínculo princípio ativo ↔ marca original.

        Pode trazer também informação clínica REVISADA (indicações, posologia,
        etc.) como complemento — só virá conteúdo já validado pela farmácia.
        Para dúvida clínica detalhada e atual, prefira `consultar_bula_secao`
        (bula da ANVISA).

        Args:
            termo: nome do princípio ativo OU da marca. Ex: "buspirona",
                   "dobutrex", "dipirona".
        """
        try:
            from services.referencia_repo import search_referencia
            rows = await search_referencia(termo, limit=5)
        except Exception as exc:  # noqa: BLE001
            log.warning("tool.consultar_medicamento_referencia.error",
                        termo=termo, exc=str(exc))
            return (
                "Não consegui consultar a base de medicamentos de referência "
                "agora. Não afirme qual é o original/genérico sem confirmar."
            )

        if not rows:
            return (
                f"Nenhum medicamento de referência encontrado para '{termo}'. "
                "NÃO invente qual é o original ou o genérico."
            )

        out = [f"Base de referência — '{termo}':"]
        for r in rows[:5]:
            out.append(_format_mapping(r))
            # Seções clínicas curadas (já filtradas por status='active' no repo).
            for sec in (r.get("secoes") or []):
                slug = sec.get("secao") or ""
                conteudo = (sec.get("conteudo") or "").strip()
                if not conteudo:
                    continue
                label = _SECAO_LABELS.get(slug, slug)
                out.append(f"   [{label}] {conteudo}")
                _REF_CLINICAL_USED.labels(secao=slug or "?").inc()

        out.append(
            "\n(Mapeamento do guia de referência. Trechos clínicos, quando "
            "presentes, são revisados — cite a proveniência e, para detalhe "
            "clínico atual, confirme na bula da ANVISA.)"
        )
        return "\n".join(out)

    return consultar_medicamento_referencia
