"""Shared pytest fixtures."""
import os
import sys
from pathlib import Path

import pytest

# Garante que tanto a raiz (pacote `agents`) quanto `api/` (pacotes `services`,
# `config`, `db`) estejam no sys.path — o código de `agents` importa `services.*`.
_ROOT = Path(__file__).resolve().parents[1]
for _p in (_ROOT, _ROOT / "api"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Point to a dummy env so pydantic-settings doesn't fail when .env is absent
os.environ.setdefault("DATABASE_URL", "postgresql://farmacia:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
