"""Resolução de produto em `adicionar_ao_carrinho` (SPEC 03).

Trava a regressão do "pedido virou fumaça": catálogo Sheets com DUAS linhas de
`name` idêntico e preços diferentes (Benegrip R$16,90 "Caixa c/12" vs R$83,15
"Caixa c/30 250mg"). O `LIMIT 1` cego antigo faturava o produto ERRADO em silêncio
OU falhava quando o nome vinha "completo". Ver project_cart_duplicate_name_limit1.
"""
import asyncio
import sys
import types

import pytest

from agents.tools.inventory import make_add_to_cart_tool


# ── Catálogo fake — reproduz o caso real (dois "Benegrip") ──────────────────────
_TWO_BENEGRIP = [
    {"name": "Benegrip", "price": 16.90,
     "description": "Formato: Caixa c/ 12 comprimidos",
     "principio_ativo": "Dipirona + Clorfeniramina + Cafeina"},
    {"name": "Benegrip", "price": 83.15,
     "description": "Formato: Caixa c/ 30 comprimidos | Dosagem: 250mg",
     "principio_ativo": "Dipirona + Clorfeniramina + Cafeina"},
]
_SINGLE = [
    {"name": "Dipirona 500mg", "price": 9.90,
     "description": "Caixa c/ 10 comprimidos", "principio_ativo": "Dipirona"},
]


class _FakeConn:
    """Emula o WHERE (name/description/principio_ativo ILIKE $1) do _SELECT_CAND."""

    def __init__(self, products):
        self._products = products

    async def execute(self, *_a, **_k):
        return None

    async def fetch(self, _query, *params):
        needle = str(params[0]).strip("%").lower()
        out = []
        for p in self._products:
            hay = [p.get("name", ""), p.get("description") or "",
                   p.get("principio_ativo") or ""]
            if any(needle in (f or "").lower() for f in hay):
                out.append({"name": p["name"], "price": p["price"],
                            "description": p.get("description")})
        return out


class _FakeConnCtx:
    def __init__(self, products):
        self._products = products

    async def __aenter__(self):
        return _FakeConn(self._products)

    async def __aexit__(self, *_a):
        return False


@pytest.fixture
def patch_db(monkeypatch):
    """Injeta um `db.postgres` fake em sys.modules (o real puxa config.Settings,
    que quebra no ambiente de teste). O tool faz `from db.postgres import
    get_db_conn` em runtime, então basta a entrada em sys.modules."""
    def _use(products):
        if "db" not in sys.modules:
            pkg = types.ModuleType("db")
            pkg.__path__ = []  # marca como pacote
            monkeypatch.setitem(sys.modules, "db", pkg)
        fake = types.ModuleType("db.postgres")
        fake.get_db_conn = lambda: _FakeConnCtx(products)
        monkeypatch.setitem(sys.modules, "db.postgres", fake)
    return _use


def _add(cart, produto, quantidade=1):
    tool = make_add_to_cart_tool("tenant_test", cart)
    return asyncio.run(tool.ainvoke({"produto": produto, "quantidade": quantidade}))


# ── Produto único: adiciona normalmente ─────────────────────────────────────────
def test_single_match_adds(patch_db):
    patch_db(_SINGLE)
    cart: dict = {"items": [], "subtotal": 0.0}
    out = _add(cart, "Dipirona", 2)
    assert "adicionado ao carrinho" in out
    assert cart["items"] == [{"name": "Dipirona 500mg", "price": 9.90, "qty": 2}]


# ── Nome "completo" resolve na linha CERTA (o caso do turno "sim") ───────────────
def test_full_name_resolves_to_correct_row(patch_db):
    """'Benegrip caixa c/ 30 comprimidos (250mg)' não é substring de name='Benegrip';
    o fallback por token + score tem que escolher a de R$83,15, não falhar."""
    patch_db(_TWO_BENEGRIP)
    cart: dict = {"items": [], "subtotal": 0.0}
    out = _add(cart, "Benegrip caixa c/ 30 comprimidos (250mg)", 3)
    assert "adicionado ao carrinho" in out
    assert len(cart["items"]) == 1
    assert cart["items"][0]["price"] == 83.15
    assert cart["items"][0]["qty"] == 3


def test_dosage_token_resolves(patch_db):
    patch_db(_TWO_BENEGRIP)
    cart: dict = {"items": [], "subtotal": 0.0}
    _add(cart, "Benegrip 250mg", 1)
    assert cart["items"][0]["price"] == 83.15


def test_pack_size_token_resolves_cheaper(patch_db):
    patch_db(_TWO_BENEGRIP)
    cart: dict = {"items": [], "subtotal": 0.0}
    _add(cart, "Benegrip 12 comprimidos", 1)
    assert cart["items"][0]["price"] == 16.90


# ── Empate real: NÃO adivinha — devolve opções e mantém carrinho vazio ──────────
def test_ambiguous_bare_name_does_not_guess(patch_db):
    patch_db(_TWO_BENEGRIP)
    cart: dict = {"items": [], "subtotal": 0.0}
    out = _add(cart, "Benegrip", 3)
    assert "mais de um produto" in out.lower()
    assert "16.90" in out and "83.15" in out       # lista as duas opções
    assert cart["items"] == []                      # nada faturado silenciosamente


# ── Não encontrado ──────────────────────────────────────────────────────────────
def test_not_found(patch_db):
    patch_db(_TWO_BENEGRIP)
    cart: dict = {"items": [], "subtotal": 0.0}
    out = _add(cart, "Rivotril", 1)
    assert "não encontrado" in out.lower()
    assert cart["items"] == []
