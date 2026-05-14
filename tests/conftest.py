"""Shared pytest fixtures."""
import os

import pytest

# Point to a dummy env so pydantic-settings doesn't fail when .env is absent
os.environ.setdefault("DATABASE_URL", "postgresql://farmacia:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test")
os.environ.setdefault("GOOGLE_API_KEY", "test-google-key")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
