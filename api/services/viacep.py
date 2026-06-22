"""
Consulta de endereço por CEP via API pública ViaCEP (https://viacep.com.br).

API gratuita, sem autenticação, sem rate-limit declarado e sem Cloudflare —
um GET simples com httpx resolve. Usada pelo agente para autocompletar o
endereço do cliente assim que ele informa o CEP, deixando o atendimento mais
fluido (cliente só confirma rua/bairro/cidade e informa número/complemento).

Uso:
    from services.viacep import lookup_cep, ViaCepResult
    res = await lookup_cep("01310-100")
    if res:
        print(res.logradouro, res.bairro, res.localidade, res.uf)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
import structlog

log = structlog.get_logger()

_CEP_DIGITS = re.compile(r"\d")
_BASE_URL = "https://viacep.com.br/ws"


@dataclass
class ViaCepResult:
    """Endereço resolvido pelo ViaCEP. Campos podem vir vazios (CEP de cidade
    inteira não tem logradouro/bairro)."""
    cep: str
    logradouro: str
    complemento: str
    bairro: str
    localidade: str   # cidade
    uf: str           # estado (sigla)

    def linha(self) -> str:
        """Linha legível do endereço encontrado (para o agente confirmar)."""
        parts: list[str] = []
        if self.logradouro:
            parts.append(self.logradouro)
        if self.bairro:
            parts.append(self.bairro)
        if self.localidade:
            parts.append(self.localidade + (f"/{self.uf}" if self.uf else ""))
        if self.cep:
            parts.append(f"CEP {self.cep}")
        return ", ".join(parts)


def normalize_cep(cep: str) -> str | None:
    """Retorna os 8 dígitos do CEP (sem hífen) ou None se inválido."""
    digits = "".join(_CEP_DIGITS.findall(cep or ""))
    return digits if len(digits) == 8 else None


async def lookup_cep(cep: str, *, timeout: float = 8.0) -> ViaCepResult | None:
    """
    Consulta o ViaCEP e retorna o endereço, ou None se o CEP for inválido,
    não existir, ou a API falhar. Nunca lança — falha "fechada" (None) para
    o caller decidir o fallback amigável.
    """
    digits = normalize_cep(cep)
    if digits is None:
        return None

    url = f"{_BASE_URL}/{digits}/json/"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
        if resp.status_code != 200:
            log.warning("viacep.bad_status", cep=digits, status=resp.status_code)
            return None
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 — rede/parse, falha fechada
        log.warning("viacep.lookup_failed", cep=digits, exc=str(exc))
        return None

    # CEP inexistente: ViaCEP responde 200 com {"erro": true} (ou "true").
    if not isinstance(data, dict) or data.get("erro"):
        return None

    return ViaCepResult(
        cep=(data.get("cep") or "").strip(),
        logradouro=(data.get("logradouro") or "").strip(),
        complemento=(data.get("complemento") or "").strip(),
        bairro=(data.get("bairro") or "").strip(),
        localidade=(data.get("localidade") or "").strip(),
        uf=(data.get("uf") or "").strip(),
    )
