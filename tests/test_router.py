"""Unit tests for route_to_skill() and analyst_router()."""
import pytest
from agents.router import route_to_skill, analyst_router


# ── route_to_skill ────────────────────────────────────────────────────────────

def _state(**overrides):
    base = {
        "tenant_id": "t1",
        "session_id": "t1:5511999",
        "phone": "5511999",
        "schema_name": "tenant_t1",
        "current_message": "Preciso de dipirona",
        "messages": [],
        "intent": "comprar dipirona",
        "selected_skill": "farmaceutico",
        "confidence": 0.9,
        "retry_count": 0,
        "customer_profile": "indefinido",
        "cart": {"items": [], "subtotal": 0.0},
        "stock_mode": "catalogo",
        "available_skills": ["farmaceutico", "vendedor"],
        "analyst_approved": False,
        "final_response": "",
        "escalate": False,
        "callback_url": "https://example.com/cb",
    }
    base.update(overrides)
    return base


class TestRouteToSkill:
    def test_routes_to_available_skill(self):
        state = _state(selected_skill="vendedor", available_skills=["farmaceutico", "vendedor"])
        assert route_to_skill(state) == "vendedor"

    def test_falls_back_to_farmaceutico_when_skill_unavailable(self):
        # principio_ativo not in available_skills
        state = _state(selected_skill="principio_ativo", available_skills=["farmaceutico"])
        assert route_to_skill(state) == "farmaceutico"

    def test_guardrails_always_routes_to_guardrails(self):
        state = _state(selected_skill="guardrails", available_skills=["farmaceutico"])
        assert route_to_skill(state) == "guardrails"

    def test_unknown_skill_falls_back_to_farmaceutico(self):
        state = _state(selected_skill="unknown_skill", available_skills=["farmaceutico"])
        assert route_to_skill(state) == "farmaceutico"


# ── analyst_router ────────────────────────────────────────────────────────────

class TestAnalystRouter:
    def test_approved_returns_approved(self):
        state = _state(analyst_approved=True, escalate=False)
        assert analyst_router(state) == "approved"

    def test_escalate_takes_priority(self):
        state = _state(analyst_approved=False, escalate=True)
        assert analyst_router(state) == "escalate"

    def test_not_approved_not_escalated_returns_retry(self):
        state = _state(analyst_approved=False, escalate=False)
        assert analyst_router(state) == "retry"
