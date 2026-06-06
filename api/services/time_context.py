"""
Per-turn temporal context for the skills.

Quando a capability `attendance.time_aware_greeting` está ATIVA, este helper
gera um bloco VOLÁTIL (não vai no prefixo cacheado) informando ao agente a
hora atual e a saudação de período correspondente.

Fuso fixo em America/Sao_Paulo — mesmo padrão dos jobs proativos
(workers/jobs/abandoned_cart.py). Quando suportarmos coluna `timezone`
per-tenant, passar `tz=...` direto no `build_time_context_block`.

NUNCA mover este bloco para o system_prompt estável: o conteúdo muda toda
hora e quebraria o prompt cache.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

try:
    _BR_TZ = ZoneInfo("America/Sao_Paulo")
except Exception:  # noqa: BLE001 — falta tzdata (dev local Windows), cai p/ UTC-3 fixo
    _BR_TZ = timezone(timedelta(hours=-3), name="UTC-3")


def _period_for_hour(hour: int) -> tuple[str, str]:
    """Retorna (rótulo_curto, saudação) para a hora dada (0–23).

    Faixas:
      00h–05h59 → madrugada (Boa madrugada)
      06h–11h59 → manhã     (Bom dia)
      12h–17h59 → tarde     (Boa tarde)
      18h–23h59 → noite     (Boa noite)
    """
    if 0 <= hour < 6:
        return "madrugada", "Boa madrugada"
    if 6 <= hour < 12:
        return "manhã", "Bom dia"
    if 12 <= hour < 18:
        return "tarde", "Boa tarde"
    return "noite", "Boa noite"


def build_time_context_block(
    now: datetime | None = None,
    tz=None,
) -> str:
    """
    Bloco volátil de contexto temporal para injetar no system_prompt do skill.

    Args:
        now: datetime atual (default = `datetime.now(tz)`). Recebe override
             principalmente para testes; em produção deixe em None.
        tz:  fuso horário (default = America/Sao_Paulo). Quando houver
             tenant.timezone, passar aqui.

    Returns:
        String pronta para `volatile_parts.append(...)`. Sempre não-vazia
        (o caller já fez o gate na capability).
    """
    zone = tz or _BR_TZ
    current = now or datetime.now(zone)
    if current.tzinfo is None:
        current = current.replace(tzinfo=zone)
    else:
        current = current.astimezone(zone)

    period, greeting = _period_for_hour(current.hour)
    return (
        "[CONTEXTO TEMPORAL — hora atual no fuso da farmácia]\n"
        f"Agora são {current:%H:%M} — período: **{period}**.\n"
        f"Se for usar saudação de período, use exatamente \"{greeting}\". "
        f"NUNCA diga \"bom dia\" à tarde/noite, \"boa tarde\" de manhã/noite, "
        f"\"boa noite\" de dia, etc. Em conversas que não pedem saudação "
        f"(continuação de atendimento, follow-up de pedido), ignore este bloco."
    )
