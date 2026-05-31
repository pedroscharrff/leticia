# SPEC 05 — Channels + Broker

**Propósito**: receber mensagens de qualquer gateway de WhatsApp/Telegram e responder no formato que o gateway espera.

## Onde vive

```
api/channels/
├── base.py                # ChannelAdapter ABC + InboundMessage/OutboundMessage
├── registry.py            # CHANNEL_REGISTRY
├── whatsapp_cloud.py
├── whatsapp_zapi.py
└── telegram.py

api/routers/
├── webhook.py             # POST /webhook/{token} — canal nativo (formato canonical)
├── broker.py              # POST /hooks/{tenant}/{slug} — gateway universal
└── channels.py            # CRUD de tenant_channels

api/services/
└── broker.py              # apply_mapping (template), discover_paths, preview
```

## Duas estratégias de ingest

### Estratégia 1: Webhook nativo (`/webhook/{token}`)

Para gateways que conseguem entregar **no formato canonical** ou onde escrevemos um adapter:

```json
POST /webhook/{tenant_api_key}
{
  "phone": "5511999999999",
  "message": "tem dipirona?",
  "session_id": "...",          // opcional, gerado se ausente
  "media_type": "audio",         // opcional
  "media_mime": "audio/ogg",
  "media_url": "https://...",    // OU media_b64 (já baixado)
  "media_b64": "..."
}
```

Worker: `process_message`.

### Estratégia 2: Broker universal (`/hooks/{tenant}/{slug}`)

Para gateways arbitrários (Z-API, WA Cloud direto, ClickMassa, Uazapi, parceiros customizados):

- Tenant cria uma **integration** no portal (slug, hmac_secret opcional)
- Define **`inbound_field_map`**: como extrair phone/mensagem/mídia/nome do payload bruto
- Define **`reply_mode`**: `response` (síncrono no body) ou `forward` (POST out em `reply_url`)
- Define **`reply_body_template`**: template Mustache-like sobre `{input, reply, phone, message, name, session_id, event_id}`
- (Opcional) **`bundle_enabled` + `bundle_window_seconds`** — debounce de mensagens picadas
- (Opcional) **`handoff_config`**: provider de transferência humana
- (Opcional) **`session_config`**: `close_keywords`, `close_message`, TTL

Worker: `process_broker_message` ou `process_bundled_message`.

## Contrato dos adapters

```python
@dataclass
class InboundMessage:
    phone: str
    text: str
    channel_type: str
    raw: dict
    media_type: str | None
    media_mime: str | None
    media_url: str | None       # direto baixável (Z-API)
    media_id: str | None        # id do provider (WA Cloud — precisa token)

@dataclass
class OutboundMessage:
    to: str
    text: str

class ChannelAdapter(ABC):
    channel_type: str
    @abstractmethod
    def verify_signature(self, body: bytes, headers: dict) -> bool
    @abstractmethod
    def parse_inbound(self, payload: dict) -> InboundMessage | None
    @abstractmethod
    async def send_outbound(self, msg: OutboundMessage, credentials: dict) -> None
```

`credentials` é dict descriptografado de `tenant_secrets` (Fernet).

## Invariantes

1. **`parse_inbound` retorna `None` para eventos não-mensagem** (status, ack, presence, etc.) — não levantar exceção.
2. **Texto + mídia mutuamente combináveis**: imagem com caption → text=caption + media_type=image. Áudio puro → text="".
3. **`api_key` do tenant é o webhook_token**. Validação em DB com `active=TRUE`.
4. **Broker raw event SEMPRE persistido** (`broker_raw_events`) antes do worker rodar — replay/auditoria.
5. **Bundling não pode atropelar** — task agendada compara `last_seen` (Redis) com `scheduled_for_ts` próprio e desiste se chegou msg nova depois.
6. **Pause de IA respeitada antes de tudo** — `is_ai_paused(tenant, phone)` curto-circuita ambos os endpoints (mesmo antes de billing check).

## Fluxos críticos

### Fluxo broker (passo a passo)

```
POST /hooks/{tenant_token}/{integration_slug}
  ├─ valida tenant + integration (active, enabled)
  ├─ valida HMAC se hmac_secret configurado
  ├─ persiste broker_raw_events (status=received, payload bruto)
  ├─ aplica inbound_field_map → canonical_input {phone, message, session_id, media_*, name}
  ├─ detect_media auxiliar (services.media_detect) se mapping não capturou mídia
  ├─ is_ai_paused? → 200 OK, sem disparar worker
  ├─ usage_check (middleware) → 402 se limite
  ├─ bundle_enabled?
  │     SIM → push em Redis list + agenda process_bundled_message com countdown
  │     NÃO → disparada process_broker_message direto
  └─ retorna reply_status_code com body padrão ou template (modo response)
```

### Apply mapping (broker.apply_mapping)

```python
template = {
    "to": "{{phone}}",
    "from": "bot",
    "body": "{{reply}}",
    "meta": {"event": "{{event_id}}"}
}
ctx = {"input": canonical_input, "reply": "...", "phone": "...", "message": "...", ...}
applied = apply_mapping(template, ctx)
# applied = {"to": "5511...", "from": "bot", "body": "...", "meta": {"event": "..."}}
```

Substitui `{{key}}` com lookup em ctx (suporta dotted path).

### Send outbound (canal nativo)

```python
adapter = get_adapter(channel.channel_type, webhook_secret=channel.webhook_secret)
credentials = decrypt_secrets(channel.credentials_ref)
await adapter.send_outbound(OutboundMessage(to=phone, text=reply), credentials)
```

Para mídia (ofertas pré-handoff): `services.channel_media.send_media(provider, channel_cfg, media_type, phone, caption, media_url)`.

## Adapters atuais

### `whatsapp_cloud.py` — WhatsApp Cloud API (Meta)
- `verify_signature` valida `X-Hub-Signature-256` HMAC sha256
- `parse_inbound` extrai de `entry[].changes[].value.messages[]`
- `send_outbound` POST em `https://graph.facebook.com/v18.0/{phone_id}/messages`
- Mídia: vem como `media_id` (precisa token pra resolver URL)

### `whatsapp_zapi.py` — Z-API (BR)
- Sem HMAC (rely on API key na URL + IP allowlist)
- `parse_inbound` reconhece `type: ReceivedCallback` com `text/audio/image/video/document`
- Mídia: vem como URL direto baixável
- `send_outbound` POST em `https://api.z-api.io/instances/{id}/token/{token}/send-text` com `Client-Token` header

### `telegram.py`
- Sem HMAC; valida `secret_token` opcional via `X-Telegram-Bot-Api-Secret-Token`
- `parse_inbound` extrai de `message.text`, `message.voice`, `message.photo`
- `send_outbound` POST em `https://api.telegram.org/bot{token}/sendMessage`

## Pontos de extensão

### Novo canal nativo

1. Criar `api/channels/<canal>.py` herdando `ChannelAdapter`.
2. Implementar `channel_type` class attribute (snake_case).
3. Implementar 3 métodos abstratos.
4. Registrar em `api/channels/registry.py::CHANNEL_REGISTRY`.
5. Migration para ampliar CHECK em `tenant_channels.channel_type`.
6. Frontend `PortalCanais.tsx` adicionar form de credenciais.

### Novo gateway via broker (sem código)

1. Tenant cria integration no portal.
2. Cola exemplo de payload em `discover` → backend extrai paths.
3. Configura `inbound_field_map` (UI visual).
4. Configura `reply_mode` + template.
5. Testa via `preview` (mapping + reply contra payload sample).

### Novo handoff provider (além de ClickMassa)

1. Em `api/services/handoff.py`, abstrair `transfer_to_human` em dispatcher.
2. Implementar `send_<provider>_message` + `transfer_via_<provider>`.
3. `handoff_config.provider` decide qual disparar.

## Regressões conhecidas / "Não fazer"

- **Não disparar worker sem persistir `broker_raw_events` antes.** Erro intermitente perde mensagem.
- **Não validar HMAC após o body já ter sido `.json()` parseado** — precisa do bytes brutos. Use `await request.body()`.
- **Não esquecer `phone_clean = "".join(c for c in phone if c.isdigit())[:20]`** — Z-API/WA mandam `":21@s.whatsapp.net"` no número.
- **Não ignorar mídia que veio fora do `inbound_field_map`** — `services.media_detect.detect_media(raw_payload)` é safety net.
- **Não fazer forward do reply principal quando `handoff_was_executed=True`** — `transfer_to_human` já entregou a mensagem ao cliente; forward duplicaria.
- **Não confiar no `phone` cru pro `session_key`** — sempre normalize. Caso contrário, sessões duplicam.
- **Não bypassar `is_ai_paused`** — quando atendente humano assumiu, bot ignora 100% das mensagens daquela conversa (mesmo as picadas).

## Schema das tabelas

### `public.tenant_channels`
```sql
tenant_id, channel_type, display_name, credentials_ref (→ tenant_secrets.key),
webhook_secret, active, config_json, handoff_config, session_config, handoff_pause_minutes
```

### `public.tenant_integrations` (broker)
```sql
tenant_id, slug, name, direction, hmac_secret, hmac_header, hmac_algorithm, enabled,
inbound_field_map JSONB,
reply_mode TEXT, reply_url TEXT, reply_method, reply_headers JSONB,
reply_body_template JSONB, reply_status_code INT,
bundle_enabled, bundle_window_seconds,
skip_rules JSONB,            -- regras "se body bate X, ignora"
handoff_config JSONB,
session_config JSONB,
config_json JSONB
```

### `public.broker_raw_events`
```sql
tenant_id, integration_id, payload JSONB, headers JSONB,
status TEXT (received|processing|processed|failed|skipped),
canonical_event TEXT, canonical_payload JSONB,
attempts INT, error TEXT,
forward_url, forward_status_code, forward_response,
created_at, processed_at
```
