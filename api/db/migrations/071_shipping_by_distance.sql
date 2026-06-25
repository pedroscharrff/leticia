-- ─────────────────────────────────────────────────────────────────────────────
-- Frete por DISTÂNCIA (raio) — evolução da capability `delivery.shipping_by_cep`.
--
-- Hoje o frete é só uma tabela de faixas de CEP → valor fixo digitado à mão
-- (public.tenant_shipping_rules). Não há "distância" nem origem da farmácia.
-- Esta migration adiciona o modelo por raio:
--
--   1) public.tenant_shipping_origin — endereço/coordenada de ORIGEM da farmácia
--      (1 linha por tenant). `mode` decide qual modelo o tool usa:
--         'cep_table' (default, comportamento atual) | 'distance' (novo).
--      lat/lng são preenchidos por geocoding do CEP (services/geocoding.py).
--
--   2) public.tenant_shipping_distance_tiers — faixas de raio (km) → valor + prazo.
--      O dono cadastra "até 3 km = R$ 5", "até 5 km = R$ 8", "até 10 km = R$ 15"…
--      O tool calcula a distância origem↔CEP-do-cliente e aplica a MENOR faixa
--      cujo `max_distance_km` ainda cobre a distância. Acima da maior faixa →
--      "fora da área de entrega" (não inventa valor).
--
-- Ambas são tabelas GLOBAIS com `tenant_id` (mesmo padrão de
-- tenant_shipping_rules) — não vivem no schema do tenant, logo NÃO precisam
-- entrar no aggregator `create_tenant_schema_full` (migration 048).
-- ─────────────────────────────────────────────────────────────────────────────

-- ── 1. Origem da farmácia ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenant_shipping_origin (
    tenant_id         UUID         PRIMARY KEY REFERENCES public.tenants(id) ON DELETE CASCADE,
    mode              VARCHAR(16)  NOT NULL DEFAULT 'cep_table'
                          CHECK (mode IN ('cep_table', 'distance')),
    -- Fonte da distância no modo 'distance':
    --   'haversine' = linha reta (grátis, geocoding por CEP)
    --   'google'    = rota real de rua (Google Distance Matrix, chave da plataforma)
    distance_source   VARCHAR(16)  NOT NULL DEFAULT 'haversine'
                          CHECK (distance_source IN ('haversine', 'google')),
    cep               VARCHAR(9),                 -- '01310-100' (origem da farmácia)
    lat               DOUBLE PRECISION,           -- preenchido por geocoding
    lng               DOUBLE PRECISION,
    resolved_address  TEXT,                       -- endereço legível devolvido pelo geocoder
    geocoded_at       TIMESTAMPTZ,                -- quando lat/lng foram resolvidos
    created_at        TIMESTAMPTZ  DEFAULT NOW(),
    updated_at        TIMESTAMPTZ  DEFAULT NOW()
);

-- Defensivo: se a 071 foi aplicada numa versão anterior (sem distance_source),
-- adiciona a coluna sem recriar a tabela.
ALTER TABLE public.tenant_shipping_origin
    ADD COLUMN IF NOT EXISTS distance_source VARCHAR(16) NOT NULL DEFAULT 'haversine';
DO $ck$
BEGIN
    ALTER TABLE public.tenant_shipping_origin
        ADD CONSTRAINT tenant_shipping_origin_distance_source_chk
        CHECK (distance_source IN ('haversine', 'google'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $ck$;

-- ── 2. Faixas de raio (km) ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS public.tenant_shipping_distance_tiers (
    id               UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id        UUID          NOT NULL REFERENCES public.tenants(id) ON DELETE CASCADE,
    label            VARCHAR(120)  NOT NULL,
    max_distance_km  NUMERIC(6,2)  NOT NULL,      -- teto da faixa (ex.: 3, 5, 10, 15)
    valor            NUMERIC(10,2) NOT NULL DEFAULT 0,
    prazo_dias       INTEGER       NOT NULL DEFAULT 2,
    gratis_acima     NUMERIC(10,2),               -- frete grátis acima desse subtotal (NULL = nunca)
    active           BOOLEAN       NOT NULL DEFAULT TRUE,
    sort_order       INTEGER       NOT NULL DEFAULT 100,
    created_at       TIMESTAMPTZ   DEFAULT NOW(),
    updated_at       TIMESTAMPTZ   DEFAULT NOW(),
    CHECK (max_distance_km > 0)
);

CREATE INDEX IF NOT EXISTS idx_shipping_tiers_tenant
    ON public.tenant_shipping_distance_tiers (tenant_id, active, max_distance_km);

-- ── 3. Atualiza a descrição da capability (menciona o modo por distância) ─────
UPDATE public.capability_catalog
   SET long_desc = $md$**Como funciona**
Dois modos, escolhidos na tela **Frete & Entrega**:

- **Por faixa de CEP** (padrão): você cadastra faixas de CEP → valor + prazo.
- **Por distância** (raio): você cadastra o **CEP da sua farmácia** (a origem) e faixas de raio em km → valor + prazo (ex.: até 3 km = R$ 5, até 5 km = R$ 8, até 10 km = R$ 15). Quando o cliente informa o CEP, o robô calcula a distância real até ele e aplica a faixa certa. Acima da maior faixa, ele avisa que está **fora da área de entrega** em vez de inventar valor.

Em ambos pode oferecer **frete grátis acima de um valor**.

**Quando ativar**
Se você faz entregas. Hoje, sem isso, o robô fecha pedido sem calcular frete e o cliente fica surpreso.

**Quando NÃO ativar**
Se a farmácia é só retirada no balcão.

**Exemplo**
> Cliente: "Meu CEP é 01310-100"
> Robô: "Você está a ~2,4 km da gente. A entrega fica R$ 5,00 em até 2 dias úteis. Seu total fica R$ 67,80. Confirma?"$md$
 WHERE key = 'delivery.shipping_by_cep';
