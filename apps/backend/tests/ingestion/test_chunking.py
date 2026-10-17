"""Tests — chunking con coordenadas reconstruibles (SPEC-02 §1.3 · §6).

Autorizado por: SPEC-02 §1.3 (conservar página y posición) y §6
("exact_quote es verificable en el texto original").
"""

from __future__ import annotations

from app.services.chunking import (
    Chunk,
    chunk_page,
    chunk_pages,
    find_quote_span,
    normalize_pages,
    reconstruct_quote,
    verify_chunk,
)

PAGES = [
    "Juan Pérez, ciudadano español, figura como administrador único de Acme Corp.",
    "Según el informe, Acme Corp financió la compra de equipos de cifrado en 2024.",
    "El dispositivo con IMEI 356938035643809 aparece asociado al domicilio en Madrid.",
]


# ---------------------------------------------------------------------
# Coordenadas exactas
# ---------------------------------------------------------------------
def test_exact_quote_is_reconstructible_from_coordinates():
    for chunk in chunk_pages(PAGES):
        assert isinstance(chunk, Chunk)
        assert reconstruct_quote(PAGES, chunk.page_number, chunk.char_start, chunk.char_end) == chunk.text
        assert verify_chunk(PAGES, chunk) is True


def test_page_number_is_preserved_across_pages():
    chunks = chunk_pages(PAGES, max_chars=40)
    assert {chunk.page_number for chunk in chunks} == {1, 2, 3}
    for chunk in chunks:
        assert PAGES[chunk.page_number - 1][chunk.char_start:chunk.char_end] == chunk.text


def test_chunks_do_not_mutate_source_text():
    chunks = chunk_pages(PAGES, max_chars=30)
    for chunk in chunks:
        assert chunk.text in PAGES[chunk.page_number - 1]


def test_index_is_monotonic():
    chunks = chunk_pages(PAGES, max_chars=50)
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    # El offset de página de cada chunk respeta su propia página.
    for chunk in chunks:
        assert 0 <= chunk.char_start < chunk.char_end <= len(PAGES[chunk.page_number - 1])


def test_overlap_preserves_coordinates():
    long_page = " ".join(f"token{i}" for i in range(200))
    without_overlap = chunk_pages(long_page, max_chars=100)
    with_overlap = chunk_pages(long_page, max_chars=100, overlap=20)
    assert len(with_overlap) > len(without_overlap)
    for chunk in with_overlap:
        assert verify_chunk(long_page, chunk) is True


# ---------------------------------------------------------------------
# Localización de una cita concreta (para provenance)
# ---------------------------------------------------------------------
def test_find_quote_span_locates_exact_fragment():
    quote = "Acme Corp financió la compra de equipos de cifrado"
    span = find_quote_span(PAGES[1], quote)
    assert span is not None
    start, end = span
    assert reconstruct_quote(PAGES, 2, start, end) == quote


def test_quote_wider_than_chunk_is_not_found_in_chunk():
    chunk = chunk_page(PAGES[1], 2, max_chars=20)[0]
    assert find_quote_span(chunk.text, PAGES[1]) is None  # cita mayor que el chunk


# ---------------------------------------------------------------------
# Entrada flexible
# ---------------------------------------------------------------------
def test_normalize_accepts_string_and_dicts():
    assert normalize_pages("una sola página") == ["una sola página"]
    assert normalize_pages([{"page_number": 1, "text": "hola"}]) == ["hola"]
    assert normalize_pages([{"content": "mundo"}]) == ["mundo"]


def test_single_string_is_one_page():
    chunks = chunk_pages("texto corto de una página")
    assert len(chunks) == 1
    assert chunks[0].page_number == 1
    assert chunks[0].char_start == 0
    assert chunks[0].char_end == len("texto corto de una página")
