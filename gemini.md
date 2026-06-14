[CONTEXTO]
Atualmente, nosso sistema multi-agente baseado em LangGraph usa marcadores de texto em formato string (ex: `[[HANDOFF:vendedor]]`, `[[ESCALATE]]`) gerados pelo LLM para decidir a próxima Skill. Isso é parseado via Regex, o que aumenta a taxa de erro de sintaxe e o consumo de tokens. Queremos migrar para Tool Calling nativo do LangGraph.

[ARQUIVOS ALVO]

- agents/nodes/skills/\_base.py (onde estão funções como \_parse_handoff)
- uploaded:vendedor.py
- uploaded:farmaceutico.py
- Seu arquivo principal de definição do grafo (ex: graph.py ou app.py)

[REQUISITOS DA IMPLEMENTAÇÃO]

1. Crie uma estrutura baseada em Pydantic (BaseModel) para as ferramentas de controle do ciclo de vida do fluxo:
   - `HandoffTool(target_skill: Literal[...], context: str)`
   - `EscalateToHumanTool(reason: str)`
   - `EndConversationTool()`
2. Vincule (bind) essas ferramentas às chamadas dos LLMs nas skills `vendedor` e `farmaceutico`.
3. No arquivo de definição do grafo do LangGraph, remova o parseamento de texto livre por Regex.
4. Substitua as arestas condicionais (Conditional Edges) para lerem diretamente o campo `message.tool_calls` do último estado gerado pelo LLM.
5. Remova dos prompts das skills as referências textuais a `[[HANDOFF]]`, substituindo-as por instruções de uso dessas ferramentas.

[CRITÉRIOS DE ACEITAÇÃO]

- O roteamento entre nós deve falhar estritamente se o LLM não invocar a ferramenta correspondente.
- Nenhum output em texto puro enviado ao usuário do WhatsApp deve conter resíduos como "[[HANDOFF...]]".
