# PRD — SaaS Farmácia (Atendimento Inteligente)

> Product Requirements Document. Fonte da verdade sobre **o que é**, **para quem** e **o que pode/não pode**.
> Atualize sempre que adicionar uma feature significativa.

---

## 1. One-liner

Plataforma SaaS que automatiza o atendimento de farmácias no WhatsApp com agentes de IA especializados (farmacêutico, vendedor, recuperação de clientes), integrando catálogo/estoque do PDV, validando segurança regulatória (ANVISA) e transferindo para atendente humano quando preciso.

---

## 2. Quem usa

### Persona 1 — Dono da farmácia (operador do tenant)
- Pequeno/médio varejista farmacêutico (1 a 10 lojas)
- Usa WhatsApp Business como canal principal de pedidos
- Quer reduzir tempo de atendente humano em dúvidas repetidas e captura de pedidos
- Configura o bot no portal: persona, ofertas, capabilities, integração com gateway de WhatsApp e CRM/PDV (ClickMassa, TalkFarma, etc.)
- **Não é técnico** — precisa de UI clara, defaults seguros, "ligar/desligar" granular

### Persona 2 — Atendente humano (operador do balcão)
- Recebe ticket transferido pelo bot quando: cliente pede humano, agente desiste, pedido fica pronto pra fechar
- Vê histórico do bot na conversa (transparência total)
- Quer que o bot **não compita** com ele depois da transferência (auto-pause)

### Persona 3 — Cliente final (paciente)
- Usa WhatsApp pra perguntar disponibilidade, preço, tirar dúvida farmacêutica, fazer pedido
- Não sabe que existe IA — pra ele é só "a farmácia"
- Pode mandar áudio, foto de receita, lista escrita à mão
- Espera resposta rápida (< 30s), em linguagem natural

### Persona 4 — Admin da plataforma (Anthropic-side)
- Onboarding de novos tenants
- Gerencia catálogo de skills/capabilities globais
- Acompanha métricas agregadas (msgs/mês, latência, custo LLM por tenant)

---

## 3. Por que existe

**Problema**: farmácia de bairro perde venda fora do horário comercial, vive com WhatsApp lotado de "tem dipirona?", e não consegue contratar farmacêutico 24/7. Soluções de chatbot tradicionais (decision tree, NLP de palavra-chave) não escalam pra linguagem do cliente real.

**Solução**: agente conversacional que entende sintoma, consulta estoque real, conhece bula ANVISA, captura pedido completo com dados do cliente, e transfere pro humano só quando faz sentido.

---

## 4. O que o produto faz (capacidades atuais)

### 4.1. Atendimento conversacional multi-agente

Cada mensagem entra em um grafo de agentes que **se passam a bola** internamente. Cliente vê uma única "persona" da farmácia.

Skills (especialidades) disponíveis:

| Skill | Quando age | Plano mínimo |
|---|---|---|
| `saudacao` | Primeiro contato, "oi", mensagem ambígua | basic |
| `farmaceutico` | Sintomas, dúvidas clínicas, posologia, bulas (com tool ANVISA) | basic |
| `vendedor` | Cliente nomeou produto, quer preço, montar carrinho, fechar pedido | pro |
| `principio_ativo` | "Qual o princípio ativo do Tylenol?" | pro |
| `genericos` | "Tem alternativa mais barata?" | pro |
| `recuperador` | Reengajamento de clientes inativos (jobs proativos) | enterprise |
| `guardrails` | Off-topic, emergência médica, conteúdo impróprio (sempre ON) | — |

### 4.2. Dois modos de operação (escolha do dono)

Controlado pela capability `inventory.track_stock`:

| Modo | Quando usar | Comportamento |
|---|---|---|
| **ERP completo** (ON) | Farmácia com PDV integrado e catálogo curado | Bot consulta estoque real, preço, finaliza pedido no banco. Capabilities de safety validam preço/tarja/disponibilidade. |
| **Pré-atendimento** (OFF) | Farmácia de bairro sem ERP integrado | Bot só **anota** o pedido (itens + dados do cliente) e **transfere** pro balcão humano finalizar. Sem catálogo autoritativo. |

Mudar de modo é flip de capability no portal — sem deploy.

### 4.3. Multimodal (áudio + imagem)

Cliente manda áudio → Whisper transcreve (Groq por default).
Cliente manda foto de receita ou produto → Claude Vision descreve.
Tudo isso vira `current_message` antes do orchestrator decidir o skill.

### 4.4. Integrações de canal

- WhatsApp Cloud API (Meta) — adapter nativo
- Z-API (popular no Brasil) — adapter nativo
- Telegram — adapter nativo
- **Broker universal** — qualquer gateway que mande JSON. Tenant configura `inbound_field_map` (extrair phone/mensagem do payload), `reply_mode` (forward pra reply_url ou response síncrono), `reply_body_template`. Sem código.

### 4.5. Handoff para atendente humano

Provider hoje: **ClickMassa / TalkFarma**. Outros pluggáveis pelo dispatcher.

Disparado por:
- Agente colocou `[[ESCALATE]]` na resposta
- Cliente mandou keyword (`atendente`, `humano`, `balcão`, custom)
- Tool `finalizar_pedido` ou `anotar_pedido_balcao` rodou com sucesso (transferência automática pós-pedido)

Após handoff: **IA pausa automaticamente** por N minutos (`handoff_pause_minutes` por canal, default 4h) — evita bot atropelar o atendente humano.

### 4.6. Capabilities (feature flags por tenant)

Mais de 15 capabilities configuráveis no portal sem deploy. Categorias:

- **Safety** (default ON, regulatório): `availability_guard`, `price_guard`, `prescription_guard`, `delivery_guard`
- **Sales**: `stock_check`, `cross_sell`, `pre_handoff_offers`
- **Delivery**: `shipping_by_cep`
- **Payments**: `pix_asaas`
- **Attendance**: `customer_memory`, `interactive_buttons`
- **Inventory**: `track_stock`

Cada capability tem `default_config` overridável pelo tenant.

### 4.7. Persona configurável

Por tenant: `agent_name`, `tone`, `language`, `pharmacy_name`, `persona_bio`, `formality`, `emoji_usage`, `response_length`, `conversation_playbook`, `custom_instructions`. Tudo via portal.

Tenant pode também **substituir** o system prompt de qualquer skill (`tenant_skill_prompts.system_prompt`) ou **acrescentar** instruções (`extra_instructions`).

### 4.8. Memória de longo prazo do cliente (capability)

`customers` table per-tenant guarda: alergias, medicamentos contínuos, preferências, segmento (VIP/inadimplente/novo), LTV, total de pedidos, última compra. Bot usa isso pra personalizar (capability `attendance.customer_memory`).

Tools registram automaticamente: `registrar_alergia`, `registrar_medicamento_continuo`, `registrar_preferencia`.

### 4.9. Bula ANVISA

Base regulatória **global** (compartilhada entre tenants) em `medicamentos_anvisa` + `bula_secoes`. Tools:
- `consultar_bula(termo)` — metadata (princípio ativo, fabricante, classe)
- `consultar_bula_secao(termo, pergunta)` — trecho real da bula (posologia, contraindicações, interações, etc.)

Atualizada via scripts (`scripts/backfill_top_meds.py`, `scripts/test_anvisa.py`).

### 4.10. Jobs proativos

- `recover_abandoned_carts` (hourly) — capability-gated, manda lembrete pra carrinhos com itens não finalizados
- `nudge_continuous_refill` (daily) — capability-gated, lembra clientes de medicamento contínuo a recomprar

### 4.11. Pagamentos

- **Asaas** integrado — gera link PIX no chat via tool `gerar_link_pix` (capability `payments.pix_asaas`)
- Webhook de confirmação atualiza pedido automaticamente

### 4.12. Billing da plataforma

- **Stripe** e **Asaas** como provedores
- Planos: `basic` (R$ 97), `pro` (R$ 297), `enterprise` (R$ 697)
- Limites: msgs/mês, tokens/mês, products_max, customers_max
- Usage counter em Redis (incremento por mensagem aceita)
- Trial 7 dias default
- Suspensão automática em `past_due` (middleware retorna 402 no webhook)

### 4.13. Portal admin do tenant

UI React (Vite + TypeScript) com 25+ páginas. Principais:

- `Dashboard` — métricas de conversas, latência, mensagens
- `PortalSkills` — ligar/desligar e configurar skills
- `PortalRecursos` — capabilities catalog (UI didática por categoria)
- `PortalPersona` — persona + custom prompts por skill
- `PortalLLMConfig` — provider/model por papel, BYOK
- `PortalBroker` — integrations universais (mapping + reply)
- `PortalCanais` — WhatsApp Cloud, Z-API, Telegram
- `PortalEstoque` — catálogo (manual, CSV, XLSX, Google Sheets)
- `PortalClientes` / `PortalClienteDetalhe` — CRM + memória
- `PortalPedidos` — pedidos em aberto/fechados
- `PortalOfertas` — ofertas vigentes (com mídia opcional, pré-handoff)
- `PortalTraces` — debug por turno do agente
- `PortalLogs` — histórico de conversas
- `PortalBilling` / `PortalPagamentos` — financeiro

### 4.14. Observabilidade

- Logs estruturados JSON via structlog
- Prometheus metrics em `/metrics`
- `agent_traces` persistido por turno com: nodes executados, latência por node, tool calls + result preview, erros com stack
- Portal Traces mostra timeline visual

---

## 5. Limites e exclusões explícitas

### O que o bot NÃO faz (por design)

- Não diagnostica nem prescreve (sempre sugere consulta médica em casos sérios)
- Não inventa preço, estoque, disponibilidade, prazo — só fala o que veio de tool
- Não confirma pedido sem chamar tool de criação (trava em `farmaceutico.py` e `vendedor.py`)
- Não compete com atendente humano após handoff (auto-pause)
- Não fala "vou te passar para o farmacêutico/vendedor" — para o cliente é uma única persona

### O que o produto NÃO é

- Não é assistente médico — não substitui consulta
- Não é PDV — integra com o PDV do cliente via webhook/CSV/Sheets
- Não é gateway de WhatsApp — depende de Z-API/WA Cloud/etc.
- Não é CRM completo — tem memória de cliente, mas não substitui Salesforce/RD

### Restrições regulatórias

- Medicamentos com tarja (`prescription_required=true`) NÃO podem ser vendidos sem aviso de receita (guard determinístico)
- Não pode afirmar dose/posologia/interação sem consultar `consultar_bula_secao` (regra de prompt do farmaceutico)

---

## 6. Métricas de sucesso

### Para o cliente final
- Tempo médio de primeira resposta < 30s
- Resolução sem humano > 60% (skill_used != "handoff")
- Taxa de pedido bem-sucedido (entrou em `orders` e cliente confirmou) > 80% das tentativas

### Para o dono da farmácia
- % de atendimento fora do horário comercial servido pelo bot
- Receita gerada via pedidos do bot (ofertas pré-handoff convertidas)
- Redução de tickets enviados pro atendente humano

### Para a plataforma
- MRR por plano
- Custo médio LLM por conversa (deve ficar abaixo do preço do plano)
- Latência P95 do grafo < 5s
- Cache hit rate Anthropic > 80% (sinaliza prompt bem dividido stable/volatile)

---

## 7. Roadmap visível no código (próximos)

Itens identificados que aparecem como "futuro" / TODO / capability planejada mas ainda OFF:

- **Time-of-day para refill nudge** — hoje roda sempre que beat acorda (TODO em `celery_app.py`)
- **Vertex Cached Content API** (Google) — explícito como "skipped for now" em `llm/caching.py`
- **Provider de handoff além de ClickMassa** — dispatcher já está abstrato, falta implementar Talk-Anybody, Zenvia, etc.
- **MFA / TOTP para `tenant_users`** — coluna `mfa_secret` existe, falta UI/fluxo
- **Mais canais** — Instagram, web widget já estão no CHECK do `channel_type` mas sem adapter
- **Multi-tenancy stronger isolation** — schemas resolvem grande parte mas RLS no Postgres seria mais robusto

---

## 8. Decisões arquiteturais relevantes (ADR-style breve)

| Decisão | Por quê |
|---|---|
| LangGraph em vez de orquestração manual | Roteamento condicional + state tipado + observabilidade nativa |
| Schema-per-tenant em vez de coluna `tenant_id` em todas as tabelas | Isolamento real, fácil de backup individual, migrations atômicas por tenant |
| Capability flags em vez de planos rígidos | Operador desliga o que não quer; plano só limita o ceiling |
| Persona única externalmente, multi-agente internamente | Cliente não precisa entender "agente de vendas" vs "farmacêutico" |
| Safety guards determinísticos pós-LLM | LLM alucina; regex/check de catálogo não. Defesa em profundidade. |
| Broker universal além dos adapters | Não escalamos escrevendo Python para cada gateway novo |
| Prompt caching explícito por provider | Anthropic exige marker; custo cai 90% no input quando feito direito |
| Modo pré-atendimento (track_stock OFF) | A maioria das farmácias pequenas não tem ERP integrado — temos que servir esse mercado também |
| Pause automático pós-handoff | Bot atropelando atendente humano é o pior bug possível pra reputação |

---

## 9. Não-objetivos atuais

Coisas que **explicitamente** decidimos não fazer agora — não tentar resolver:

- Atendimento por voz (call) — só texto/áudio em WhatsApp
- Marketplace de farmácias — cada tenant é independente
- Análise preditiva de demanda — fora de escopo
- App mobile próprio — portal é web only
- Suporte multi-idioma robusto — PT-BR é o foco; campos `language` existem mas não testados em produção
