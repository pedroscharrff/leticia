# SPEC 10 — Safety Guards

**Propósito**: validar a resposta do LLM de forma **determinística** antes do cliente ver, corrigindo erros regulatórios/comerciais sem depender do modelo "acertar sozinho".

## Onde vive

```
agents/nodes/safety_guard.py             # umbrella node no grafo
api/services/availability_guard.py       # produto inventado
api/services/price_guard.py              # preço diferente do catálogo
api/services/prescription_guard.py       # "não precisa receita" em tarja
api/services/delivery_guard.py           # "frete grátis" sem regra
```

Tudo gated por:
- `inventory.track_stock` (modo ERP) — passthrough total em pré-atendimento
- Cada guard tem sua capability própria (`safety.<nome>_guard`, default ON)

## Filosofia

LLM **vai** alucinar. Volume baixo é normal, mas em farmácia uma alucinação custa:
- Receita: "esse não precisa de receita" sobre um tarja vermelha → multa ANVISA
- Preço: "R$ 12,50" sobre item de R$ 25 → prejuízo na venda + perda de confiança
- Disponibilidade: "temos sim" sobre produto inexistente → cliente vai presencial, frustra
- Frete: "frete grátis" sem regra → operador entrega sem cobrar

Defense in depth: prompt diz "não invente" + tool retorna verdade + **guard determinístico cruza** resposta vs verdade.

## Contrato do guard individual

```python
# Sync: pega regex + cruza com search_results (do cart._search_results_this_turn)
def detect_<nome>_issues(response: str, search_results: list[dict]) -> list[dict]
def build_correction_message(issues: list[dict]) -> str

# Async (delivery_guard precisa consultar tenant_shipping_rules):
async def detect_delivery_issues(response: str, *, tenant_id: str) -> list[dict]
```

Issue shape: `{"product": str, "expected": ..., "got": ..., "kind": "..."}`.

## Umbrella node (`safety_guard`)

```python
async def safety_guard(state) -> state:
    # Curto-circuito 1: modo pré-atendimento
    if not await capabilities.is_enabled(tenant_id, "inventory.track_stock"):
        return state  # passthrough

    response = state.final_response
    search_results = state.cart["_search_results_this_turn"]
    corrections = []
    issues_log = {}

    # Ordem de execução (severidade descendente):
    for cap_key, module in [
        ("safety.prescription_guard", prescription_guard),
        ("safety.price_guard",        price_guard),
        ("safety.availability_guard", availability_guard),
        ("safety.delivery_guard",     delivery_guard),
    ]:
        if not await capabilities.is_enabled(tenant_id, cap_key):
            continue
        issues = module.detect_*(response, search_results, tenant_id=...)
        if issues:
            corrections.append(module.build_correction_message(issues))
            issues_log[cap_key.split(".")[1]] = issues

    if not corrections:
        return state

    # Composição:
    #  - Availability tem precedência: REESCREVE a resposta inteira (produto fantasma)
    #  - Outros: PREPEND correção à resposta original
    if "availability" in issues_log:
        corrected = "\n\n".join(corrections)
    else:
        corrected = "\n\n".join([*corrections, response])

    log.warning("safety_guard.correction_applied", ...)
    return {**state, "final_response": corrected}
```

## Guards individuais

### `availability_guard`

Detecta produto **citado na resposta** que NÃO está em `search_results`. Cruza nomes com fuzzy matching (já consultado neste turno via `buscar_produto`).

Cobre tanto `vendedor` quanto `farmaceutico` em modo ERP — ambos têm `buscar_produto` e populam `cart._search_results_this_turn`. Em pré-atendimento o umbrella já curto-circuita antes.

Quando dispara: regenerou resposta inteira com "Não encontrei esse produto especificamente — me dá um momento que peço pro atendente confirmar".

### `price_guard`

Regex `R\$\s*\d+[.,]\d{2}` pega preços na resposta. Cruza com `search_results[].preco` — tolerância R$ 0,01.

Quando dispara: prepend "Vou conferir o valor com o atendente — pode estar desatualizado.".

### `prescription_guard`

Para cada produto consultado neste turno com `prescription_required=true` no catálogo: detecta frases ofensivas na resposta ("não precisa receita", "sem receita", "venda livre").

Quando dispara: prepend "Esse medicamento exige receita médica, posso anotar pra você apresentar no balcão na hora da retirada."

### `delivery_guard` (async)

Detecta menção a "frete grátis", "entrega gratuita" sem regra ativa em `tenant_shipping_rules` que justifique (above/range).

Quando dispara: prepend "Vou conferir o frete com o atendente."

## Invariantes

1. **Passthrough total em modo pré-atendimento.** Balcão humano valida tudo — guard só atrapalha.
2. **Cada guard em try/except independente.** Falha de um não impede os outros.
3. **Composição de correções**: availability domina (substitui), outras prepend em ordem.
4. **Fail-open**: erro no service de capabilities ou no guard → passthrough (segurança operacional > correctness em edge case).
5. **`search_results` é a fonte da verdade do turno.** Pego de `state.cart["_search_results_this_turn"]` — tool `buscar_produto` popula isso.

## Pontos de extensão

### Novo guard

1. `api/services/<nome>_guard.py`:
   ```python
   def detect_<nome>_issues(response, search_results, **kw) -> list[dict]: ...
   def build_correction_message(issues) -> str: ...
   ```
2. Migration: capability `safety.<nome>_guard` com `default_enabled=TRUE`, `category='safety'`.
3. `agents/nodes/safety_guard.py`: adicionar branch try/except chamando o módulo.
4. Documentar regras detectadas no `long_desc` do catálogo (UI no portal `PortalRecursos`).

### Tornar guard async (precisa DB)

Já feito em delivery_guard. Pattern: `async def detect_*` recebe `tenant_id`, faz query, retorna issues. No umbrella: `await detect_*(...)`.

### Substituir comportamento "prepend" por "replace"

Pra um guard que sempre quer reescrever a resposta (estilo availability), retorne única issue + `build_correction_message` que já é a resposta completa. No umbrella: marcar no `issues_log["meu_guard_replace"]` e adicionar à condição da composição.

## Regressões conhecidas / "Não fazer"

- **Não levantar exceção fora do try/except** — derruba a entrega da mensagem que JÁ está correta.
- **Não duplicar regex de preço** — `price_guard` é fonte única. Outros guards que precisem de preço devem importar.
- **Não usar `search_results` de turnos anteriores** — só do TURNO atual. Mistura quebra o cruzamento (preço mudou entre turnos).
- **Não rodar safety_guard no modo pré-atendimento.** O umbrella já faz curto-circuito; não tente "filtrar levemente" — vai gerar atrito sem benefício.
- **Não trocar a ordem (prescription → price → availability → delivery) sem revisar a lógica de composição.** Severidade orienta a composição.
- **Não aplicar correção quando `final_response` está vazio.** Curto-circuito no início do umbrella node.
- **Sugestão de nome de medicamento NUNCA é auto-correção.** O recurso `attendance.medication_name_suggestion` (tool `sugerir_nome_medicamento`, SPEC 03) só OFERECE candidatos — o agente tem que perguntar ("Você quis dizer X?") e esperar a confirmação do cliente. Trocar o nome sozinho = risco de dispensar o remédio errado. Candidatos de LLM/web são sempre verificados contra a ANVISA antes de chegarem ao cliente.

## Testes manuais úteis

```python
# tests/test_guards.py (criar se não existe)
from services.availability_guard import detect_hallucinations, build_correction_message

issues = detect_hallucinations(
    "Temos Tylenol Plus 750mg por R$ 15,90",
    search_results=[{"nome": "Tylenol 500mg", "preco": 12.0}],
)
assert issues  # produto fantasma
```
