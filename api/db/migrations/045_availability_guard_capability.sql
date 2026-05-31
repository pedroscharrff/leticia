-- 045_availability_guard_capability.sql
-- Capability: validador de disponibilidade — pega quando o LLM afirma ter
-- produto que (a) não existe no catálogo ou (b) está com estoque zero, e
-- reescreve a resposta com correção honesta antes de mandar pro cliente.
--
-- Detecção é determinística (heurística + cruzamento com resultados das
-- chamadas de `buscar_produto` deste turno). Default OFF — operador liga em
-- /portal/recursos depois de validar em produção.

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('safety.availability_guard',
 'Validador de disponibilidade',
 'safety',
 'Pega e corrige quando o agente afirma ter produto que não está no catálogo.',
 $md$**O problema**
O LLM, sob carga ou em loops de tool-use, às vezes afirma "temos esse remédio" ANTES de consultar o catálogo — e depois descobre que não tem. O cliente recebe a confirmação errada.

**Como funciona o guard**
Depois que o agente gera a resposta, o validador roda determinísticamente:

1. Olha os resultados das chamadas de `buscar_produto` deste turno.
2. Se algum produto foi pesquisado e NÃO existe no catálogo (ou tem estoque zero), verifica se a resposta do agente contém frase de afirmação ("temos", "tem sim", "em estoque", etc).
3. Em caso de batida, REESCREVE a resposta com uma correção honesta antes de mandar pro cliente: "Desculpa, na verdade [X] não temos no momento. Posso te ajudar com alternativa?"

**Trade-offs**
- Custo zero de LLM (heurística pura).
- Falsos negativos possíveis (não pega 100% — frases muito sutis escapam).
- Falsos positivos raros, mas se acontecerem o pior caso é uma resposta levemente brusca.
- Cobre vendedor + farmacêutico + qualquer skill que use `buscar_produto`.$md$,
 'Evita prometer produto que a farmácia não tem — proteção determinística pós-LLM.',
 'basic', '{}', '{}',
 '{}'::jsonb,
 '{}'::jsonb,
 TRUE, 'ga', 'shield', 50)
ON CONFLICT (key) DO UPDATE SET
    name            = EXCLUDED.name,
    category        = EXCLUDED.category,
    short_desc      = EXCLUDED.short_desc,
    long_desc       = EXCLUDED.long_desc,
    impact_label    = EXCLUDED.impact_label,
    min_plan        = EXCLUDED.min_plan,
    depends_on      = EXCLUDED.depends_on,
    requires_secret = EXCLUDED.requires_secret,
    config_schema   = EXCLUDED.config_schema,
    default_config  = EXCLUDED.default_config,
    status          = EXCLUDED.status,
    icon            = EXCLUDED.icon,
    sort_order      = EXCLUDED.sort_order,
    updated_at      = NOW();
