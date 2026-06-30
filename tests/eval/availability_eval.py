"""Mini-eval do sinal de força-busca de estoque (force-recall).

POR QUÊ: enquanto a detecção for por regex de frase, cada correção é um chute. Este
harness transforma "o bot afirma disponibilidade sem buscar" numa MÉTRICA
determinística — rode antes/depois de mexer nos padrões do `availability_guard` e
veja se melhorou ou regrediu, em vez de adivinhar. É o degrau "eval por provider"
da estratégia anti-vazamento (memória `project_weak_llm_grounding_strategy`).

O QUE MEDE: dada a RESPOSTA do agente e se houve `buscar_produto` no turno, o
runtime FORÇA a busca quando `affirms_or_offers_availability(resp)` é True e não
buscou (SPEC 10 §força-busca, sinal A — preço-fantasma/sinal B fica fora deste
skeleton, é testado à parte). Aqui cruzamos a decisão do guard contra rótulos
feitos à mão de transcrições reais.

COMO USAR:
    python tests/eval/availability_eval.py
    (sai com código !=0 se a acurácia cair abaixo de THRESHOLD — pronto pra CI)

COMO ESTENDER:
  • Novo vazamento em prod → adicione uma linha em CASES com expect=True e a frase
    real. Se o eval acusar miss, é sinal de cobrir (NÃO com mais um regex às cegas;
    cf. as camadas closed-world na SPEC 10 / grounding por nome).
  • Por provider → duplique CASES por provider e rode o mesmo scorer; o eixo que
    importa é "afirma sem buscar" por modelo, já que o tier muda o comportamento.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

# Console Windows (cp1252) quebra ao imprimir acento/seta; força utf-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:  # noqa: BLE001
    pass

# Mesmo setup de path do conftest (rodável fora do pytest).
_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT, _ROOT / "api"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from services.availability_guard import affirms_or_offers_availability  # noqa: E402

THRESHOLD = 1.0  # exigimos 100% nos casos curados; baixe se adicionar casos difíceis


@dataclass
class Case:
    id: str
    response: str          # fala do agente ao cliente
    expect_signal: bool    # deveria o force-recall disparar (afirmou/ofertou/recomendou)?
    note: str = ""


# Casos derivados de transcrições REAIS (São João, jun/2026). Mantenha curado.
CASES: list[Case] = [
    # ── Vazamentos que devem disparar ─────────────────────────────────────────
    Case("tosse-temos", "Entendi, tosse com catarro. Para isso temos opções como xarope expectorante. Você prefere xarope ou comprimido?", True, "afirmação direta 'temos'"),
    Case("tosse-maiscomum", "Sim, temos sim! O xarope expectorante mais comum aqui é o Fluimucil ou Bisolvon. Qual prefere?", True, "afirmação + recomendação de marca"),
    Case("tosse-expec", "O Expec é um xarope expectorante à base de guaifenesina, ótimo para tosse com catarro. Quantas unidades?", True, "recomendação de produto NÃO verificado"),
    Case("apresentacao-oferta", "A dipirona vem em comprimido ou gotas, qual você prefere?", True, "oferta de apresentação"),
    # NB: preço puro ("O Dorflex custa R$ 7,99") é SINAL B (preço-fantasma),
    # decidido no runtime cruzando contra os preços buscados no turno — fora do
    # escopo do sinal A (`affirms_or_offers_availability`) que este eval mede.
    # Quando modelarmos o sinal B aqui, adicionar como eixo separado.
    # ── Falas seguras que NÃO devem disparar ──────────────────────────────────
    Case("triagem", "Você está com tosse seca ou com catarro?", False, "triagem clínica"),
    Case("ref-medico", "Para esse caso, recomendo procurar um médico.", False, "recomendação clínica sem produto"),
    Case("negacao", "Infelizmente não temos esse xarope no momento.", False, "negação honesta"),
    Case("dose", "A dose usual de dipirona é 500mg a cada 6 horas.", False, "info clínica de dose"),
    Case("saudacao", "Boa noite! Como posso te ajudar hoje?", False, "saudação"),
]


def run() -> float:
    tp = tn = fp = fn = 0
    misses: list[str] = []
    for c in CASES:
        got = affirms_or_offers_availability(c.response)
        if got and c.expect_signal:
            tp += 1
        elif not got and not c.expect_signal:
            tn += 1
        elif got and not c.expect_signal:
            fp += 1
            misses.append(f"  FALSO POSITIVO [{c.id}] ({c.note}): {c.response[:70]}")
        else:
            fn += 1
            misses.append(f"  FALSO NEGATIVO [{c.id}] ({c.note}): {c.response[:70]}")

    total = len(CASES)
    acc = (tp + tn) / total if total else 1.0
    prec = tp / (tp + fp) if (tp + fp) else 1.0
    rec = tp / (tp + fn) if (tp + fn) else 1.0

    print("=== availability force-recall — mini-eval ===")
    print(f"casos={total}  acc={acc:.2%}  precisão={prec:.2%}  recall={rec:.2%}")
    print(f"TP={tp} TN={tn} FP={fp} FN={fn}")
    if misses:
        print("misses:")
        print("\n".join(misses))
    return acc


if __name__ == "__main__":
    acc = run()
    if acc < THRESHOLD:
        print(f"\nFALHOU: acurácia {acc:.2%} < threshold {THRESHOLD:.2%}")
        sys.exit(1)
    print("\nOK")
