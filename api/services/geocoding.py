"""
Geocoding de CEP brasileiro → coordenadas (lat/lng) + distância geográfica.

Usado pelo frete por distância (capability `delivery.shipping_by_cep`, modo
`distance`): resolve a origem (CEP da farmácia) e o destino (CEP do cliente) em
coordenadas e mede a distância em km via fórmula de haversine.

Estratégia de resolução (todas gratuitas, sem auth), na ordem:
  1. BrasilAPI CEP v2  — https://brasilapi.com.br/api/cep/v2/{cep}
     devolve `location.coordinates.{latitude,longitude}` quando disponível.
  2. AwesomeAPI CEP     — https://cep.awesomeapi.com.br/json/{cep}
     devolve `lat`/`lng` (fallback quando a BrasilAPI não traz coordenada).

Nunca lança: falha "fechada" (retorna None) para o caller decidir o fallback
(ex.: cair para a tabela de CEP). Cache em memória por processo (CEP de origem
muda raramente; CEPs de cliente repetem dentro da mesma região).
"""
from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger()

_CEP_DIGITS = re.compile(r"\d")

# Cache simples por processo: cep8 -> (timestamp, GeoPoint|None)
_CACHE: dict[str, tuple[float, "GeoPoint | None"]] = {}
_CACHE_TTL_SECONDS = 60 * 60 * 24  # 24h — coordenada de CEP é estável


@dataclass
class GeoPoint:
    lat: float
    lng: float
    address: str = ""


def normalize_cep(cep: str) -> str | None:
    """8 dígitos do CEP (sem hífen) ou None se inválido."""
    digits = "".join(_CEP_DIGITS.findall(cep or ""))
    return digits if len(digits) == 8 else None


def _to_float(v) -> float | None:
    try:
        f = float(str(v).strip())
        return f if f != 0.0 else None
    except (TypeError, ValueError):
        return None


async def _from_brasilapi(client: httpx.AsyncClient, cep8: str) -> GeoPoint | None:
    resp = await client.get(f"https://brasilapi.com.br/api/cep/v2/{cep8}")
    if resp.status_code != 200:
        return None
    data = resp.json()
    coords = (data.get("location") or {}).get("coordinates") or {}
    lat = _to_float(coords.get("latitude"))
    lng = _to_float(coords.get("longitude"))
    if lat is None or lng is None:
        return None
    addr = ", ".join(
        p for p in (data.get("street"), data.get("neighborhood"),
                    data.get("city"), data.get("state")) if p
    )
    return GeoPoint(lat=lat, lng=lng, address=addr)


async def _from_awesomeapi(client: httpx.AsyncClient, cep8: str) -> GeoPoint | None:
    resp = await client.get(f"https://cep.awesomeapi.com.br/json/{cep8}")
    if resp.status_code != 200:
        return None
    data = resp.json()
    lat = _to_float(data.get("lat"))
    lng = _to_float(data.get("lng"))
    if lat is None or lng is None:
        return None
    addr = ", ".join(
        p for p in (data.get("address"), data.get("district"),
                    data.get("city"), data.get("state")) if p
    )
    return GeoPoint(lat=lat, lng=lng, address=addr)


async def geocode_cep(cep: str, *, timeout: float = 8.0) -> GeoPoint | None:
    """
    Resolve um CEP em coordenadas. Retorna GeoPoint ou None (CEP inválido,
    não geocodificável, ou todas as fontes falharam). Cacheado 24h.
    """
    cep8 = normalize_cep(cep)
    if cep8 is None:
        return None

    now = time.time()
    cached = _CACHE.get(cep8)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    result: GeoPoint | None = None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            for source in (_from_brasilapi, _from_awesomeapi):
                try:
                    result = await source(client, cep8)
                except Exception as exc:  # noqa: BLE001 — rede/parse, tenta o próximo
                    log.warning("geocoding.source_failed",
                                source=source.__name__, cep=cep8, exc=str(exc))
                    result = None
                if result is not None:
                    break
    except Exception as exc:  # noqa: BLE001
        log.warning("geocoding.client_failed", cep=cep8, exc=str(exc))
        result = None

    _CACHE[cep8] = (now, result)
    return result


# Cache de rota Google: (origin_arredondado, cep8) -> (timestamp, km|None)
_ROUTE_CACHE: dict[tuple, tuple[float, "float | None"]] = {}


async def google_distance_km(
    origin_lat: float,
    origin_lng: float,
    dest_cep: str,
    *,
    api_key: str,
    timeout: float = 8.0,
) -> float | None:
    """
    Distância de ROTA REAL (rua, modo carro) entre a origem e o CEP do cliente,
    via Google Distance Matrix API. Retorna km ou None (sem chave, CEP inválido,
    rota não encontrada, ou API falhou) — o caller decide o fallback (haversine).

    Cobrado pela plataforma (1 elemento por consulta). Cacheado 24h por
    (origem, CEP) — o mesmo cliente reconsultando não gera nova cobrança.
    """
    cep8 = normalize_cep(dest_cep)
    if cep8 is None or not api_key:
        return None

    key = (round(origin_lat, 4), round(origin_lng, 4), cep8)
    now = time.time()
    cached = _ROUTE_CACHE.get(key)
    if cached and (now - cached[0]) < _CACHE_TTL_SECONDS:
        return cached[1]

    km: float | None = None
    try:
        params = {
            "origins": f"{origin_lat},{origin_lng}",
            "destinations": f"{cep8[:5]}-{cep8[5:]}, Brasil",
            "units": "metric",
            "mode": "driving",
            "key": api_key,
        }
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                "https://maps.googleapis.com/maps/api/distancematrix/json",
                params=params,
            )
        if resp.status_code == 200:
            data = resp.json()
            if data.get("status") == "OK":
                elem = (data.get("rows") or [{}])[0].get("elements", [{}])[0]
                if elem.get("status") == "OK":
                    meters = (elem.get("distance") or {}).get("value")
                    if isinstance(meters, (int, float)):
                        km = meters / 1000.0
            else:
                log.warning("geocoding.google_distance.status",
                            status=data.get("status"),
                            msg=data.get("error_message"))
    except Exception as exc:  # noqa: BLE001 — rede/parse, falha fechada
        log.warning("geocoding.google_distance.failed", cep=cep8, exc=str(exc))
        km = None

    _ROUTE_CACHE[key] = (now, km)
    return km


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Distância em km entre dois pontos (linha reta, fórmula de haversine)."""
    r = 6371.0  # raio médio da Terra em km
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = (math.sin(dphi / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2)
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
