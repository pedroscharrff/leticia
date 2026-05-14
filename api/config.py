from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # Database
    database_url: str

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # RabbitMQ
    rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"

    # LLM API keys
    anthropic_api_key: str = ""
    google_api_key: str = ""
    openai_api_key: str = ""
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

    # External medication/bulas API (used by farmaceutico tool-skill)
    bulas_api_base_url: str = ""
    bulas_api_key: str = ""
    bulas_api_timeout_seconds: int = 10

    # Tool-calling loop limits
    skill_max_tool_iterations: int = 5

    # Limits
    celery_workers_concurrency: int = 16
    session_ttl_seconds: int = 1800
    max_context_messages: int = 10
    analyst_max_retries: int = 1
    llm_timeout_seconds: int = 30
    # 0.0 = fully deterministic, 1.0 = highly creative.
    # 0.2 keeps the agent on-script while allowing minor phrasing variation.
    llm_temperature: float = 0.2


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
