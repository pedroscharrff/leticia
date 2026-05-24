"""
Capabilities service — feature flags plug-and-play por tenant.

Cada capability é uma capacidade do bot que o tenant liga/desliga de forma
INDEPENDENTE. Este service é o ponto único de verdade: agentes, tools, jobs
e endpoints consultam aqui se uma capability está habilitada para um tenant
e qual é a config efetiva (default do catálogo + override do tenant).

Performance:
  • is_enabled() e get_config() são cacheados em Redis por 60s.
  • A invalidação acontece em set_enabled() (apaga a chave do tenant).

Decorator:
  @with_capability("sales.cross_sell", default=None)
  async def recomendar_complementos(...) -> ...

  Quando a capability está OFF, a função retorna `default` sem executar.
"""
from __future__ import annotations

import asyncio
import functools
import inspect
import json
from typing import Any, Callable, Iterable

import structlog
from fastapi import HTTPException

from db.postgres import get_db_conn
from db.redis_client import get_redis

log = structlog.get_logger()


_PLAN_RANK = {"basic": 0, "pro": 1, "enterprise": 2}
_CACHE_TTL_SECONDS = 60
_CACHE_PREFIX = "cap:"  # cap:{tenant_id} -> JSON {key: {enabled, config}}


# ── Cache helpers ────────────────────────────────────────────────────────────

def _cache_key(tenant_id: str) -> str:
    return f"{_CACHE_PREFIX}{tenant_id}"


async def _load_tenant_state(tenant_id: str) -> dict[str, dict]:
    """Retorna `{capability_key: {"enabled": bool, "config": dict}}`.

    Mescla catálogo (default_enabled, default_config) com overrides do
    tenant. Cacheado em Redis para evitar hit no Postgres a cada turno do bot.
    """
    redis = None
    try:
        redis = get_redis()
        cached = await redis.get(_cache_key(tenant_id))
        if cached:
            return json.loads(cached)
    except Exception as exc:
        log.warning("capabilities.cache.read_failed", tenant=tenant_id, exc=str(exc))

    async with get_db_conn() as conn:
        rows = await conn.fetch(
            """
            SELECT c.key,
                   c.default_enabled,
                   c.default_config,
                   tc.enabled AS tenant_enabled,
                   tc.config  AS tenant_config
              FROM public.capability_catalog c
              LEFT JOIN public.tenant_capabilities tc
                ON tc.capability_key = c.key
               AND tc.tenant_id      = $1
            """,
            tenant_id,
        )

    state: dict[str, dict] = {}
    for r in rows:
        enabled = r["tenant_enabled"]
        if enabled is None:
            enabled = r["default_enabled"]

        # Merge configs: default do catálogo + override do tenant (vence)
        default_cfg = _ensure_dict(r["default_config"])
        tenant_cfg  = _ensure_dict(r["tenant_config"])
        merged_cfg  = {**default_cfg, **tenant_cfg}

        state[r["key"]] = {"enabled": bool(enabled), "config": merged_cfg}

    # Best-effort cache write
    if redis is not None:
        try:
            await redis.setex(_cache_key(tenant_id), _CACHE_TTL_SECONDS, json.dumps(state))
        except Exception as exc:
            log.warning("capabilities.cache.write_failed", tenant=tenant_id, exc=str(exc))

    return state


def _ensure_dict(value: Any) -> dict:
    """Normaliza JSONB que pode vir como str (asyncpg) ou dict."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return {}


async def invalidate_cache(tenant_id: str) -> None:
    try:
        redis = get_redis()
        await redis.delete(_cache_key(tenant_id))
    except Exception as exc:
        log.warning("capabilities.cache.invalidate_failed", tenant=tenant_id, exc=str(exc))


# ── Public API ───────────────────────────────────────────────────────────────

async def is_enabled(tenant_id: str | None, key: str) -> bool:
    """True quando a capability está ON para o tenant.

    Tolerante a falhas: se algo quebra (Redis off, DB off, tenant_id ausente),
    retorna False — o caller cai no comportamento padrão sem-feature, nunca
    "vaza" feature paga por erro de leitura.
    """
    if not tenant_id:
        return False
    try:
        state = await _load_tenant_state(tenant_id)
        return bool(state.get(key, {}).get("enabled", False))
    except Exception as exc:
        log.warning("capabilities.is_enabled.failed",
                    tenant=tenant_id, key=key, exc=str(exc))
        return False


async def get_config(tenant_id: str | None, key: str) -> dict:
    if not tenant_id:
        return {}
    try:
        state = await _load_tenant_state(tenant_id)
        return dict(state.get(key, {}).get("config", {}))
    except Exception as exc:
        log.warning("capabilities.get_config.failed",
                    tenant=tenant_id, key=key, exc=str(exc))
        return {}


async def list_for_tenant(tenant_id: str) -> list[dict]:
    """Retorna o catálogo INTEIRO com status atual para a UI do portal.

    Cada item inclui o motivo do bloqueio (se houver) para a UI explicar ao
    usuário por que ele não pode ativar (plano, dependência, secret).
    """
    async with get_db_conn() as conn:
        plan_row = await conn.fetchrow(
            "SELECT plan FROM public.tenants WHERE id = $1", tenant_id
        )
        tenant_plan = (plan_row["plan"] if plan_row else "basic") or "basic"

        catalog = await conn.fetch(
            """
            SELECT key, name, category, short_desc, long_desc, impact_label,
                   min_plan, depends_on, requires_secret, config_schema,
                   default_config, default_enabled, status, icon, sort_order
              FROM public.capability_catalog
             ORDER BY sort_order, name
            """
        )

        overrides = await conn.fetch(
            """
            SELECT capability_key, enabled, config, updated_at, updated_by
              FROM public.tenant_capabilities
             WHERE tenant_id = $1
            """,
            tenant_id,
        )

        secrets = await conn.fetch(
            "SELECT key FROM public.tenant_secrets WHERE tenant_id = $1",
            tenant_id,
        )

    overrides_by_key = {r["capability_key"]: r for r in overrides}
    secret_set = {r["key"] for r in secrets}

    items: list[dict] = []
    for c in catalog:
        ov          = overrides_by_key.get(c["key"])
        default_cfg = _ensure_dict(c["default_config"])
        tenant_cfg  = _ensure_dict(ov["config"]) if ov else {}
        enabled     = ov["enabled"] if ov else c["default_enabled"]

        # Pré-requisitos
        plan_ok    = _PLAN_RANK.get(tenant_plan, 0) >= _PLAN_RANK.get(c["min_plan"], 0)
        missing_secrets = [s for s in (c["requires_secret"] or []) if s not in secret_set]
        missing_deps    = [d for d in (c["depends_on"]      or [])]  # validado abaixo

        blockers: list[dict] = []
        if not plan_ok:
            blockers.append({
                "type": "plan",
                "message": f"Disponível no plano {c['min_plan'].capitalize()} ou superior.",
                "min_plan": c["min_plan"],
            })
        if missing_secrets:
            blockers.append({
                "type": "secret",
                "message": f"Conecte primeiro: {', '.join(missing_secrets)}.",
                "secrets": missing_secrets,
            })

        items.append({
            "key":            c["key"],
            "name":           c["name"],
            "category":       c["category"],
            "short_desc":     c["short_desc"],
            "long_desc":      c["long_desc"],
            "impact_label":   c["impact_label"],
            "min_plan":       c["min_plan"],
            "depends_on":     list(c["depends_on"] or []),
            "requires_secret": list(c["requires_secret"] or []),
            "config_schema":  _ensure_dict(c["config_schema"]),
            "default_config": default_cfg,
            "config":         {**default_cfg, **tenant_cfg},
            "default_enabled": c["default_enabled"],
            "enabled":        bool(enabled),
            "status":         c["status"],
            "icon":           c["icon"],
            "sort_order":     c["sort_order"],
            "blockers":       blockers,
            "available":      not blockers,
            "updated_at":     ov["updated_at"].isoformat() if ov and ov["updated_at"] else None,
            "updated_by":     ov["updated_by"] if ov else None,
        })

    # Resolve dependências em uma segunda passada (precisa do estado completo)
    by_key = {it["key"]: it for it in items}
    for it in items:
        missing_deps_pretty: list[str] = []
        for dep in it["depends_on"]:
            dep_item = by_key.get(dep)
            if not dep_item or not dep_item["enabled"]:
                missing_deps_pretty.append(dep_item["name"] if dep_item else dep)
        if missing_deps_pretty:
            it["blockers"].append({
                "type": "dependency",
                "message": f"Requer outras capacidades ativas: {', '.join(missing_deps_pretty)}.",
                "depends_on": it["depends_on"],
            })
            it["available"] = False

    return items


async def set_enabled(
    tenant_id: str,
    key: str,
    enabled: bool,
    config: dict | None,
    user_id: str | None,
) -> dict:
    """Persiste flag + config para o tenant.

    Valida plano, secrets e dependências ANTES de habilitar. Se algum
    requisito falhar, levanta HTTPException com mensagem clara.

    Devolve o item atualizado (mesmo shape do list_for_tenant).
    """
    async with get_db_conn() as conn:
        cap = await conn.fetchrow(
            "SELECT * FROM public.capability_catalog WHERE key = $1", key
        )
        if not cap:
            raise HTTPException(status_code=404, detail=f"Capacidade '{key}' não existe.")

        if enabled:
            # Plano
            plan_row = await conn.fetchrow(
                "SELECT plan FROM public.tenants WHERE id = $1", tenant_id
            )
            tenant_plan = (plan_row["plan"] if plan_row else "basic") or "basic"
            if _PLAN_RANK.get(tenant_plan, 0) < _PLAN_RANK.get(cap["min_plan"], 0):
                raise HTTPException(
                    status_code=402,
                    detail=f"Esta capacidade requer o plano {cap['min_plan'].capitalize()} "
                           f"ou superior. Faça upgrade para ativá-la.",
                )

            # Secrets
            if cap["requires_secret"]:
                rows = await conn.fetch(
                    "SELECT key FROM public.tenant_secrets WHERE tenant_id = $1 "
                    "AND key = ANY($2::text[])",
                    tenant_id, list(cap["requires_secret"]),
                )
                have = {r["key"] for r in rows}
                missing = [s for s in cap["requires_secret"] if s not in have]
                if missing:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Antes de ativar, conecte estes recursos: {', '.join(missing)}.",
                    )

            # Dependências (precisam estar enabled)
            if cap["depends_on"]:
                rows = await conn.fetch(
                    """
                    SELECT c.key, c.default_enabled, tc.enabled
                      FROM public.capability_catalog c
                      LEFT JOIN public.tenant_capabilities tc
                        ON tc.capability_key = c.key AND tc.tenant_id = $1
                     WHERE c.key = ANY($2::text[])
                    """,
                    tenant_id, list(cap["depends_on"]),
                )
                disabled = [
                    r["key"] for r in rows
                    if not (r["enabled"] if r["enabled"] is not None else r["default_enabled"])
                ]
                if disabled:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Ative antes as capacidades dependentes: {', '.join(disabled)}.",
                    )

        # Persist
        final_config = config if config is not None else _ensure_dict(cap["default_config"])
        await conn.execute(
            """
            INSERT INTO public.tenant_capabilities
                (tenant_id, capability_key, enabled, config, updated_at, updated_by)
            VALUES ($1, $2, $3, $4::jsonb, NOW(), $5)
            ON CONFLICT (tenant_id, capability_key) DO UPDATE
               SET enabled    = EXCLUDED.enabled,
                   config     = EXCLUDED.config,
                   updated_at = NOW(),
                   updated_by = EXCLUDED.updated_by
            """,
            tenant_id, key, enabled, json.dumps(final_config), user_id,
        )

    await invalidate_cache(tenant_id)
    log.info("capabilities.set", tenant=tenant_id, key=key,
             enabled=enabled, by=user_id)

    # Devolve item atualizado
    items = await list_for_tenant(tenant_id)
    for it in items:
        if it["key"] == key:
            return it
    return {"key": key, "enabled": enabled, "config": final_config}


# ── Decorator ────────────────────────────────────────────────────────────────

def with_capability(key: str, *, default: Any = None, tenant_arg: str = "tenant_id"):
    """Curto-circuita a função quando a capability está OFF para o tenant.

    A função decorada precisa receber `tenant_id` (kwarg ou primeiro arg
    posicional). Quando a flag está OFF, retorna `default` sem executar.

    Uso:
        @with_capability("sales.cross_sell", default=[])
        async def recomendar_complementos(*, tenant_id, product_id):
            ...
    """
    def _decorator(fn: Callable):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        async def _wrapper(*args, **kwargs):
            bound = sig.bind_partial(*args, **kwargs)
            tenant_id = bound.arguments.get(tenant_arg)
            if not await is_enabled(tenant_id, key):
                return default
            return await fn(*args, **kwargs)

        return _wrapper

    return _decorator
