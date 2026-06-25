# SPEC 03 — Tools (agente)

**Propósito**: Tools são funções que o LLM do skill pode chamar para consultar dados reais (catálogo, bula, pedido) ou produzir efeitos (criar pedido, registrar memória).

## Onde vive

```
agents/tools/
├── inventory.py        # catálogo, carrinho, finalizar_pedido (modo normal)
├── customer.py         # salvar_dados_cliente, consultar/cancelar/editar_pedido
├── balcao.py           # anotar_pedido_balcao (modo pré-atendimento)
├── bulario.py          # consultar_bula, consultar_bula_secao (ANVISA)
└── sales_extras.py     # cross-sell, shipping, PIX, memória de cliente
```

## Padrão de implementação

Toda tool é construída via **factory** que recebe contexto por closure e retorna um `@tool` LangChain:

```python
from langchain_core.tools import tool

def make_buscar_produto_tool(schema_name: str, tenant_id: str, cart: dict):
    @tool
    async def buscar_produto(nome: str) -> str:
        """
        Docstring importante — é o que o LLM lê pra decidir quando chamar.
        Descreve: o que faz, args (com tipos PT-BR amigáveis), retorno esperado.
        """
        # ... lógica usando schema_name/tenant_id/cart via closure
        return "texto retornado pro LLM"
    return buscar_produto
```

## Invariantes globais

1. **Tool retorna `str`** sempre (texto que o LLM vai ler). Pra dados estruturados, encode em texto formatado legível pelo LLM.
2. **Tool nunca lança exceção pro skill**. Captura tudo, retorna mensagem de erro humana.
3. **Mutação de estado in-place**: `cart`/`customer` recebidos por closure devem ser mutados (skill passa a mesma ref).
4. **Sem efeitos colaterais externos não-essenciais**: tool de consulta NÃO escreve em DB. Tool de mutação (finalizar_pedido, anotar_pedido_balcao, salvar_dados_cliente) escreve E retorna confirmação.
5. **Marker de sucesso reconhecível**: tools de fechamento retornam strings com prefixo único (ex.: `"PEDIDO_ANOTADO:OK"`, `"PEDIDO_CRIADO:..."`) — usado pelo worker pra detectar trigger de handoff determinístico.
6. **Tools que dependem de capability**: checar `await capabilities.is_enabled(tenant_id, "key")` dentro da tool (defesa em profundidade) — não confiar só no gating do skill.

## Tools por arquivo

### `inventory.py` — Modo normal (estoque real)

| Tool | Args | Side effect | Retorna |
|---|---|---|---|
| `buscar_produto(nome)` | nome livre | nenhum | Lista de matches com nome/apresentação/preço/`[INTERNO: N un]` (se track_stock ON). Cliente NÃO vê o bloco interno. |
| `adicionar_ao_carrinho(produto, quantidade)` | produto = nome retornado por buscar_produto | muta `cart` | Confirmação + subtotal atualizado |
| `remover_do_carrinho(produto)` | produto exato | muta `cart` | Confirmação |
| `atualizar_qtd_carrinho(produto, nova_quantidade)` | | muta `cart` | Confirmação |
| `finalizar_pedido(forma_pagamento, observacoes)` | enum: pix/cartao_*/dinheiro/boleto | escreve em `orders` + `order_items`; muta `cart.just_finalized=True`, `cart.last_order={...}`, **esvazia `items`** | `"✅ Pedido confirmado #..."` com recibo formatado |

`finalizar_pedido` falha **fechado** quando faltam campos obrigatórios (`sales_config.required_fields`) — incrementa `sales_attempts`, retorna mensagem de erro pedindo o campo.

### `customer.py` — Cadastro + ciclo de vida de pedido

| Tool | Args | Side effect | Retorna |
|---|---|---|---|
| `salvar_dados_cliente(campos)` | dict com chaves PT (`nome/cpf/cep/rua/...`) ou EN | upsert em `customers` + muta `customer` no state | Confirmação + estado atual |
| `consultar_cep(cep)` | CEP (com/sem hífen) | nenhum (read-only, ViaCEP) | Rua/bairro/cidade/UF p/ o agente confirmar + pedir número/complemento |
| `consultar_pedido(codigo)` | código (vazio = mais recente do telefone) | nenhum | Status amigável + itens + total |
| `cancelar_pedido(numero_pedido)` | código | marca `cancelled` em `orders` | Confirmação |
| `editar_pedido(numero_pedido, adicionar, remover, nova_observacao)` | | atualiza `orders` (apenas se status=pending) | Confirmação |

`_FIELD_TO_COLUMN` mapeia chaves PT/EN → colunas reais. Adicionar campo novo = atualizar esse dict.

**`consultar_cep`** chama o ViaCEP (`api/services/viacep.py`, GET público sem auth) para autocompletar o endereço assim que o cliente informa o CEP — o agente confirma rua/bairro/cidade e só coleta número/complemento, deixando o atendimento mais fluido. É **read-only** (invariante #4): NÃO grava no banco; o salvamento continua via `salvar_dados_cliente` após o cliente confirmar. A tool + o bloco de prompt `cep_lookup_block()` (em `prompts/commerce.py`) só entram no vendedor modo normal quando a farmácia coleta endereço — gate `_collects_address`: `cep` em `required_fields`, OU `ask_delivery`, OU capability `delivery.shipping_by_cep`. Falha fechada: ViaCEP fora do ar / CEP inexistente → a tool instrui o agente a pedir o endereço manualmente.

### `balcao.py` — Modo pré-atendimento

| Tool | Args | Side effect | Retorna |
|---|---|---|---|
| `anotar_pedido_balcao(itens, observacoes)` | itens = `[{"name", "qty"}]`, observacoes livre | escreve em `orders` com status `aguardando_balcao`; muta `cart.items` (price=0), `cart.subtotal=0`, `cart.just_finalized=True`, `cart.last_order={id, items, ...}` | `"PEDIDO_ANOTADO:OK"` (marker para o worker triggar handoff) |

A mutação completa de `cart.items` e `cart.last_order` é o que permite `send_order_summary` (capability `sales.order_summary_after_handoff`) montar o resumo do pedido em pré-atendimento. Sem ela, o snapshot ficaria vazio. O template do resumo detecta `all(preco == 0)` e omite automaticamente `{preco_*}` e a linha do Total.

### `bulario.py` — Base ANVISA (compartilhada)

| Tool | Args | Retorna |
|---|---|---|
| `consultar_bula(termo)` | termo livre | Metadata: nome, princípio ativo, fabricante, classe (top 5 matches) |
| `consultar_bula_secao(termo_medicamento, pergunta)` | medicamento + pergunta | Trecho real da bula da seção relevante (indicações, posologia, contraindicações, etc.) |

Estratégia: cache → local fuzzy (`bulario_repo`) → fallback ANVISA API com upsert.

⚠️ **Threshold de similaridade (`bulario_repo.MIN_SIMILARITY`, 0.45):** o operador
`%` do pg_trgm sozinho usa o default 0.30 (frouxo) — "buspirona" casava
"espironolactona" (sim=0.30) e o early-return de `get_or_fetch` nunca chegava à
ANVISA. `search_local` e `_fetch_rows_filtered` aplicam o corte explícito; abaixo
dele o resultado é descartado (força refetch). NÃO baixar o threshold sem validar
true-positives (`dipirona`, `losartana`, `paracetamol`).

**Alimentação manual (superadmin):** além do cold path disparado pelos agentes, o
painel `/admin/medicamentos` aba **Bulário ANVISA** permite popular o cache à mão:
- `POST /admin/medicamentos/bulario/consultar` `{termo, top_n}` — consulta manual
  de 1 termo (reusa `bulario_repo.get_or_fetch` com `AnvisaClient` próprio).
- `POST /admin/medicamentos/bulario/bulk` `{termos[], top_n}` — inserção em massa
  (até 100 termos, dedup, **sequencial** com 1 `AnvisaClient` compartilhado — a API
  da ANVISA é throttled, paralelismo agressivo toma rate-limit).
- `GET /admin/medicamentos/bulario/stats` — total/com-detalhe/com-bula (header).
- `GET /admin/medicamentos/bulario/{num_processo}` — detalhe + seções de bula
  extraídas (clique na linha abre o modal de conferência).

O bulário é cache da ANVISA: só dá pra inserir (consulta/massa) e consultar — sem
remoção nem edição (espelho regulatório). A base de referência, sim, é curada/editável.

### `referencia.py` — Guia de medicamentos de referência (curado, global)

| Tool | Args | Retorna |
|---|---|---|
| `consultar_medicamento_referencia(termo)` | princípio ativo OU marca | Mapeamento referência↔genérico (princípio ativo, marca original, forma, categoria) + seções clínicas **só se `status='active'`** |

Fonte: `public.medicamentos_referencia(+_secoes)`, ingerida do *Guia de Medicamentos
Genéricos* (2001) via `scripts/ingest_guia_genericos.py`. Bindada em `farmaceutico`,
`principio_ativo` e `genericos`. **Gate de curadoria determinístico** em
`referencia_repo.search_referencia` (mesmo `MIN_SIMILARITY` do bulário): seções
`pending`/`disabled` nunca chegam ao agente — só as revisadas/ativadas no painel
superadmin (`/admin/medicamentos`). A info clínica é COMPLEMENTO da bula ANVISA,
nunca a substitui; o prompt do farmacêutico fixa essa hierarquia.

⚠️ **Roteamento (regressão 2026-06-14):** os prompts das 3 skills posicionavam a
tool SÓ como mapeamento original↔genérico, então perguntas de **indicação**
("para que serve X?") nunca a invocavam — seções `indicacoes` ativas ficavam
inalcançáveis mesmo curadas. Corrigido: os prompts (e as docstrings de
`consultar_medicamento_referencia` e `consultar_bula_secao`) agora listam
indicação/"para que serve" como gatilho. Para dúvida clínica, ANVISA
(`consultar_bula_secao`) vem primeiro; a referência cobre como complemento.

**Telemetria (monitoramento):** cada chamada de `consultar_medicamento_referencia`
grava 1 linha em `public.medicamentos_referencia_consultas` via
`referencia_repo.log_consulta` (termo, medicamento casado, seções ATIVAS
devolvidas, tenant/skill — contexto threadado no factory só p/ telemetria). Log
defensivo: nunca quebra o turno. Exposto no painel superadmin `/admin/medicamentos`
aba **Consultas** (`GET /referencia/consultas` + `/referencia/consultas/stats`).
Complementa o Counter `saas_reference_clinical_used_total{secao}` (agregado) com
detalhe por consulta — auditável: termos sem match e "achou mas sem seção ativa".

### `medicamento_suggest.py` — Correção de nome ("Você quis dizer…?")

| Tool | Capability | Args | Retorna |
|---|---|---|---|
| `sugerir_nome_medicamento(termo)` | `attendance.medication_name_suggestion` (ON default) | termo como o cliente escreveu | Lista curta de nomes prováveis para o agente OFERECER (com confirmação) |

Entra no caminho **"não encontrei"** do `consultar_bula`: quando o cliente
escreve o nome com erro forte (abaixo do `MIN_SIMILARITY=0.45` do bulário e a
ANVISA também não casa), o agente chama esta tool em vez de só desistir.

Pipeline (em `services/medicamento_suggest.py`), determinístico-first:
1. **fuzzy nas bases reais** — `word_similarity`/`%` (pg_trgm) sobre
   `public.medicamentos_anvisa` e `public.medicamentos_referencia` (ambas já com
   índice GIN trigram). Piso `_FUZZY_FLOOR=0.30` (mais frouxo que o do bulário
   DE PROPÓSITO — aqui queremos os quase-matches que o bulário descartou).
2. **LLM leve** normaliza a grafia torta — roda no **provedor do próprio
   tenant** (`load_tenant_llm_config` + factory `get_llm`/`get_llm_for_tenant`),
   então respeita BYOK / OpenAI / Gemini / Ollama. Só dispara se a camada 1 não
   encheu `max_candidates` (economia de custo).
3. **web search nativo do Claude** (`web_search_20250305`, opcional por config
   `enable_web_search`) — último recurso, específico da Anthropic. Usa a chave
   Anthropic disponível (a do tenant se BYOK-Anthropic, senão a da plataforma);
   sem nenhuma chave Anthropic, **pula a web** e as camadas 1+2 cobrem. Usa o SDK
   `anthropic` direto (não há server-tool na factory; usage logada em
   `medicamento_suggest.web.usage`). Degrada com elegância se indisponível.

⚠️ **INVARIANTE DE SEGURANÇA:** candidatos das camadas 2/3 são SEMPRE verificados
contra a ANVISA (`bulario_repo.get_or_fetch`) antes de devolvidos — nunca se
confia na grafia do LLM/web. E o agente apenas SUGERE (pergunta "Você quis dizer
X?"); **nunca substitui o nome sozinho** — confirmação do cliente é obrigatória
(medicamento errado é risco clínico). Bindada em `farmaceutico` e
`principio_ativo`. **Não cria tabela** — usa as bases reais já existentes (o CSV
`dados_medicamentos.csv` é registro de correlatos/dispositivos, NÃO de
medicamentos — não usar como dicionário).

### `sales_extras.py` — Tools opcionais por capability

| Tool | Capability | Args | Retorna |
|---|---|---|---|
| `recomendar_complementos(produto)` | `sales.cross_sell` | nome | Sugestões via `product_relations` (filtra alergias) |
| `calcular_frete(cep, subtotal)` | `delivery.shipping_by_cep` | cep, subtotal | Valor + prazo + "frete grátis" se aplicável (modo CEP **ou** distância) |
| `gerar_link_pix(numero_pedido, valor_total)` | `payments.pix_asaas` | numero, valor | Copia-cola PIX via Asaas (pede CPF se falta) |
| `registrar_alergia(substancia)` | `attendance.customer_memory` | | Persiste em `customers.allergies` |
| `registrar_medicamento_continuo(nome, dose, frequencia)` | `attendance.customer_memory` | | Persiste em `customers.continuous_meds` |
| `registrar_preferencia(chave, valor)` | `attendance.customer_memory` | | Persiste em `customers.preferences` |

## Pontos de extensão

### Adicionar nova tool

1. Em `agents/tools/<arquivo apropriado>.py`: implementa factory `make_<tool>(...)` + função decorada `@tool`.
2. Docstring **clara e completa** — o LLM usa ela pra decidir invocação.
3. No skill que vai usar: bind no array de `tools` passado ao `run_skill` ou `bind_tools`.
4. Atualizar prompt do skill mencionando a tool e quando usar.
5. (Se capability-gated) Checar `is_enabled` no skill antes de incluir + dentro da tool como defesa.

### Adicionar campo aceito em `salvar_dados_cliente`

Atualizar `_FIELD_TO_COLUMN` em `customer.py`. Se for coluna nova, criar migration adicionando coluna em `customers` (via `create_tenant_schema` + função de upgrade para tenants existentes).

### `calcular_frete` — dois modelos de precificação

A tool decide o modelo pela linha do tenant em `public.tenant_shipping_origin`
(`mode`):

- **`cep_table`** (default, legado): faixas de CEP → valor fixo
  (`public.tenant_shipping_rules`). Match pela faixa **mais específica** (menor
  amplitude `cep_end - cep_start`) que contém o CEP — corrige o bug histórico em
  que uma faixa larga "capital" mascarava uma faixa estreita "centro".
- **`distance`**: mede a distância origem↔destino e aplica a **menor** faixa de
  raio (`public.tenant_shipping_distance_tiers`) cujo `max_distance_km` cobre a
  distância. Acima da maior faixa → "fora da área de entrega" (NÃO inventa valor).
  Sem distância medível/sem faixa → **fallback** para `cep_table`. A **fonte da
  distância** é `tenant_shipping_origin.distance_source`:
    - `haversine` (default, grátis): geocoda origem+destino (BrasilAPI v2 →
      fallback AwesomeAPI, cache 24h) e mede linha reta.
    - `google`: rota REAL de rua via Google Distance Matrix
      (`geocoding.google_distance_km`, chave **da plataforma**
      `settings.google_maps_api_key`, cache 24h por origem+CEP). Sem chave ou
      falha da API → cai para haversine automaticamente.

**Anti-promessa-errada (integra com o `delivery_guard`, SPEC 10):** quando recebe
`cart`, a tool grava o orçamento computado em `cart["_shipping_quote_this_turn"]`
(`{kind, valor, free, free_threshold, distance_km, ...}`). O `delivery_guard`
cruza a resposta do agente contra esse orçamento — pega "frete grátis" prometido
abaixo do mínimo e valor/entrega afirmados quando o CEP está fora de área.

Portal: tela **Frete & Entrega** (`PortalEntregas.tsx`) tem seletor de modo +
origem + faixas. Endpoints: `/portal/shipping-origin` (GET/PUT, PUT geocoda),
`/portal/shipping-tiers` (CRUD), `/portal/shipping-rules` (CRUD legado).

### Adicionar provider de PIX além de Asaas

`api/services/payments_asaas.py` é o cliente atual. Para abstrair:
1. Extrair interface em `api/services/payments_provider.py`.
2. Implementar novo provider (ex. `payments_mercadopago.py`).
3. Resolver provider pelo `tenant_capabilities.payments.<provider>.config`.

## Regressões conhecidas / "Não fazer"

- **Não retornar quantidade de estoque ao cliente.** Use `[INTERNO: N un]` que só o LLM vê (instrução em `vendedor._SYSTEM` REGRA 10). Cliente vê só "tem" ou "não tem".
- **Não confiar no `finalizar_pedido` para "sucesso = pedido criado"** sem verificar `cart.just_finalized` no worker. O LLM pode ter visto erro e ainda gerado mensagem de sucesso.
- **Não criar tool nova fora de uma factory.** Tools sem closure não conseguem acesso ao tenant/schema/cart.
- **Não fazer `tool.invoke` (síncrono) num evento async.** Sempre `await tool.ainvoke(args)`.
- **Não logar PII do cliente em plaintext** (CPF, endereço completo). Logue prefixo + flags.
- **Não inventar variantes de embalagem em buscar_produto** — `vendedor._SYSTEM` REGRA 11. Tool retorna o que existe; LLM não pode oferecer "blister/frasco/ampola" que não vieram.
- **Não deixar `sugerir_nome_medicamento` auto-corrigir.** A tool SUGERE; o agente tem que perguntar e esperar confirmação. Trocar o nome sozinho = risco de dispensar o remédio errado. E nunca usar `dados_medicamentos.csv` como dicionário de nomes (é correlatos/dispositivos, não medicamentos).
- **Não declarar tool com `args_schema` SEM nenhum campo.** Um schema de `OBJECT` com `properties` vazio é aceito por Anthropic/OpenAI, mas o **Gemini rejeita em runtime** (`400 INVALID_ARGUMENT ... parameters.properties should be non-empty`). Tool sem payload (ex.: `encerrar_atendimento`) deve declarar ao menos um campo opcional inócuo. Mordeu o `_EndInput` em `flow_control.py` quando habilitamos BYOK Gemini.

## Loop tool-calling (pattern)

Em `_base._invoke_with_tools` e `vendedor` inline:

```python
llm_with_tools = llm.bind_tools(tools)
for i in range(max_iters):
    response = await llm_with_tools.ainvoke(messages)
    if not response.tool_calls:
        final_text = _extract_text(response.content)
        break
    messages.append(response)
    for tc in response.tool_calls:
        result = await tool_map[tc["name"]].ainvoke(tc["args"])
        messages.append(ToolMessage(content=str(result), tool_call_id=tc["id"]))
else:
    # excedeu max_iters → força resposta sem tools
    response = await llm.ainvoke(messages)
    final_text = _extract_text(response.content)

# Fallback se ficou só com tool_calls sem texto:
if not final_text.strip():
    messages.append(HumanMessage(content="Responda agora em texto curto..."))
    response = await llm.ainvoke(messages)
    final_text = _extract_text(response.content)
```

Trace records de tool_calls (cada iteração):
```python
{"iter": int, "name": str, "args": dict, "result_preview": str (300 chars), "error": str?}
```
