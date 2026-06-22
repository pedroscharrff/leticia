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
- **`sales.stock_check` (existe catálogo)** — passthrough total quando OFF (pré-atendimento, sem catálogo). **NÃO** gatear em `inventory.track_stock`: ele é só "quantidade autoritativa" (ERP) e está OFF no modo Sheets/CSV, que TEM catálogo. Gatear em `track_stock` deixava o modo Sheets (default de todos os tenants hoje) sem nenhum guard — o farmaceutico afirmava "temos X" pela bula e ninguém cruzava com o catálogo (regressão real, jun/2026).
- Cada guard tem sua capability própria (`safety.<nome>_guard`, default ON)

> **Os três modos e o gate certo** (cf. SPEC 04 §modos): pré-atendimento = `sales.stock_check` OFF (sem catálogo → passthrough). Sheets/CSV = `stock_check` ON + `track_stock` OFF (catálogo existe, quantidade não-autoritativa → guards rodam; `buscar_produto` presume disponível, então `availability_guard` só flagga "produto inventado", nunca "sem estoque"). ERP = ambos ON (catálogo + quantidade real → tudo roda, incluindo "sem estoque").

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
    # Curto-circuito 1: pré-atendimento (sem catálogo). Gate = sales.stock_check,
    # NÃO inventory.track_stock (que deixaria o modo Sheets sem guards).
    if not await capabilities.is_enabled(tenant_id, "sales.stock_check"):
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

Cobre tanto `vendedor` quanto `farmaceutico` sempre que há catálogo (Sheets OU ERP) — ambos têm `buscar_produto` e populam `cart._search_results_this_turn`. Em pré-atendimento (sem catálogo) o umbrella já curto-circuita antes.

Quando dispara: regenerou resposta inteira com "Não encontrei esse produto especificamente — me dá um momento que peço pro atendente confirmar".

> ⚠️ **Ponto cego coberto pelo force-recall (abaixo).** `detect_hallucinations` curto-circuita quando `search_results` está VAZIO (`if not search_results: return []`). Logo ele NÃO pega o pior caso: a LLM fraca que afirma "temos" **sem nunca chamar `buscar_produto`** neste turno. Esse caso é fechado ANTES, no runtime, pela **força-busca de estoque** — que força a tool e repopula `search_results`, deixando este guard como segunda linha.

## Força-busca de estoque (runtime, andaime weak-LLM)

Vive em `agents/runtime.py` (`StockRecall` + `_maybe_force_stock_search`), NÃO em `services/*_guard.py` — é um **andaime de tool-calling**, não um guard pós-texto, e roda dentro do tool-loop (após `post_loop_hook`, antes do empty-text fallback).

**Problema**: medição em prod (`llm/model_tier.py`) — `gemini-*-flash-lite` fica com ~82% dos turnos sem chamar tool. Havendo catálogo, isso vira "temos esse remédio" sem ter consultado o catálogo. O prompt (`stock_check_block`, SPEC 02) é ignorado pela LLM fraca; o `availability_guard` curto-circuita porque não houve busca. Nada segura.

**Como funciona** (só quando fornecido `stock_recall` — o skill só fornece para LLM **fraca** (`needs_tool_scaffolding`) **E** existe catálogo (`sales.stock_check` ON)):
1. Após o loop, checa `availability_guard.affirms_or_offers_availability(final_text)` — combina DOIS sinais:
   - `has_unverified_affirmation`: afirmação direta ("temos", "tem sim", "em estoque"…) sem negação.
   - `has_presentation_offer`: **oferta de apresentação** para escolher/comprar ("a dipirona vem em comprimido ou gotas, qual prefere?") — exige token de forma (comprimido/gotas/mg/apresentação…) **E** convite de compra (qual prefere/posso anotar/quantas…), sem negação. Esse vetor não tem "temos", então escapava do nº1 — era o farmaceutico enumerando apresentações DA BULA como se fossem estoque (caso real medido). Os dois exigem regex; fonte única no `availability_guard`.
2. **Suprime** se `buscar_produto` já rodou no turno (guard cobre) OU se uma tool de carrinho/pedido rodou (`suppress_tools` — item já validado, evita atrito em reafirmação de fechamento).
3. Senão: re-injeta uma `HumanMessage` forçando `buscar_produto`, executa a(s) busca(s), e regenera a resposta com instrução de usar APENAS o resultado. Se o modelo não buscar nem forçado → resposta segura ("deixa eu confirmar a disponibilidade…").
4. Fail-open: qualquer exceção no andaime é logada e ignorada (não derruba a entrega).

**Gating por skill**:
- `farmaceutico` → `run_skill(..., verify_stock_affirmation=has_catalog)` (`has_catalog = sales.stock_check`). `run_skill` só ativa quando `_scaffold` E há `buscar_produto` bindada.
- `vendedor` (modo normal) → passa `StockRecall(suppress_tools=("adicionar_ao_carrinho","finalizar_pedido"))` direto ao `run_tool_loop`, gated por `_v_scaffold and not use_preattendimento`.

**Defense-in-depth**: a busca forçada repopula `cart._search_results_this_turn`, então o `safety_guard` downstream ainda cruza a resposta regenerada — se o modelo insistir em afirmar um item sem match, o `availability_guard` reescreve.

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

1. **Passthrough total só em pré-atendimento (sem catálogo = `sales.stock_check` OFF).** Balcão humano valida tudo — guard só atrapalha. Em modo Sheets (catálogo, `track_stock` OFF) os guards RODAM. Não confundir "sem `track_stock`" com "pré-atendimento".
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
- **Não tratar "sem busca neste turno" como seguro no `availability_guard`.** O guard pós-texto curto-circuita com `search_results` vazio DE PROPÓSITO (sem busca não há o que cruzar). O caso "afirmou sem buscar" é responsabilidade do **force-recall no runtime** (`StockRecall`), não do guard. Não tente "consertar" o guard pra cobrir isso — ele cruza, não força.
- **Não ligar o force-recall para modelo forte.** `stock_recall` só é fornecido quando `needs_tool_scaffolding` é True (Gemini/weak/local). Claude/GPT forte: caminho byte-idêntico, sem re-prompt extra.
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
