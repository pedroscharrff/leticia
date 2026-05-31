-- 044b_capability_category_safety.sql
--
-- Adiciona 'safety' às categorias permitidas em public.capability_catalog.
-- Pré-requisito pras migrations 045 (availability_guard) e 046 (price/
-- prescription/delivery guards), que pertencem a essa categoria.
--
-- Idempotente: DROP IF EXISTS + ADD recria a constraint do zero.
-- Nome do arquivo "044b" garante que rode ANTES de 045 (sort alfabético).

ALTER TABLE public.capability_catalog
    DROP CONSTRAINT IF EXISTS capability_catalog_category_check;

ALTER TABLE public.capability_catalog
    ADD CONSTRAINT capability_catalog_category_check
    CHECK (category IN (
        'atendimento',
        'vendas',
        'pagamentos_entrega',
        'analise',
        'inteligencia',
        'safety'
    ));
