-- 061_time_aware_greeting.sql
-- Capability: attendance.time_aware_greeting
--
-- Sem este contexto, o agente não sabe que horas são e pode mandar "boa noite"
-- de manhã, "bom dia" às 23h, etc. Quando ATIVA, injeta um pequeno bloco
-- VOLÁTIL no system_prompt de TODOS os skills informando hora atual + período
-- (madrugada / manhã / tarde / noite) e a saudação correta correspondente.
--
-- Fuso fixo em America/Sao_Paulo (segue convenção do projeto — não temos
-- coluna `timezone` per-tenant ainda; ver workers/jobs/abandoned_cart.py).
-- Quando suportarmos tenant.timezone, o helper troca a TZ sem mudar a cap.
--
-- Default OFF (opt-in — não muda comportamento de tenants existentes nem
-- aumenta custo de quem não precisa). Custo de quem ativa: ~30 tokens
-- voláteis por turno (nunca entra no prefixo cacheado).

INSERT INTO public.capability_catalog
    (key, name, category, short_desc, long_desc, impact_label,
     min_plan, depends_on, requires_secret, config_schema, default_config,
     default_enabled, status, icon, sort_order)
VALUES
('attendance.time_aware_greeting',
 'Saudação no período correto do dia',
 'atendimento',
 'O agente sabe que horas são e usa "bom dia / boa tarde / boa noite" no período certo, em vez de chutar.',
 $md$**O problema**
Sem contexto temporal o agente não tem como saber se é 8h da manhã ou 23h. Resultado: clientes recebem "bom dia" às 22h ou "boa noite" no meio da tarde — parece bot, derruba a sensação de atendimento humano.

**Como funciona**
Quando ATIVA, antes de cada resposta o sistema injeta no contexto do agente a hora atual no fuso da farmácia (America/Sao_Paulo) e o período do dia:
- 00h–05h59 → **madrugada** ("Boa madrugada")
- 06h–11h59 → **manhã** ("Bom dia")
- 12h–17h59 → **tarde** ("Boa tarde")
- 18h–23h59 → **noite** ("Boa noite")

O agente passa a usar a saudação correta sempre que abrir ou retomar uma conversa.

**Quando ativar**
- Você quer atendimento que pareça humano de verdade — saudação no período certo é o detalhe que mais entrega "isso é um bot" quando erra.
- Sua farmácia atende em mais de um período do dia (manhã + tarde, ou 24h).

**Quando NÃO ativar**
- Você atende em um horário só e o agente só fala "olá" / "oi" (sem saudação de período) — aí esta cap não traz ganho.

**Custo extra**
~30 tokens por turno (bloco volátil — não afeta o prefixo cacheado do Anthropic).
$md$,
 'Conversas mais humanas, sem "bom dia" às 22h',
 'basic', '{}', '{}',
 '{}'::jsonb,
 '{}'::jsonb,
 FALSE, 'ga', 'clock', 57)
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
