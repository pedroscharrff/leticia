-- ═══════════════════════════════════════════════════════════════════════════
-- 064_persona_register.sql — Registro de comunicação na persona
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Dois eixos guiados de personalização que faltavam para os clientes afinarem o
-- agente entre "técnico" e "simples/acolhedor":
--   - vocabulary_level   → nível de vocabulário (leigo / intermediário / técnico)
--   - explanation_depth  → quanto detalhe dar nas explicações
--
-- public.tenant_persona é tabela PUBLIC (não per-tenant) → não toca
-- create_tenant_schema_full. Renderizado em agents/nodes/skills/_base.py
-- (_persona_prefix) — é a ÚNICA porta de entrada da persona no prompt. Campos no
-- bloco ESTÁVEL (cacheado): só invalidam o prefixo quando o tenant edita.
-- Idempotente (ADD COLUMN IF NOT EXISTS).

ALTER TABLE public.tenant_persona
    ADD COLUMN IF NOT EXISTS vocabulary_level VARCHAR(15) NOT NULL DEFAULT 'intermediario'
        CHECK (vocabulary_level IN ('leigo','intermediario','tecnico'));

ALTER TABLE public.tenant_persona
    ADD COLUMN IF NOT EXISTS explanation_depth VARCHAR(15) NOT NULL DEFAULT 'equilibrada'
        CHECK (explanation_depth IN ('minima','equilibrada','detalhada'));
