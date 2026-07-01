-- ═══════════════════════════════════════════════════════════════════════════
-- 072_signature_position.sql — Posição da assinatura do atendente (topo/fim)
-- ═══════════════════════════════════════════════════════════════════════════
-- Assinatura (`tenant_persona.signature`) sempre foi anexada no FINAL da
-- resposta (ver agents/nodes/context.py::_apply_signature). Este campo deixa
-- a posição configurável pelo tenant, mantendo 'fim' como default para não
-- mudar o comportamento de tenants existentes.

ALTER TABLE public.tenant_persona
    ADD COLUMN IF NOT EXISTS signature_position VARCHAR(10) NOT NULL DEFAULT 'fim'
        CHECK (signature_position IN ('topo','fim'));
