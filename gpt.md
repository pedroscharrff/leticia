# Evolução Arquitetural do Sistema Multiagente (LangGraph)

## Objetivo

O sistema atual já está em produção e possui uma arquitetura funcional baseada em:

- LangGraph
- Orquestrador
- Skills especializadas
- Handoffs
- Capabilities
- Tool Calling
- Analyst
- Safety Guard

O objetivo desta evolução NÃO é reescrever o sistema.

O objetivo é prepará-lo para crescimento contínuo, permitindo adicionar novas capacidades, agentes, integrações e bases de conhecimento sem aumentar exponencialmente a complexidade.

---

# Estado Atual

Fluxo atual:

```text
START
 ↓
load_context
 ↓
ingest_media
 ↓
sentiment_analyzer
 ↓
orchestrator
 ↓
skill
 ↓
safety_guard
 ↓
analyst
 ↓
save_context
 ↓
END
```

Atualmente o sistema classifica diretamente para uma skill:

```python
{
    "skill": "farmaceutico"
}
```

ou

```python
{
    "skill": "vendedor"
}
```

---

# Problema de Escalabilidade

Hoje existem poucas skills:

```text
farmaceutico
vendedor
genericos
principio_ativo
guardrails
```

Mas futuramente existirão outras:

```text
crm
fidelidade
entrega
financeiro
cobranca
convenios
receitas
promocoes
pos_venda
atendimento_humano
```

Se o orquestrador continuar roteando diretamente para skills, o prompt do orquestrador crescerá indefinidamente e se tornará difícil de manter.

---

# Nova Camada: Catálogo de Conversas

Antes de decidir qual skill executará a conversa, devemos classificar o tipo de conversa.

Criar:

```python
class ConversationType(str, Enum):
    GREETING = "greeting"

    PRODUCT_SEARCH = "product_search"

    CLINICAL_QUESTION = "clinical_question"

    DRUG_INFORMATION = "drug_information"

    GENERIC_SUBSTITUTION = "generic_substitution"

    ORDER_CREATION = "order_creation"

    ORDER_FINALIZATION = "order_finalization"

    POST_SALES = "post_sales"

    DELIVERY = "delivery"

    CRM = "crm"

    HUMAN_SUPPORT = "human_support"
```

---

# Fluxo Futuro

Trocar:

```text
Mensagem
 ↓
Orchestrator
 ↓
Skill
```

por:

```text
Mensagem
 ↓
Conversation Classification
 ↓
Conversation Type
 ↓
Skill Resolution
 ↓
Skill
```

Exemplo:

```text
"Posso tomar amoxicilina com cerveja?"

↓

conversation_type:
clinical_question

↓

skill:
farmaceutico
```

---

```text
"Quero uma opção mais barata que Lipitor"

↓

conversation_type:
generic_substitution

↓

skill:
genericos
```

---

```text
"Pode fechar meu pedido"

↓

conversation_type:
order_finalization

↓

skill:
vendedor
```

---

# Estado Persistente da Conversa

Adicionar ao AgentState:

```python
conversation_type: str | None

current_owner: str | None

conversation_phase: str | None
```

---

## current_owner

Representa qual skill está conduzindo a conversa atualmente.

Exemplo:

```python
{
    "current_owner": "farmaceutico"
}
```

Enquanto houver contexto ativo, novas mensagens devem retornar para o mesmo agente sem nova classificação.

---

## conversation_phase

Representa a etapa da conversa.

Exemplo:

```python
{
    "conversation_phase": "collecting_medication"
}
```

Depois:

```python
{
    "conversation_phase": "collecting_presentation"
}
```

Depois:

```python
{
    "conversation_phase": "collecting_quantity"
}
```

Depois:

```python
{
    "conversation_phase": "finalization"
}
```

---

# Regra de Reclassificação

O orquestrador NÃO deve ser executado em toda mensagem.

Executar apenas quando:

```python
current_owner is None
```

ou

```python
houve_handoff
```

ou

```python
conversation_encerrada
```

ou

```python
escalate == True
```

---

# Benefícios

Redução significativa de:

- custo de tokens
- latência
- classificações erradas
- troca desnecessária de agentes

---

# Skill Registry Evoluído

Substituir estrutura simples atual por:

```python
@dataclass
class SkillDefinition:
    name: str

    supported_conversation_types: list[str]

    allowed_handoffs: list[str]

    capabilities: list[str]

    prompt_builder: Callable

    tool_builder: Callable
```

---

Exemplo:

```python
farmaceutico = SkillDefinition(
    name="farmaceutico",

    supported_conversation_types=[
        "clinical_question",
        "drug_information",
    ],

    allowed_handoffs=[
        "vendedor",
        "genericos",
        "principio_ativo",
    ],
)
```

---

# Prompt Modular

Evitar prompts monolíticos.

Trocar:

```python
_SYSTEM = """
1000+ linhas
"""
```

por:

```python
PROMPT_CORE

PROMPT_HANDOFF

PROMPT_END

PROMPT_STOCK

PROMPT_BULA

PROMPT_CAPABILITIES
```

Montados dinamicamente.

---

Exemplo:

```python
prompt = PromptBuilder(
    core=True,
    handoff=True,
    stock=track_stock,
    bula=True,
).build()
```

---

# Separação entre Infraestrutura e Especialidade

## Guardrails

Guardrails NÃO é uma skill.

Guardrails é infraestrutura.

Arquitetura desejada:

```text
Mensagem
 ↓
Guardrails
 ↓
Conversation Router
 ↓
Skill
```

---

# Runtime Compartilhado

Criar uma camada comum:

```python
AgentRuntime
```

Responsável por:

- tool loop
- tracing
- metrics
- handoff handling
- escalation handling
- retry handling
- empty response fallback
- error handling

---

Skills devem conter apenas:

- Prompt
- Tools
- Capabilities
- Regras de negócio específicas

---

# Handoff Estruturado

Hoje:

```text
[[HANDOFF:vendedor:dipirona]]
```

Futuro:

```python
handoff(
    target="vendedor",
    context="dipirona"
)
```

---

Manter compatibilidade retroativa.

O runtime deve aceitar:

```text
[[HANDOFF]]
```

e converter internamente.

---

# Simplificação de Skills

Avaliar transformar algumas skills em ferramentas.

Possíveis candidatas:

```text
genericos
principio_ativo
```

Essas habilidades são predominantemente consultas estruturadas e podem futuramente virar tools acessadas por agentes conversacionais.

Não executar essa mudança agora.

Apenas preparar a arquitetura para permitir essa migração no futuro.

---

# Objetivo Final

Chegar a uma arquitetura onde:

```text
LangGraph
 ↓
Guardrails
 ↓
Conversation Classifier
 ↓
Skill Resolver
 ↓
Agent Runtime
 ↓
Skill
 ↓
Tools
 ↓
Analyst
 ↓
Save Context
```

Com isso será possível adicionar dezenas de novas capacidades sem crescimento exponencial da complexidade do sistema.
