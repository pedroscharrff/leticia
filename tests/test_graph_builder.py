"""Unit tests for build_graph_for_tenant()."""
from unittest.mock import MagicMock, patch

import pytest

from agents.graph_builder import build_graph_for_tenant, TenantConfig


def _make_tenant(skills=None):
    return TenantConfig(
        tenant_id="test-tenant",
        schema_name="tenant_test",
        callback_url="https://example.com/cb",
        skills_active=skills or ["farmaceutico"],
    )


class TestBuildGraphForTenant:
    def test_builds_with_single_skill(self):
        redis_mock = MagicMock()
        cfg = _make_tenant(skills=["farmaceutico"])
        graph = build_graph_for_tenant(cfg, redis_mock)
        assert graph is not None

    def test_builds_with_all_skills(self):
        redis_mock = MagicMock()
        cfg = _make_tenant(
            skills=["farmaceutico", "principio_ativo", "genericos", "vendedor", "recuperador"]
        )
        graph = build_graph_for_tenant(cfg, redis_mock)
        assert graph is not None

    def test_falls_back_to_farmaceutico_when_no_skills(self):
        redis_mock = MagicMock()
        cfg = _make_tenant(skills=[])
        graph = build_graph_for_tenant(cfg, redis_mock)
        assert graph is not None

    def test_ignores_unknown_skills(self):
        redis_mock = MagicMock()
        cfg = _make_tenant(skills=["farmaceutico", "nonexistent_skill"])
        graph = build_graph_for_tenant(cfg, redis_mock)
        assert graph is not None
