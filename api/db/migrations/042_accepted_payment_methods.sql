-- ═══════════════════════════════════════════════════════════════════════════
-- 042_accepted_payment_methods.sql — Métodos de pagamento aceitos por tenant
-- ═══════════════════════════════════════════════════════════════════════════
--
-- No modo de fechamento "completo" com ask_payment, hoje o agente oferece
-- TODOS os métodos. Esta coluna deixa a farmácia escolher QUAIS aceita.
-- O agente só vai oferecer os métodos listados aqui.
--
-- Valores válidos: pix, cartao_credito, cartao_debito, dinheiro, boleto
-- Default: todos os 5 (preserva comportamento atual).

ALTER TABLE public.tenant_sales_config
    ADD COLUMN IF NOT EXISTS accepted_payment_methods TEXT[] NOT NULL
        DEFAULT ARRAY['pix','cartao_credito','cartao_debito','dinheiro','boleto']::TEXT[];

-- Garante linha para todos os tenants existentes
INSERT INTO public.tenant_sales_config (tenant_id)
SELECT id FROM public.tenants
ON CONFLICT (tenant_id) DO NOTHING;
