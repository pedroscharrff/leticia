# Resumo do pedido — enriquecimento com pagamento e endereço

> Documento de feature. Registra **o que foi feito**, **por que**, **como o dado
> flui** e **como evoluir**. Complementa SPEC 09 (§Pre-handoff offers + order
> summary) e SPEC 04 (capability `sales.order_summary_after_handoff`).

## 1. Objetivo

O resumo do pedido enviado ao cliente logo após a transferência para o atendente
humano passou a incluir, **quando houver**:

- a **forma de pagamento** escolhida no fechamento, e
- o **endereço de entrega** do cliente.

Tudo de forma **determinística** — os dados saem de campos já coletados/validados
no atendimento, montados por código, sem pedir ao LLM para "escrever o resumo".
Isso elimina alucinação de endereço/forma de pagamento e mantém a mensagem
estável.

## 2. Princípio de design: determinístico > estatístico

O resumo NUNCA é gerado pelo LLM. `build_summary_text` é uma função pura que
recebe o `cart` (já com os dados) + o template do tenant e monta o texto. O agente
não participa da montagem do resumo — ele só conduz a conversa que **preenche** os
dados (forma de pagamento via `finalizar_pedido`, endereço via
`salvar_dados_cliente`/`consultar_cep`).

Consequência prática: se o dado existe, aparece; se não existe, a linha some.
Não há "às vezes o bot esquece de colocar o endereço".

## 3. Fluxo do dado (de onde sai cada campo)

```
                      finalizar_pedido (inventory.py, modo normal)
                          │  grava cart.last_order.payment = "PIX" | "Dinheiro" | ...
                          ▼
cliente ──► agente ──► cart.last_order ──┐
                          ▲              │
        salvar_dados_cliente /           │   _cart_for_summary(final_state)   (celery_app.py)
        consultar_cep (ViaCEP)           │   • payment  ← last_order.payment ("balcao" → "")
        grava customer.{street,...}      │   • address  ← format_customer_address(customer)
                          │              ▼
                    final_state.customer ──► build_summary_text(cart, cfg)   (order_summary.py)
                                                 • renderiza "{payment_label}: {payment}"  (se != "")
                                                 • renderiza "{address_label}: {address}"  (se != "")
                                                 ▼
                                         send_order_summary ──► WhatsApp
```

| Campo no resumo | Fonte primária | Montado em | Omitido quando |
|---|---|---|---|
| Forma de pagamento | `cart.last_order.payment` (set por `finalizar_pedido`) | `_cart_for_summary` | pré-atendimento (sentinela `"balcao"` zerado), ou sem forma definida |
| Endereço | `customer.{street, street_number, complement, neighborhood, city, state, cep}` | `format_customer_address()` em `sales_config.py` | cliente sem endereço cadastrado (ex.: retirada) |

## 4. Arquivos alterados

| Arquivo | Mudança |
|---|---|
| `api/services/order_summary.py` | `build_summary_text` renderiza linhas de pagamento e endereço (determinísticas, "quando houver"); `_DEFAULTS` ganha `show_payment`/`payment_label`/`show_address`/`address_label`. |
| `api/services/sales_config.py` | Novo `format_customer_address(customer)` público (wrapper de `_format_known_address`), reusado pelo resumo. |
| `api/workers/celery_app.py` | `_cart_for_summary` popula `payment` (zerando o sentinela `"balcao"`) e `address` (do cadastro do cliente). |
| `api/db/migrations/070_order_summary_payment_address.sql` | Estende `config_schema` + `default_config` da capability 044 com os 4 campos novos. Idempotente (`UPDATE ... WHERE key=...`). |
| `api/routers/payments.py` | Modelos `OrderSummaryConfig{Out,In,Preview}In` + defaults + merge + preview ganham os 4 campos. Constante `ORDER_SUMMARY_FIELDS` vira fonte única dos loops/merges. Preview sample mostra pagamento + endereço. |
| `frontend/src/api/payments.ts` | Tipos `OrderSummaryConfig`/`Patch` com os 4 campos. |
| `frontend/src/pages/PortalResumoPedido.tsx` | Toggles "Mostrar pagamento"/"Mostrar endereço" + inputs de rótulo + textos de ajuda. |

## 5. Configuração (capability `sales.order_summary_after_handoff`)

Novos campos no `config`/`default_config` (mig 070):

| Campo | Tipo | Default | Efeito |
|---|---|---|---|
| `show_payment`  | bool   | `true`         | Liga/desliga a linha de pagamento. |
| `payment_label` | string | `*Pagamento*`  | Rótulo da linha (formatação WhatsApp permitida). |
| `show_address`  | bool   | `true`         | Liga/desliga a linha de endereço. |
| `address_label` | string | `*Entrega*`    | Rótulo da linha. |

Config esparsa (SPEC 04): só campos != default são gravados como override; o resto
herda do catálogo. Tenants que já customizaram o template herdam os novos campos
automaticamente.

## 6. Exemplos renderizados

**Modo normal (com pagamento + endereço):**
```
📋 *Resumo do seu pedido:*
• 2x Dipirona 500mg — R$ 15,00
• 1x Tylenol — R$ 18,90
*Total*: R$ 33,90
*Pagamento*: PIX
*Entrega*: Av. Paulista, 1000, Bela Vista, São Paulo/SP, CEP 01310-100
Um atendente vai confirmar disponibilidade e finalizar. 😊
```

**Pré-atendimento (sem preço, sem pagamento, com endereço):**
```
📋 *Resumo do seu pedido:*
• 2x Dipirona 500mg
• 1x Soro fisiológico
*Entrega*: Rua X, 10, Centro, Campinas/SP
Um atendente vai confirmar disponibilidade e finalizar. 😊
```

**Retirada (sem endereço) com pagamento desligado:** mostra só itens + total.

## 7. Invariantes / não-fazer

- **Não gerar o resumo pelo LLM.** A montagem é determinística por contrato; o
  agente só preenche os dados-fonte. Voltar a delegar ao modelo reabre alucinação.
- **`"balcao"` não é forma de pagamento** — é sentinela do pré-atendimento
  (`anotar_pedido_balcao`). `_cart_for_summary` zera antes de exibir.
- **"Quando houver" = string vazia ⇒ linha omitida.** Não imprimir
  "Pagamento: " nem "Entrega: " vazios.
- **Nunca levantar exceção** no caminho do resumo (handoff já saiu) — vale também
  para `format_customer_address` (envolto em try/except no worker).

## 8. Melhorias futuras (backlog)

1. **Distinguir ENTREGA × RETIRADA.** Hoje o endereço aparece sempre que o cliente
   *tem* um cadastrado, mesmo em pedido de retirada. `finalizar_pedido` não captura
   a escolha entrega/retirada. Proposta: adicionar `delivery_method`
   (`entrega`/`retirada`) ao `finalizar_pedido` → gravar em `cart.last_order` →
   gatear `address` por `delivery_method == "entrega"`.
2. **Cálculo de frete no resumo.** Reaproveitar `calcular_frete` (capability
   `delivery.shipping_by_cep`) para incluir valor + prazo de entrega no resumo,
   também determinístico (gravar o frete escolhido em `last_order`).
3. **Preview real com endereço.** O `/preview` com `session_key` real ainda não
   busca o endereço do cliente (só itens do cart). Buscar `customers` pela sessão
   para um preview fiel.
4. **Persistir o snapshot do resumo** em `orders` (ou `agent_traces`) para auditoria
   do que exatamente foi enviado ao cliente.
5. **Telefone/observações** como linhas opcionais, no mesmo padrão determinístico.
