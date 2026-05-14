"""
Transactional email via Resend (resend.com).
Falls back to a no-op log if RESEND_API_KEY is not configured.
"""
from __future__ import annotations

import structlog
from config import settings

log = structlog.get_logger()


async def send_email(to: str, subject: str, html: str) -> bool:
    if not settings.resend_api_key:
        log.warning("email.no_api_key", to=to, subject=subject)
        return False

    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            json={"from": settings.email_from, "to": [to], "subject": subject, "html": html},
        )
    if resp.status_code >= 400:
        log.error("email.send_failed", to=to, status=resp.status_code)
        return False
    log.info("email.sent", to=to, subject=subject)
    return True


# ── Email templates ───────────────────────────────────────────────────────────

async def send_welcome(to: str, name: str, tenant_name: str, password: str) -> None:
    html = f"""
    <h2>Bem-vindo ao Agente Farmácia IA, {name}!</h2>
    <p>Sua conta da farmácia <strong>{tenant_name}</strong> foi criada com sucesso.</p>
    <p><strong>E-mail:</strong> {to}<br>
       <strong>Senha temporária:</strong> <code>{password}</code></p>
    <p>Acesse o portal e altere sua senha no primeiro login.</p>
    """
    await send_email(to, f"Bem-vindo ao Agente Farmácia IA — {tenant_name}", html)


async def send_invoice_paid(to: str, tenant_name: str, amount: float, period: str) -> None:
    html = f"""
    <h2>Pagamento confirmado!</h2>
    <p>Farmácia <strong>{tenant_name}</strong> — período {period}</p>
    <p>Valor: R$ {amount:.2f}</p>
    <p>Obrigado pela preferência!</p>
    """
    await send_email(to, f"Pagamento confirmado — {tenant_name}", html)


async def send_usage_alert(to: str, tenant_name: str, current: int, limit: int) -> None:
    pct = int(current / limit * 100)
    html = f"""
    <h2>Alerta de uso — {tenant_name}</h2>
    <p>Você utilizou <strong>{current}/{limit} mensagens ({pct}%)</strong> do seu plano este mês.</p>
    <p>Considere fazer upgrade para evitar interrupção do serviço.</p>
    """
    await send_email(to, f"Alerta: {pct}% do limite de mensagens usado", html)
