-- 031_integration_config_json.sql
--
-- Adiciona campo de configuração livre por integração para guardar
-- credenciais/parâmetros de envio (base_url, token, provider, flags como
-- notify_order_status). Hoje tenant_integrations só tinha metadata HMAC
-- pra inbound — sem lugar pra credenciais OUTBOUND.

ALTER TABLE public.tenant_integrations
    ADD COLUMN IF NOT EXISTS config_json JSONB NOT NULL DEFAULT '{}'::jsonb;
