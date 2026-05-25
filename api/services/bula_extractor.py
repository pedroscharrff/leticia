"""
Extração de texto das bulas em PDF da ANVISA.

Pipeline:
  1. download_bula_pdf(codigo_bula)   — baixa PDF via JWT
  2. pdf_to_text(bytes)               — extrai texto puro com pypdf
  3. split_secoes(text)               — particiona em seções conhecidas

Seções padrão da "bula do paciente" (RDC 47/2009): nove perguntas numeradas.
Detectamos cada cabeçalho via regex flexível e o conteúdo é tudo até o
próximo cabeçalho. Se nada bater (PDF muito diferente do padrão), devolve
uma única seção `completa` com o texto inteiro — agente ainda consegue
buscar lexicamente.

Não joga exceção em caso de PDF estranho — chamadores assumem retorno
possivelmente vazio. Erros vão pro log.
"""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass

import structlog
from pypdf import PdfReader

log = structlog.get_logger()


@dataclass(frozen=True)
class BulaSecao:
    """Seção extraída — pronta pra upsert em public.bula_secoes."""
    slug: str           # ex: "indicacoes", "posologia"
    titulo: str         # título original como veio no PDF
    conteudo: str


# Mapa slug → padrões regex que casam o cabeçalho da seção na bula do paciente.
# Cada slug pode ter vários padrões — bulas variam um pouco no fraseado.
# Casamos o título inteiro (linha curta começando com número opcional).
_SECTION_PATTERNS: list[tuple[str, str]] = [
    ("indicacoes",       r"para\s+que\s+est[ea]\s+medicamento\s+[ée]\s+indicado"),
    ("indicacoes",       r"indica[cç][oõ]es"),
    ("mecanismo",        r"como\s+est[ea]\s+medicamento\s+funciona"),
    ("contraindicacoes", r"quando\s+n[aã]o\s+devo\s+(usar|utilizar)"),
    ("contraindicacoes", r"contraindica[cç][oõ]es"),
    ("precaucoes",       r"o\s+que\s+devo\s+saber\s+antes\s+de\s+(usar|utilizar)"),
    ("precaucoes",       r"adverten[cç]ias?\s+e\s+precau[cç][oõ]es"),
    ("interacoes",       r"intera[cç][oõ]es\s+medicamentos"),
    ("armazenamento",    r"onde[,\s]+como\s+e\s+por\s+quanto\s+tempo\s+posso\s+guardar"),
    ("armazenamento",    r"armazenagem|armazenamento|cuidados\s+de\s+conserva"),
    ("posologia",        r"como\s+devo\s+(usar|utilizar)\s+est[ea]\s+medicamento"),
    ("posologia",        r"posologia"),
    ("esquecimento",     r"o\s+que\s+devo\s+fazer\s+quando\s+eu\s+me\s+esquecer"),
    ("reacoes_adversas", r"quais\s+os\s+males\s+que\s+este\s+medicamento\s+pode\s+me\s+causar"),
    ("reacoes_adversas", r"rea[cç][oõ]es\s+adversas"),
    ("superdosagem",     r"o\s+que\s+fazer\s+se\s+algu[eé]m\s+usar\s+uma\s+quantidade"),
    ("superdosagem",     r"superdose|superdosagem"),
    ("composicao",       r"composi[cç][aã]o"),
]


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def pdf_to_text(pdf_bytes: bytes) -> str:
    """Extrai texto integral de PDF bytes. Concatena páginas com \\n\\n."""
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
    except Exception as exc:  # noqa: BLE001
        log.warning("bula.pdf.parse_failed", exc=str(exc))
        return ""

    chunks: list[str] = []
    for i, page in enumerate(reader.pages):
        try:
            chunks.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            log.warning("bula.pdf.page_failed", page=i, exc=str(exc))
            continue
    return "\n\n".join(chunks)


def _normalize_text(text: str) -> str:
    """Limpa whitespace excessivo preservando quebras de parágrafo."""
    # Junta linhas quebradas no meio de frases (heurística: linha que não
    # termina em pontuação + próxima começando minúscula).
    text = re.sub(r"-\n(\w)", r"\1", text)            # palavras hifenizadas
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _find_section_anchors(text: str) -> list[tuple[int, str, str]]:
    """
    Acha posições onde cada seção começa.
    Retorna lista [(offset, slug, titulo_capturado), ...] ordenada por offset.
    """
    norm = _strip_accents(text).lower()
    matches: list[tuple[int, str, str]] = []
    seen_slugs: set[str] = set()

    for slug, pattern in _SECTION_PATTERNS:
        if slug in seen_slugs:
            # Já achamos uma variante deste slug — pula pra não duplicar.
            continue
        # Restringe início de linha (^ ou após \n), aceita prefixo numérico
        full = re.compile(
            r"(^|\n)\s*(\d+[\.\)]\s*)?(" + pattern + r")[^\n]*",
            flags=re.IGNORECASE | re.MULTILINE,
        )
        m = full.search(norm)
        if m:
            # offset relativo no texto normalizado == no original (mesmo
            # comprimento; só removemos acentos sem mexer no resto).
            start = m.start() + (1 if m.group(1) == "\n" else 0)
            titulo = text[start:m.end()].strip()[:120]
            matches.append((start, slug, titulo))
            seen_slugs.add(slug)

    matches.sort(key=lambda x: x[0])
    return matches


def split_secoes(raw_text: str) -> list[BulaSecao]:
    """
    Particiona o texto da bula em seções conhecidas.

    Se nenhuma seção for detectada (PDF fora do padrão), retorna uma única
    seção `completa` com todo o texto — ainda permite busca lexical.
    """
    text = _normalize_text(raw_text)
    if not text:
        return []

    anchors = _find_section_anchors(text)
    if not anchors:
        log.info("bula.split.no_anchors", chars=len(text))
        return [BulaSecao(slug="completa", titulo="Bula completa", conteudo=text)]

    secoes: list[BulaSecao] = []
    for i, (start, slug, titulo) in enumerate(anchors):
        end = anchors[i + 1][0] if i + 1 < len(anchors) else len(text)
        body = text[start:end].strip()
        # Remove a primeira linha (o título), guarda só o conteúdo
        if "\n" in body:
            body = body.split("\n", 1)[1].strip()
        if not body:
            continue
        secoes.append(BulaSecao(slug=slug, titulo=titulo, conteudo=body))

    return secoes
