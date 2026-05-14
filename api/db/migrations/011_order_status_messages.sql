-- ═══════════════════════════════════════════════════════════════════════════
-- 011_order_status_messages.sql — Per-tenant templates sent to the customer
-- when an order's status changes in the panel.
-- ═══════════════════════════════════════════════════════════════════════════
--
-- Placeholders supported in `template`:
--   {nome}          — customer name (or "" if unknown)
--   {numero_pedido} — short order id
--   {total}         — formatted "R$ XX,YY"
--   {itens}         — bulleted list of items
--   {farmacia}      — pharmacy name (from tenant_persona.pharmacy_name)
--
-- Setting `enabled = FALSE` skips the notification for that status.

CREATE TABLE IF NOT EXISTS public.tenant_order_status_messages (
    tenant_id   UUID         NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    status      VARCHAR(20)  NOT NULL
                  CHECK (status IN ('pending','confirmed','processing','shipped','delivered','cancelled')),
    enabled     BOOLEAN      NOT NULL DEFAULT TRUE,
    template    TEXT         NOT NULL,
    updated_by  VARCHAR(200),
    updated_at  TIMESTAMPTZ  DEFAULT NOW(),
    PRIMARY KEY (tenant_id, status)
);

-- Backfill default templates for every existing tenant
INSERT INTO public.tenant_order_status_messages (tenant_id, status, enabled, template)
SELECT t.id, x.status, x.enabled, x.template
  FROM public.tenants t
  CROSS JOIN (VALUES
    ('pending',    FALSE, 'Olá {nome}! Recebi seu pedido #{numero_pedido} no valor de {total}. Já encaminhei para nossa equipe.'),
    ('confirmed',  TRUE,  'Boas notícias, {nome}! Seu pedido #{numero_pedido} foi confirmado e já estamos preparando.'),
    ('processing', TRUE,  '{nome}, seu pedido #{numero_pedido} está sendo separado pela nossa equipe.'),
    ('shipped',    TRUE,  '{nome}, seu pedido #{numero_pedido} saiu para entrega! Em instantes você recebe.'),
    ('delivered',  TRUE,  '{nome}, seu pedido #{numero_pedido} foi entregue. Obrigado pela preferência! 💚'),
    ('cancelled',  TRUE,  '{nome}, seu pedido #{numero_pedido} foi cancelado. Se foi engano, é só nos avisar.')
  ) AS x(status, enabled, template)
ON CONFLICT (tenant_id, status) DO NOTHING;
