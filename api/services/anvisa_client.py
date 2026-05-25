"""
Cliente HTTP para a API pública da ANVISA (consultas.anvisa.gov.br).

Endpoints não-documentados oficialmente mas estáveis há anos — os mesmos
consumidos pela lib JS `bulario`. Header essencial: `Authorization: Guest`.

⚠️  Cloudflare na frente da API detecta TLS fingerprint. Cliente comum
(httpx, requests, aiohttp) toma 403 antes mesmo de mandar headers. Por isso
usamos `curl_cffi` com `impersonate="chrome120"` — libcurl com handshake
TLS idêntico ao Chrome real.

Uso:
    async with AnvisaClient() as cli:
        results = await cli.search("dipirona")
        detail  = await cli.detail(num_processo)
"""
from __future__ import annotations

import asyncio
from typing import Any

import structlog
from curl_cffi.requests import AsyncSession
from curl_cffi.requests.errors import RequestsError

log = structlog.get_logger()

BASE_URL = "https://consultas.anvisa.gov.br/api"
PORTAL_URL = "https://consultas.anvisa.gov.br/"

# Authorization: Guest é o crítico pra API. Resto é completude — o TLS
# fingerprint via impersonate cobre o que o Cloudflare olha primeiro.
_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Authorization": "Guest",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": PORTAL_URL,
    "Origin": "https://consultas.anvisa.gov.br",
}

# Versão do Chrome impersonada. curl_cffi cuida do TLS + headers HTTP/2.
_IMPERSONATE = "chrome120"


class AnvisaError(Exception):
    """Falha ao consultar a API da ANVISA."""


class AnvisaClient:
    """
    Cliente assíncrono e throttled. Reutilize a instância (uma por processo
    em produção, via singleton no startup do FastAPI). Para scripts/tests,
    use como context manager.
    """

    def __init__(self, *, timeout: float = 15.0, max_concurrent: int = 2):
        self._session = AsyncSession(
            headers=_HEADERS,
            timeout=timeout,
            impersonate=_IMPERSONATE,
        )
        # Throttle conservador — endpoint não-documentado, evita rate limit.
        self._sem = asyncio.Semaphore(max_concurrent)
        self._warmed_up = False
        self._warmup_lock = asyncio.Lock()

    async def __aenter__(self) -> "AnvisaClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._session.close()

    async def _warmup(self) -> None:
        """
        Visita o portal pra coletar cookies do Cloudflare antes de bater na
        API. Com curl_cffi+impersonate normalmente passa de primeira.
        """
        if self._warmed_up:
            return
        async with self._warmup_lock:
            if self._warmed_up:
                return
            try:
                resp = await self._session.get(PORTAL_URL)
                self._warmed_up = True
                log.info(
                    "anvisa.warmup.ok",
                    status=resp.status_code,
                    cookies=len(self._session.cookies.jar),
                )
            except RequestsError as exc:
                log.warning("anvisa.warmup.failed", exc=str(exc))

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._warmup()
        url = BASE_URL + path
        async with self._sem:
            try:
                resp = await self._session.get(url, params=params)
            except RequestsError as exc:
                log.warning("anvisa.network_error", path=path, exc=str(exc))
                raise AnvisaError(f"ANVISA network error: {exc}") from exc

            if resp.status_code >= 400:
                log.warning(
                    "anvisa.http_error",
                    path=path,
                    status=resp.status_code,
                    body=resp.text[:300],
                )
                raise AnvisaError(f"ANVISA HTTP {resp.status_code}")

            try:
                return resp.json()
            except ValueError as exc:
                log.warning("anvisa.json_decode_error", path=path, body=resp.text[:300])
                raise AnvisaError(f"ANVISA non-JSON response: {exc}") from exc

    # ── Endpoints públicos ────────────────────────────────────────────────

    async def search(self, nome_produto: str, *, page: int = 1, count: int = 10) -> dict:
        """
        Busca produtos no bulário pelo nome.
        Retorna `{"content": [...], "totalElements": N, ...}`.
        """
        return await self._get(
            "/consulta/bulario",
            params={
                "count": count,
                "filter[nomeProduto]": nome_produto,
                "page": page,
            },
        )

    async def autocomplete(self, texto: str) -> list[dict]:
        """Sugestões de medicamentos para autocomplete."""
        return await self._get(f"/produto/listaMedicamentoBula/{texto}")

    async def detail(self, num_processo: str) -> dict:
        """Detalhe completo de um medicamento pelo número de processo."""
        return await self._get(f"/consulta/medicamento/produtos/{num_processo}")

    async def categories(self) -> list[dict]:
        """Lista de categorias regulatórias."""
        return await self._get("/tipoCategoriaRegulatoria")

    async def download_bula_pdf(self, codigo_bula: str) -> bytes:
        """
        Baixa o PDF da bula (paciente ou profissional).

        `codigo_bula` é o JWT retornado em `detail.codigoBulaPaciente` ou
        `detail.codigoBulaProfissional`. JWT é de curta duração (~5 min),
        então o caller deve buscar detail fresco antes de chamar este método.
        Retorna os bytes do PDF.
        """
        await self._warmup()
        url = f"{BASE_URL}/consulta/medicamentos/arquivo/bula/parecer/{codigo_bula}/"
        async with self._sem:
            try:
                resp = await self._session.get(url)
            except RequestsError as exc:
                log.warning("anvisa.bula_pdf.network_error", exc=str(exc))
                raise AnvisaError(f"ANVISA PDF network error: {exc}") from exc

            if resp.status_code >= 400:
                log.warning(
                    "anvisa.bula_pdf.http_error",
                    status=resp.status_code,
                    body=resp.text[:200],
                )
                raise AnvisaError(f"ANVISA PDF HTTP {resp.status_code}")

            content = resp.content
            if not content or not content[:4] == b"%PDF":
                log.warning(
                    "anvisa.bula_pdf.not_pdf",
                    head=content[:50] if content else b"",
                    size=len(content),
                )
                raise AnvisaError("ANVISA PDF: response is not a PDF")
            return content

    async def by_category(self, categoria_id: str, *, page: int = 1, count: int = 10) -> dict:
        """Produtos filtrados por categoria regulatória."""
        return await self._get(
            "/consulta/bulario",
            params={
                "count": count,
                "filter[categoriasRegulatorias]": categoria_id,
                "page": page,
            },
        )
