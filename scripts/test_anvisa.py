"""
Script de validação manual dos endpoints da ANVISA.

Não toca em DB, não precisa do app rodando. Só verifica que os endpoints
respondem e o parsing JSON está OK. Executa quatro chamadas: busca,
autocomplete, detalhe (do primeiro resultado da busca) e categorias.

Uso (dentro de saas-farmacia/):
    python -m scripts.test_anvisa
ou:
    python scripts/test_anvisa.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Garante que api/ está no sys.path quando rodado direto.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from services.anvisa_client import AnvisaClient, AnvisaError  # noqa: E402


def _short(obj, n: int = 600) -> str:
    s = json.dumps(obj, ensure_ascii=False, indent=2)
    return s if len(s) <= n else s[:n] + f"\n... [+{len(s) - n} chars]"


async def main() -> int:
    termo = "dipirona"
    print(f"=== ANVISA endpoint smoke test (termo: {termo!r}) ===\n")

    async with AnvisaClient() as cli:
        # 1. Busca
        print("[1/4] search(...)")
        try:
            res = await cli.search(termo, count=3)
        except AnvisaError as exc:
            print(f"  FAIL: {exc}")
            return 1
        content = res.get("content") or []
        total = res.get("totalElements")
        print(f"  OK — totalElements={total}, retornados={len(content)}")
        if content:
            print(f"  primeiro: {_short(content[0], 400)}")
        print()

        # 2. Autocomplete
        print("[2/4] autocomplete(...)")
        try:
            ac = await cli.autocomplete(termo)
        except AnvisaError as exc:
            print(f"  FAIL: {exc}")
            return 1
        print(f"  OK — {len(ac)} sugestões")
        if ac:
            print(f"  amostra: {_short(ac[:3], 400)}")
        print()

        # 3. Detalhe (usa numProcesso do primeiro resultado se houver)
        print("[3/4] detail(...)")
        if content:
            first = content[0]
            num_proc = (
                first.get("numProcesso")
                or first.get("numeroProcesso")
                or first.get("processo")
            )
            if num_proc:
                try:
                    det = await cli.detail(str(num_proc))
                    print(f"  OK — campos no detalhe: {list(det)[:15]}")
                    print(f"  amostra: {_short(det, 600)}")
                except AnvisaError as exc:
                    print(f"  FAIL: {exc}")
                    return 1
            else:
                print(f"  SKIP — primeiro resultado sem numProcesso. Campos: {list(first)[:15]}")
        else:
            print("  SKIP — busca não retornou itens")
        print()

        # 4. Categorias
        print("[4/4] categories()")
        try:
            cats = await cli.categories()
        except AnvisaError as exc:
            print(f"  FAIL: {exc}")
            return 1
        print(f"  OK — {len(cats)} categorias")
        if cats:
            print(f"  amostra: {_short(cats[:3], 400)}")

    print("\n=== Todos os endpoints responderam ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
