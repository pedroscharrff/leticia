"""
services/handoff.py

Transferência de conversas do agente para atendente humano (balcão).

Fluxo:
    1. Agente decide que precisa de humano (escalate=True) OU
       cliente pediu transferência via keyword ("atendente", "humano", ...).
    2. Chamamos a API externa do PDV/CRM (ex.: ClickMassa / TalkFarma) que
       cria o ticket numa fila de atendimento humano.

Hoje só suportamos o provider "clickmassa" (TalkFarma). Outros providers
podem ser plugados pelo mesmo dispatcher.

Config esperada (campo `handoff_config` em public.tenant_integrations):

    {
        "enabled": true,
        "provider": "clickmassa",
        "base_url": "https://chatapi.talkfarma.pro/v1/api/external/<uuid>",
        "token": "<jwt>",
        "queue_id": 4,
        "transfer_message": "Vou te transferir...",
        "trigger_keywords": ["atendente", "humano", "balcão"],
        "post_handoff_order": "summary_first"  // ou "offers_first" para inverter
    }
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

log = structlog.get_logger()


# Palavras que, se aparecerem na mensagem do cliente, disparam transferência
# automática — mesmo que o agente não tenha pedido escalate. O tenant pode
# sobrescrever esta lista em `handoff_config.trigger_keywords`.
DEFAULT_TRIGGER_KEYWORDS = [
    "atendente",
    "humano",
    "pessoa",
    "balcão",
    "balcao",
    "falar com alguém",
    "falar com alguem",
    "atendimento humano",
]


def should_handoff(
    handoff_cfg: dict[str, Any] | None,
    *,
    agent_escalate: bool,
    user_message: str,
) -> tuple[bool, str]:
    """
    Decide se a conversa deve ir para o balcão.

    Retorna (True/False, motivo) — o motivo é usado no log/evento.
    """
    if not handoff_cfg or not handoff_cfg.get("enabled"):
        return False, ""

    if agent_escalate:
        return True, "agent_escalate"

    keywords = handoff_cfg.get("trigger_keywords") or DEFAULT_TRIGGER_KEYWORDS
    msg = (user_message or "").lower()
    for kw in keywords:
        if not kw:
            continue
        if str(kw).lower() in msg:
            return True, f"keyword:{kw}"

    return False, ""


async def send_clickmassa_message(
    handoff_cfg: dict[str, Any],
    *,
    phone: str,
    body: str,
    external_key: str = "123456",
) -> dict[str, Any]:
    """
    Envia uma mensagem simples pelo endpoint externo da ClickMassa/TalkFarma.

    Diferente de `transfer_to_human`, NÃO cria ticket nem força departamento —
    apenas dispara uma mensagem de saída. Usado por notificações automáticas
    (ex.: troca de status de pedido) quando o canal tem ClickMassa ativa.

    URL: {base_url}/?token={token}
    Body: {"number": phone, "externalKey": external_key, "body": body}
    """
    base_url = (handoff_cfg.get("base_url") or "").strip().rstrip("/")
    token    = (handoff_cfg.get("token") or "").strip()

    missing = []
    if not base_url: missing.append("base_url")
    if not token:    missing.append("token")
    if not phone:    missing.append("phone")
    if not body:     missing.append("body")
    if missing:
        return {
            "ok": False, "status_code": None, "response": None,
            "error": f"Config ClickMassa incompleta. Faltando: {', '.join(missing)}",
        }

    url = f"{base_url}/?token={token}"
    payload = {"number": phone, "externalKey": external_key, "body": body}

    log.info("clickmassa.send.dispatching",
             url_prefix=base_url, phone_prefix=phone[:4])
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {"_text": resp.text[:2000]}
        ok = 200 <= resp.status_code < 300
        if ok:
            log.info("clickmassa.send.success", status=resp.status_code)
        else:
            log.warning("clickmassa.send.bad_status",
                        status=resp.status_code, preview=str(data)[:300])
        return {
            "ok": ok, "status_code": resp.status_code, "response": data,
            "error": None if ok else f"API ClickMassa retornou {resp.status_code}",
        }
    except Exception as exc:  # noqa: BLE001
        log.error("clickmassa.send.failed", error=str(exc))
        return {
            "ok": False, "status_code": None, "response": None,
            "error": f"Falha ao conectar ClickMassa: {exc}",
        }


async def transfer_to_human(
    handoff_cfg: dict[str, Any],
    *,
    phone: str,
    custom_message: str | None = None,
) -> dict[str, Any]:
    """
    Executa a transferência via API externa.

    Args:
        handoff_cfg: bloco lido de tenant_integrations.handoff_config
        phone:       número do cliente (mapeado pelo broker, só dígitos)
        custom_message: sobrescreve handoff_cfg["transfer_message"] se passado

    Returns:
        {"ok": bool, "status_code": int|None, "response": dict|None, "error": str|None}
    """
    provider = (handoff_cfg.get("provider") or "clickmassa").lower()
    if provider != "clickmassa":
        return {
            "ok": False,
            "status_code": None,
            "response": None,
            "error": f"Provider '{provider}' não suportado ainda. Use 'clickmassa'.",
        }

    base_url = (handoff_cfg.get("base_url") or "").strip().rstrip("/")
    token = (handoff_cfg.get("token") or "").strip()
    queue_id = handoff_cfg.get("queue_id")
    transfer_message = (custom_message
                        or handoff_cfg.get("transfer_message")
                        or "Vou te transferir para um de nossos atendentes agora.").strip()

    # Validação mínima
    missing = []
    if not base_url:
        missing.append("base_url")
    if not token:
        missing.append("token")
    if queue_id in (None, ""):
        missing.append("queue_id")
    if not phone:
        missing.append("phone")
    if missing:
        return {
            "ok": False,
            "status_code": None,
            "response": None,
            "error": f"Config de handoff incompleta. Faltando: {', '.join(missing)}",
        }

    try:
        queue_id_int = int(queue_id)
    except (ValueError, TypeError):
        return {
            "ok": False,
            "status_code": None,
            "response": None,
            "error": f"queue_id deve ser número inteiro (recebido: {queue_id!r})",
        }

    # URL final: {base_url}/?token={token}
    url = f"{base_url}/?token={token}"
    body = {
        "number": phone,
        "body": transfer_message,
        "forceTicketToDepartment": True,
        "queueId": queue_id_int,
        "externalKey": "123456",
    }

    log.info("handoff.dispatching", url_prefix=base_url, queue_id=queue_id_int,
             phone_prefix=phone[:4])

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json=body)
        try:
            payload = resp.json()
        except Exception:
            payload = {"_text": resp.text[:2000]}

        ok = 200 <= resp.status_code < 300
        if ok:
            log.info("handoff.success", status=resp.status_code, queue_id=queue_id_int)
        else:
            log.warning("handoff.bad_status", status=resp.status_code,
                        response_preview=str(payload)[:300])
        return {
            "ok": ok,
            "status_code": resp.status_code,
            "response": payload,
            "error": None if ok else f"API externa retornou {resp.status_code}",
        }
    except Exception as exc:
        log.error("handoff.failed", error=str(exc))
        return {
            "ok": False,
            "status_code": None,
            "response": None,
            "error": f"Falha ao conectar com a API de transferência: {exc}",
        }
