"""
Ingestão do "Guia de Medicamentos Genéricos" (PDF) → base de referência.

Popula public.medicamentos_referencia (mapeamento princípio ativo ↔ marca de
referência) e public.medicamentos_referencia_secoes (seções clínicas). As seções
nascem `status='pending'` — só viram visíveis ao agente depois de revisadas no
painel superadmin (a fonte é de 2001; info clínica precisa de curadoria).

Estrutura do PDF (uma entrada por página de conteúdo):
    <NOME EM CAIXA ALTA>           ex: "DOBUTAMINA (CLORIDRATO)"
    Ref. <MARCA>                   ex: "Ref. DOBUTREX"
    FORMA(S) FARMACÊUTICA(S)       → vai para o PAI (forma_farmaceutica)
    INDICAÇÕES                     ┐
    POSOLOGIA                      │
    CONTRA-INDICAÇÕES              ├ seções clínicas → tabela FILHA (pending)
    EFEITOS ADVERSOS               │
    INTERAÇÕES                     │
    PRECAUÇÕES                     ┘
A categoria terapêutica vem do rodapé das páginas ("... Page N <categoria>").

Idempotente: re-rodar atualiza conteúdo SEM resetar o `status` já curado.

Uso:
    docker compose exec api python -m scripts.ingest_guia_genericos --dry-run
    docker compose exec api python -m scripts.ingest_guia_genericos --dry-run --limit 8
    docker compose exec api python -m scripts.ingest_guia_genericos
    docker compose exec api python -m scripts.ingest_guia_genericos --pdf /caminho/guia.pdf
"""
from __future__ import annotations

import argparse
import asyncio
import io
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "api"))

from db.postgres import init_pool, close_pool, get_db_conn  # noqa: E402
from services.bulario_repo import _normalize  # noqa: E402
from pypdf import PdfReader  # noqa: E402

# O PDF mora em api/ (copiado para a imagem em /app/api/). Fallback para a raiz
# caso alguém o coloque lá. ROOT = /app no container, saas-farmacia/ no host.
_PDF_CANDIDATES = (
    ROOT / "api" / "guia_medicamentos_genericos.pdf",
    ROOT / "guia_medicamentos_genericos.pdf",
)
DEFAULT_PDF = next((p for p in _PDF_CANDIDATES if p.exists()), _PDF_CANDIDATES[0])
SOURCE_TAG = "guia_genericos_2001"

# Slugs das seções clínicas (vão para a tabela filha). `forma` é tratada à parte
# (vira coluna no pai). Mantém os mesmos slugs aceitos pelo painel/tool.
CLINICAL_SLUGS = (
    "indicacoes", "posologia", "contraindicacoes",
    "efeitos_adversos", "interacoes", "precaucoes",
)

# ── Regex de estrutura ──────────────────────────────────────────────────────
# Parser baseado em ÂNCORAS (igual ao split_secoes da bula): acha "Ref." e os
# cabeçalhos de seção em QUALQUER posição do texto, então funciona tanto com
# extração linha-a-linha quanto com texto reflowed (headers inline) — o pypdf
# pode produzir qualquer um dos dois.

# Rodapé tolerante a mojibake do título decorativo ("GenŽricos" etc.).
FOOTER_RE = re.compile(
    r"^Medicamentos\s+Gen.*?\bPage\s+(\d+)\b\s*(?P<cat>.*)$", re.IGNORECASE
)
PAGENUM_RE = re.compile(r"^\d{1,4}$")
# "Ref." como marcador de início de entrada (delimitador entre medicamentos).
REF_RE = re.compile(r"\bRef\.\s+")
# Cabeçalhos de seção. Padrões tolerantes a acento/mojibake (\S{0,4} cobre os
# vogais acentuadas de "INDICAÇÕES" etc.). FORMA consome o header inteiro até o
# ")" de "FARMACÊUTICA(S)" para não vazar "ÊUTICA(S)" no conteúdo.
SEC_RE = re.compile(
    r"(?P<h>FORMA.{0,8}FARMAC(?:[^)]{0,15}\))?|INDICA\S{0,4}ES|POSOLOGIA|"
    r"CONTRA.{0,3}INDICA\S{0,4}ES|EFEITOS\s+ADVERSOS|INTERA\S{0,4}ES|PRECAU\S{0,4}ES)",
    re.IGNORECASE,
)
# Nome do medicamento = sequência maximal em CAIXA ALTA imediatamente antes de "Ref.".
NAME_TAIL_RE = re.compile(r"([A-ZÀ-Ý][A-ZÀ-Ý0-9 ()\-,]{2,70})\s*$")
# Marca de referência = primeira sequência em caixa alta após "Ref.".
BRAND_RE = re.compile(r"([A-ZÀ-Ý0-9][A-ZÀ-Ý0-9 ()\-\.]{0,40})")


@dataclass
class Entry:
    principio_ativo: str
    nome_referencia: str | None
    forma_farmaceutica: str | None
    categoria: str | None
    page_ref: int | None
    secoes: dict[str, str] = field(default_factory=dict)


def _deaccent_upper(s: str) -> str:
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.upper().strip()


def _slug_of(header: str) -> str | None:
    """Mapeia o texto do cabeçalho casado para o slug da seção."""
    u = _deaccent_upper(header)
    if u.startswith("FORMA"):    return "forma"
    if u.startswith("INDICA"):   return "indicacoes"
    if u.startswith("POSOLOGIA"): return "posologia"
    if u.startswith("CONTRA"):   return "contraindicacoes"
    if u.startswith("EFEITOS"):  return "efeitos_adversos"
    if u.startswith("INTERA"):   return "interacoes"
    if u.startswith("PRECAU"):   return "precaucoes"
    return None


def parse_pdf(pdf_bytes: bytes) -> list[Entry]:
    """
    Extrai as entradas do PDF via âncoras. Fases:
      1. Limpa rodapés/números, captura categoria/página e junta as linhas
         sobreviventes num único texto (com mapa offset → categoria/página).
      2. Acha as posições de "Ref." (delimita entradas) e dos cabeçalhos de
         seção; particiona cada entrada.
    Tolerante: trechos estranhos são ignorados, nunca lança.
    """
    import bisect

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_text = []
    for page in reader.pages:
        try:
            pages_text.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001
            pages_text.append("")

    # ── Fase 1 ──────────────────────────────────────────────────────────────
    # A categoria terapêutica NÃO está no rodapé — está em PÁGINAS DIVISÓRIAS
    # próprias entre os medicamentos (ex.: "Agentes Inotrópicos"). Detectamos a
    # divisória (sem "Ref.", sem cabeçalho de seção, texto curto), usamos como
    # categoria corrente e NÃO emitimos suas linhas — assim o título não vaza
    # para a última seção (precauções) do medicamento anterior.
    parts: list[str] = []
    offsets: list[int] = []          # offset inicial de cada linha em `joined`
    metas: list[tuple[str | None, int | None]] = []  # (categoria, página)
    pos = 0
    current_category: str | None = None
    current_page: int | None = None

    for ptext in pages_text:
        page_num = current_page
        lines: list[str] = []
        for raw in ptext.splitlines():
            s = raw.strip()
            if not s:
                continue
            mf = FOOTER_RE.match(s)
            if mf:
                page_num = int(mf.group(1))
                continue
            if PAGENUM_RE.match(s):       # número de página solto (na divisória)
                continue
            lines.append(s)
        current_page = page_num
        if not lines:
            continue

        body_len = sum(len(ln) for ln in lines)
        is_divider = (
            not REF_RE.search(ptext)
            and not SEC_RE.search(ptext)
            and body_len < 160
        )
        if is_divider:
            cat = re.sub(r"\b\d{1,4}\b", " ", " ".join(lines))  # tira nº de página inline
            cat = re.sub(r"\s+", " ", cat).strip()
            if cat:
                current_category = cat
            continue

        for s in lines:
            parts.append(s)
            offsets.append(pos)
            metas.append((current_category, current_page))
            pos += len(s) + 1            # +1 do espaço que junta as linhas

    joined = " ".join(parts)

    def meta_at(off: int) -> tuple[str | None, int | None]:
        if not metas:
            return None, None
        i = max(0, min(bisect.bisect_right(offsets, off) - 1, len(metas) - 1))
        return metas[i]

    # ── Fase 2 ──────────────────────────────────────────────────────────────
    refs = [m.start() for m in REF_RE.finditer(joined)]
    secs = [(m.start(), m.end(), _slug_of(m.group("h")))
            for m in SEC_RE.finditer(joined)]

    entries: list[Entry] = []
    for k, r in enumerate(refs):
        r2 = refs[k + 1] if k + 1 < len(refs) else len(joined)
        esecs = [s for s in secs if r < s[0] < r2]
        first_sec = esecs[0][0] if esecs else r2

        brand_start = r + re.match(r"\bRef\.\s+", joined[r:]).end()
        mb = BRAND_RE.match(joined[brand_start:first_sec].strip())
        brand = mb.group(1).strip(" .-") if mb else None

        mn = NAME_TAIL_RE.search(joined[max(0, r - 90):r])
        name = re.sub(r"\s+", " ", mn.group(1)).strip() if mn else ""

        buckets: dict[str, list[str]] = {}
        for j, (s_start, s_end, slug) in enumerate(esecs):
            nxt = esecs[j + 1][0] if j + 1 < len(esecs) else r2
            body = joined[s_end:nxt].strip()
            if nxt == r2:
                # A última seção encosta no nome do PRÓXIMO medicamento — corta-o.
                mt = NAME_TAIL_RE.search(body)
                if mt and len(mt.group(1).strip()) >= 3:
                    body = body[: mt.start()].strip()
            if slug:
                buckets.setdefault(slug, []).append(body)

        forma = "; ".join(buckets.get("forma", [])).strip() or None
        secoes = {
            slug: " ".join(buckets[slug]).strip()
            for slug in CLINICAL_SLUGS
            if buckets.get(slug) and " ".join(buckets[slug]).strip()
        }
        cat, page = meta_at(r)
        entries.append(Entry(
            principio_ativo=name,
            nome_referencia=brand,
            forma_farmaceutica=forma,
            categoria=cat,
            page_ref=page,
            secoes=secoes,
        ))
    return entries


async def upsert_entry(conn, e: Entry) -> int:
    """Upsert idempotente do pai + seções. NÃO mexe em status/reviewed_*."""
    row = await conn.fetchrow(
        """
        INSERT INTO public.medicamentos_referencia
            (principio_ativo, principio_ativo_norm, nome_referencia,
             nome_referencia_norm, forma_farmaceutica, categoria,
             source, page_ref, updated_at)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NOW())
        ON CONFLICT (principio_ativo, nome_referencia) DO UPDATE
           SET principio_ativo_norm = EXCLUDED.principio_ativo_norm,
               nome_referencia_norm = EXCLUDED.nome_referencia_norm,
               forma_farmaceutica   = EXCLUDED.forma_farmaceutica,
               categoria            = EXCLUDED.categoria,
               page_ref             = EXCLUDED.page_ref,
               updated_at           = NOW()
        RETURNING id
        """,
        e.principio_ativo,
        _normalize(e.principio_ativo),
        e.nome_referencia,
        _normalize(e.nome_referencia) if e.nome_referencia else None,
        e.forma_farmaceutica,
        e.categoria,
        SOURCE_TAG,
        e.page_ref,
    )
    ref_id = row["id"]
    for slug, conteudo in e.secoes.items():
        # ON CONFLICT só atualiza conteúdo — preserva status/reviewed_* da curadoria.
        await conn.execute(
            """
            INSERT INTO public.medicamentos_referencia_secoes
                (referencia_id, secao, conteudo, updated_at)
            VALUES ($1, $2, $3, NOW())
            ON CONFLICT (referencia_id, secao) DO UPDATE
               SET conteudo = EXCLUDED.conteudo, updated_at = NOW()
            """,
            ref_id, slug, conteudo,
        )
    return ref_id


def _print_entry(i: int, e: Entry) -> None:
    print(f"\n[{i}] {e.principio_ativo}  →  Ref. {e.nome_referencia or '—'}"
          f"   (pág {e.page_ref}, cat: {e.categoria or '—'})")
    print(f"    forma: {(e.forma_farmaceutica or '—')[:100]}")
    for slug in CLINICAL_SLUGS:
        if slug in e.secoes:
            preview = e.secoes[slug].replace("\n", " ")[:90]
            print(f"    {slug:18}: {preview}")


async def main(argv: list[str]) -> int:
    # Locale C/POSIX no container → stdout ASCII → print() de acentos estoura.
    # Força UTF-8 (no-op se já for) para o dry-run nunca quebrar exibindo nomes.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", type=str, default=str(DEFAULT_PDF), help="caminho do PDF")
    ap.add_argument("--limit", type=int, default=None, help="processa só os primeiros N")
    ap.add_argument("--dry-run", action="store_true", help="só parseia e imprime, não grava")
    args = ap.parse_args(argv)

    pdf_path = Path(args.pdf)
    if not pdf_path.exists():
        print(f"ERRO: PDF não encontrado em {pdf_path}", file=sys.stderr)
        return 2

    entries = parse_pdf(pdf_path.read_bytes())
    if args.limit:
        entries = entries[: args.limit]

    # Sanidade do parsing
    no_brand = sum(1 for e in entries if not e.nome_referencia)
    no_sections = sum(1 for e in entries if not e.secoes)
    print(f"Parse: {len(entries)} entradas  "
          f"(sem marca: {no_brand}, sem seções clínicas: {no_sections})")

    if args.dry_run:
        for i, e in enumerate(entries[:8], 1):
            _print_entry(i, e)
        print("\n[dry-run] nada foi gravado.")
        return 0

    await init_pool()
    n_ok = 0
    n_secoes = 0
    try:
        async with get_db_conn() as conn:
            for e in entries:
                if not e.principio_ativo:
                    continue
                await upsert_entry(conn, e)
                n_ok += 1
                n_secoes += len(e.secoes)
    finally:
        await close_pool()

    print(f"\nGravado: {n_ok} medicamentos, {n_secoes} seções clínicas (status=pending).")
    print("Revise e ative as seções no painel superadmin antes do agente usá-las.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv[1:])))
