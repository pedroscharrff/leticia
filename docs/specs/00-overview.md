# SPECs — índice

Especificações por módulo do backend. Cada SPEC descreve **contrato**, **invariantes**, **pontos de extensão** e **regressões conhecidas**.

| # | SPEC | Cobre |
|---|---|---|
| 01 | [Agent Graph](./01-agent-graph.md) | LangGraph: estado, nodes fixos, roteamento, retry/escalation |
| 02 | [Skills](./02-skills.md) | Cada skill node: contrato, prompts, fast-paths, handoffs |
| 03 | [Tools](./03-tools.md) | Tools que skills usam (inventory, customer, balcão, bulario, sales extras) |
| 04 | [Capabilities](./04-capabilities.md) | Feature flags por tenant + catálogo + gating |
| 05 | [Channels + Broker](./05-channels-and-broker.md) | Adapters de canal + broker universal de webhooks |
| 06 | [Billing + Tenancy](./06-billing-and-tenancy.md) | Multi-tenant, planos, usage, subscriptions |
| 07 | [Database](./07-database.md) | Schema global vs per-tenant, migrations, convenções |
| 08 | [LLM Layer](./08-llm-layer.md) | Providers, prompt caching, retry, modo BYOK |
| 09 | [Workers + Jobs](./09-workers-and-jobs.md) | Celery tasks, beat schedule, debounce/bundling |
| 10 | [Safety Guards](./10-safety-guards.md) | Validadores determinísticos pós-LLM |

## Convenções gerais

- Toda spec começa com **propósito em 1 linha**.
- Toda regressão histórica vira "Não fazer" com data/contexto.
- Mudanças significativas: atualizar a spec ANTES do PR.
- Quando o código contradiz a spec: a spec ganha, o código é bug.

## Como usar com Claude Code

Quando for mexer num módulo, **leia a spec correspondente primeiro**. Quando criar funcionalidade nova, **proponha a spec antes de codar** — pode ser inline no PR e migrada pra cá depois.

Estrutura sugerida de cada spec:

```
1. Propósito (1 linha)
2. Onde vive (arquivos)
3. Contrato público (funções/classes que outros módulos usam)
4. Invariantes (verdades que NÃO podem ser quebradas)
5. Fluxos críticos (passo-a-passo dos casos principais)
6. Pontos de extensão (como adicionar X)
7. Regressões conhecidas / "Não fazer" (com contexto histórico)
```
