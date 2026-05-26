-- ─────────────────────────────────────────────────────────────────────────────
-- Offers: suporte a mídia (imagem OU áudio) opcional por oferta.
--
-- - media_type IN (NULL, 'image', 'audio') — NULL = oferta só texto
-- - media_url  : URL pública servida pelo MinIO/S3 (consumida pelo broker do
--                canal — Z-API, WA Cloud, etc.)
-- - media_mime : MIME real do arquivo (image/jpeg, audio/ogg, ...) — usado
--                pelo provider para escolher endpoint correto (send-image vs
--                send-audio) e pelo player no preview do portal.
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE public.offers
    ADD COLUMN IF NOT EXISTS media_type TEXT
        CHECK (media_type IS NULL OR media_type IN ('image', 'audio')),
    ADD COLUMN IF NOT EXISTS media_url  TEXT,
    ADD COLUMN IF NOT EXISTS media_mime TEXT;
