from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    # `database_url` é o caminho usado pelo app em runtime (via PgBouncer em
    # transaction mode). `database_url_direct` aponta pro Postgres direto
    # (porta 5432) e é usado por scripts que precisam de DDL/transações longas
    # ou recursos não-suportados em transaction pooling (migrations, etc).
    database_url: str
    database_url_direct: str = ""

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # RabbitMQ
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # LLM API keys
    anthropic_api_key: str = ""
    google_api_key: str = ""
    openai_api_key: str = ""
    # Google Maps Platform (Distance Matrix) — chave SEPARADA da google_api_key
    # (que é da GenAI/Gemini). Usada pelo frete por distância no modo 'google'
    # (rota real de rua). Vazia = só haversine disponível.
    google_maps_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # App
    secret_key: str
    environment: str = "development"
    log_level: str = "INFO"

    # Admin credentials (bcrypt hash of the admin password)
    admin_email: str = "admin@farmacia.io"
    admin_password_hash: str = ""  # set via ADMIN_PASSWORD_HASH env var

    # JWT
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60

    # Encryption key for tenant secrets (Fernet, base64-urlsafe 32-byte key)
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    encryption_key: str = ""

    # Billing
    stripe_api_key: str = ""
    stripe_webhook_secret: str = ""
    asaas_api_key: str = ""
    asaas_base_url: str = "https://api.asaas.com/v3"

    # Email (Resend)
    resend_api_key: str = ""
    email_from: str = "noreply@farmacia.io"

    # CORS — comma-separated list of allowed origins
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # Public URL of the backend (used to build webhook ingest URLs shown in the UI).
    # Set this to the URL that external systems can reach (e.g. https://api.farmacia.io)
    # Falls back to request.base_url when empty (works for direct access, not behind proxies).
    public_api_url: str = "http://localhost:8000"

    # Self-service signup
    allow_signup: bool = True
    default_trial_days: int = 7

    # Default LLM for orchestrator and analyst nodes (overridable per tenant)
    default_orchestrator_provider: str = "anthropic"
    default_orchestrator_model: str = "claude-haiku-4-5-20251001"
    default_analyst_provider: str = "anthropic"
    default_analyst_model: str = "claude-haiku-4-5-20251001"
    # Default LLM for skill nodes
    default_skill_provider: str = "anthropic"
    default_skill_model: str = "claude-sonnet-4-6"

    # Multimodal ingestion (image + audio from WhatsApp)
    # Transcription provider: 'groq' (whisper-large-v3) or 'openai' (whisper-1)
    media_transcription_provider: str = "groq"
    media_transcription_model: str = "whisper-large-v3"
    groq_api_key: str = ""
    # Vision provider: uses the tenant's default skill LLM (Anthropic/Google) by default
    media_vision_provider: str = "anthropic"
    media_vision_model: str = "claude-sonnet-4-6"
    # Hard limits to avoid abuse (bytes)
    media_max_audio_bytes: int = 16 * 1024 * 1024   # 16 MB
    media_max_image_bytes: int = 5 * 1024 * 1024    # 5 MB

    # MinIO / S3-compatible object storage (mídia de ofertas, etc.)
    # Em prod, MINIO_PUBLIC_URL deve apontar para a URL externa pública do bucket
    # (ex.: https://media.farmacia.io) — é o que vai no body das mensagens enviadas
    # aos providers de canal (que precisam baixar a URL).
    minio_endpoint:    str  = "minio:9000"     # host:port interno (cliente Python)
    minio_access_key:  str  = "farmacia"
    minio_secret_key:  str  = ""
    minio_bucket:      str  = "offers-media"
    minio_secure:      bool = False             # True = HTTPS no cliente interno
    minio_public_url:  str  = "http://localhost:9000"  # base URL para os assets servidos

    # External medication/bulas API (used by farmaceutico tool-skill)
    bulas_api_base_url: str = ""
    bulas_api_key: str = ""
    bulas_api_timeout_seconds: int = 10

    # Tool-calling loop limits
    skill_max_tool_iterations: int = 5

    # Sticky ownership: quando True, o orchestrator NÃO re-classifica a cada
    # turno — enquanto a conversa tem dono (current_owner), novas mensagens
    # voltam ao mesmo skill (economia de custo/latência, menos misroute).
    # Default False = comportamento histórico (classifica todo turno). Ligar só
    # após validar no tenant de testes. Emergência/pedido de humano nunca é
    # interceptado (ver agents/nodes/orchestrator._should_bypass_sticky).
    sticky_ownership_enabled: bool = False

    # Limits
    celery_workers_concurrency: int = 16
    session_ttl_seconds: int = 1800
    max_context_messages: int = 10
    analyst_max_retries: int = 2
    llm_timeout_seconds: int = 30
    # 0.0 = fully deterministic, 1.0 = highly creative.
    # 0.2 keeps the agent on-script while allowing minor phrasing variation.
    llm_temperature: float = 0.2


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
