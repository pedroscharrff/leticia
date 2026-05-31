"""
agents/nodes/safety_guard.py

Validador determinístico pós-LLM, umbrella de TODOS os safety guards. Roda
DEPOIS de qualquer skill, ANTES do analyst.

Curto-circuito #1 — modo pré-atendimento (capability `inventory.track_stock`
OFF): passthrough total. O fluxo de balcão é "anotar pedido + transferir",
sem catálogo autoritativo, sem preços precisos, sem checagem de receita.
Forçar guards aqui só atrapalha.

Curto-circuito #2 — cada sub-guard é gated pela sua capability própria
(`safety.availability_guard`, `safety.price_guard`,
`safety.prescription_guard`, `safety.delivery_guard`). Default ON; o operador
desliga em /portal/recursos se quiser "afrouxar" as regras.

Composição da correção: quando múltiplos sub-guards detectam problema na
mesma resposta, concatena as correções em ordem de severidade (receita >
preço > disponibilidade > frete). NUNCA levanta exceção (passthrough em
erro — fail-open por design).
"""
from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger()


# Capability que indica "modo normal" (ERP). OFF = pré-atendimento → passthrough.
_TRACK_STOCK_CAPABILITY = "inventory.track_stock"

_AVAILABILITY_CAP   = "safety.availability_guard"
_PRICE_CAP          = "safety.price_guard"
_PRESCRIPTION_CAP   = "safety.prescription_guard"
_DELIVERY_CAP       = "safety.delivery_guard"


async def safety_guard(state: dict[str, Any]) -> dict[str, Any]:
    tenant_id = state.get("tenant_id")
    response  = (state.get("final_response") or "").strip()
    if not response:
        return state

    # ── Curto-circuito #1: modo pré-atendimento ─────────────────────────────
    try:
        from services import capabilities as cap_svc
        if not await cap_svc.is_enabled(tenant_id, _TRACK_STOCK_CAPABILITY):
            # Pré-atendimento: passthrough. Fluxo curto e enxuto.
            return state
    except Exception as exc:  # noqa: BLE001
        log.warning("safety_guard.mode_check_failed",
                    tenant=tenant_id, exc=str(exc))
        return state

    cart = state.get("cart") or {}
    search_results = cart.get("_search_results_this_turn") or []

    # Coleta de issues por sub-guard
    corrections: list[str] = []
    issues_log: dict[str, Any] = {}

    # ── prescription (mais crítico — segurança/regulatório) ─────────────────
    try:
        if await cap_svc.is_enabled(tenant_id, _PRESCRIPTION_CAP):
            from services.prescription_guard import (
                detect_prescription_issues, build_correction_message,
            )
            issues = detect_prescription_issues(response, search_results)
            if issues:
                corrections.append(build_correction_message(issues))
                issues_log["prescription"] = issues
    except Exception as exc:  # noqa: BLE001
        log.warning("safety_guard.prescription_failed",
                    tenant=tenant_id, exc=str(exc))

    # ── price ──────────────────────────────────────────────────────────────
    try:
        if await cap_svc.is_enabled(tenant_id, _PRICE_CAP):
            from services.price_guard import (
                detect_price_issues, build_correction_message as _bld_price,
            )
            issues = detect_price_issues(response, search_results)
            if issues:
                corrections.append(_bld_price(issues))
                issues_log["price"] = issues
    except Exception as exc:  # noqa: BLE001
        log.warning("safety_guard.price_failed",
                    tenant=tenant_id, exc=str(exc))

    # ── availability ───────────────────────────────────────────────────────
    try:
        if await cap_svc.is_enabled(tenant_id, _AVAILABILITY_CAP):
            from services.availability_guard import (
                detect_hallucinations, build_correction_message as _bld_avail,
            )
            issues = detect_hallucinations(response, search_results)
            if issues:
                # Availability é a mais severa quando dispara — ela REESCREVE
                # a resposta inteira em vez de só acrescentar correção.
                # Se outras correções já foram coletadas, anexamos.
                corrections.append(_bld_avail(issues))
                issues_log["availability"] = issues
    except Exception as exc:  # noqa: BLE001
        log.warning("safety_guard.availability_failed",
                    tenant=tenant_id, exc=str(exc))

    # ── delivery (async — consulta tenant_shipping_rules) ──────────────────
    try:
        if await cap_svc.is_enabled(tenant_id, _DELIVERY_CAP):
            from services.delivery_guard import (
                detect_delivery_issues, build_correction_message as _bld_del,
            )
            issues = await detect_delivery_issues(response, tenant_id=tenant_id)
            if issues:
                corrections.append(_bld_del(issues))
                issues_log["delivery"] = issues
    except Exception as exc:  # noqa: BLE001
        log.warning("safety_guard.delivery_failed",
                    tenant=tenant_id, exc=str(exc))

    if not corrections:
        return state

    # Combina correções. Se houver alucinação de availability, ela domina —
    # reescrevemos a resposta inteira. Caso contrário, prependamos as
    # correções à resposta original (cliente vê o aviso ANTES do conteúdo).
    if "availability" in issues_log:
        corrected = "\n\n".join(corrections)
    else:
        corrected = "\n\n".join([*corrections, response])

    log.warning(
        "safety_guard.correction_applied",
        tenant=tenant_id,
        guards_fired=list(issues_log.keys()),
        issues=issues_log,
        original_preview=response[:200],
        corrected_preview=corrected[:200],
    )

    return {**state, "final_response": corrected}
