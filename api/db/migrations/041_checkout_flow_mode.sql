-- ═══════════════════════════════════════════════════════════════════════════
-- 041_checkout_flow_mode.sql — Modo de fechamento configurável por tenant
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Hoje o vendedor decide POR INTUIÇÃO se conduz pagamento e entrega. Muitas
-- farmácias só querem COLETAR o pedido e deixar o balconista resolver
-- pagamento/entrega. Estes campos tornam isso explícito por tenant.
--
--   checkout_mode:
--     'coleta'   = só coleta itens + dados obrigatórios, cria pedido pending,
--                  NÃO pergunta pagamento nem entrega.
--     'completo' = conduz o checkout (pagamento e, se ask_delivery, entrega).
--                  (default — preserva comportamento atual.)
--   ask_payment  = no modo completo, perguntar a forma de pagamento.
--   ask_delivery = no modo completo, perguntar entrega vs retirada + endereço.

ALTER TABLE public.tenant_sales_config
    ADD COLUMN IF NOT EXISTS checkout_mode VARCHAR(20) NOT NULL DEFAULT 'completo'
        CHECK (checkout_mode IN ('coleta', 'completo'));

ALTER TABLE public.tenant_sales_config
    ADD COLUMN IF NOT EXISTS ask_payment BOOLEAN NOT NULL DEFAULT TRUE;

ALTER TABLE public.tenant_sales_config
    ADD COLUMN IF NOT EXISTS ask_delivery BOOLEAN NOT NULL DEFAULT FALSE;

-- Garante linha de config para todos os tenants existentes
INSERT INTO public.tenant_sales_config (tenant_id)
SELECT id FROM public.tenants
ON CONFLICT (tenant_id) DO NOTHING;
