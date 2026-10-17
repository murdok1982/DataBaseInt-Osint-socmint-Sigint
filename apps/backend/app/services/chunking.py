"""Chunking con preservación de coordenadas de página.

Autorizado por: **SPEC-02 §1.3** ("Fragmentación (Chunking) conservando el
número de página y posición de caracteres") y **SPEC-02 §1.4**.
Zona: Agente 2 — utilidades de chunking (``apps/backend/app/services/chunking.py``).

Invariante central
------------------
Para todo :class:`Chunk` emitido:

    ``pages[chunk.page_number - 1][chunk.char_start:chunk.char_end] == chunk.text``

Es decir, ``char_start``/``char_end`` son offsets **relativos a su página**, por
lo que la porción de texto (``exact_quote``) es **reconstruible** desde las
coordenadas. Esto es lo que exige SPEC-02 §6 ("``provenance.exact_quote`` es
verificable en el texto original (página y span)").
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

#: Tamaño por defecto de chunk (caracteres). Heurística de implementación;
#: SPEC-02 §1.3 exige conservar coordenadas, no un tamaño concreto.
DEFAULT_MAX_CHARS = 1200

#: Solape por defecto entre chunks de la misma página (caracteres).
DEFAULT_OVERLAP = 0


class Chunk(BaseModel):
    """Fragmento de informe con coordenadas absolutas dentro de su página."""

    model_config = ConfigDict(frozen=True)

    #: Orden global del chunk dentro del documento (0-based).
    index: int = Field(ge=0)
    #: Número de página 1-based (SPEC-02 §2, ``provenance.page_number``).
    page_number: int = Field(ge=1)
    #: Offset de inicio en el texto de la página (inclusive).
    char_start: int = Field(ge=0)
    #: Offset de fin en el texto de la página (exclusive).
    char_end: int = Field(ge=0)
    #: Texto exacto del fragmento (``pages[page-1][start:end]``).
    text: str = Field(min_length=1)

    @property
    def span(self) -> tuple[int, int]:
        """Coordenadas ``(char_start, char_end)``."""
        return self.char_start, self.char_end


# ---------------------------------------------------------------------
# Normalización de entrada
# ---------------------------------------------------------------------
def normalize_pages(pages: Sequence[str] | Sequence[Mapping[str, Any]] | str) -> list[str]:
    """Normaliza la entrada a ``list[str]`` (una entrada por página).

    Acepta:

    - ``str`` (documento de una sola página),
    - ``list[str]`` (texto por página),
    - ``list[dict]`` con claves ``text``/``content`` y, opcionalmente,
      ``page_number`` (el orden de la lista manda; ``page_number`` es informativo).
    """
    if isinstance(pages, str):
        return [pages]

    normalized: list[str] = []
    for entry in pages:
        if isinstance(entry, str):
            normalized.append(entry)
        elif isinstance(entry, Mapping):
            value = entry.get("text", entry.get("content"))
            if value is None:
                raise ValueError(
                    "Cada página debe ser str o Mapping con clave 'text'/'content'."
                )
            normalized.append(str(value))
        else:  # pragma: no cover - defensa ante tipos inesperados
            raise TypeError(f"Tipo de página no soportado: {type(entry).__name__}")
    return normalized


# ---------------------------------------------------------------------
# Cortado
# ---------------------------------------------------------------------
def _page_windows(text: str, max_chars: int, overlap: int) -> Iterable[tuple[int, int]]:
    """Genera ventanas ``(start, end)`` sobre el texto de una página.

    Corta preferentemente en un espacio/salto para no partir palabras, pero
    **nunca** altera el texto devuelto: la ventana siempre es un slice exacto de
    ``text``.
    """
    if max_chars <= 0:
        raise ValueError("max_chars debe ser > 0.")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap debe cumplir 0 <= overlap < max_chars.")

    length = len(text)
    start = 0
    while start < length:
        end = min(start + max_chars, length)
        if end < length:
            cut = max(
                text.rfind(" ", start, end),
                text.rfind("\n", start, end),
                text.rfind("\t", start, end),
            )
            # Corta en el separador solo si deja una ventana razonable.
            if cut > start + (max_chars // 2):
                end = cut + 1
        yield start, end
        if end >= length:
            return
        start = max(end - overlap, start + 1)


def chunk_page(
    text: str,
    page_number: int,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
    index_start: int = 0,
) -> list[Chunk]:
    """Fragmenta una página preservando ``page_number`` y coordenadas.

    Los fragmentos con solo espacios se descartan; el texto devuelto se recorta
    de espacios pero ``char_start``/``char_end`` se ajustan para que el slice
    siga siendo exacto.
    """
    if page_number < 1:
        raise ValueError("page_number es 1-based (>= 1).")

    chunks: list[Chunk] = []
    for start, end in _page_windows(text, max_chars, overlap):
        raw = text[start:end]
        stripped = raw.strip()
        if not stripped:
            continue
        lead = len(raw) - len(raw.lstrip())
        trail = len(raw) - len(raw.rstrip())
        char_start = start + lead
        char_end = end - trail
        chunks.append(
            Chunk(
                index=index_start + len(chunks),
                page_number=page_number,
                char_start=char_start,
                char_end=char_end,
                text=text[char_start:char_end],
            )
        )
    return chunks


def chunk_pages(
    pages: Sequence[str] | Sequence[Mapping[str, Any]] | str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    overlap: int = DEFAULT_OVERLAP,
) -> list[Chunk]:
    """Fragmenta un documento completo (texto por página) en :class:`Chunk`.

    Devuelve los chunks en orden global con ``index`` consecutivo.
    """
    normalized = normalize_pages(pages)
    chunks: list[Chunk] = []
    for page_number, text in enumerate(normalized, start=1):
        page_chunks = chunk_page(
            text,
            page_number,
            max_chars=max_chars,
            overlap=overlap,
            index_start=len(chunks),
        )
        chunks.extend(page_chunks)
    return chunks


# ---------------------------------------------------------------------
# Reconstrucción / verificación de evidencia
# ---------------------------------------------------------------------
def reconstruct_quote(
    pages: Sequence[str] | Sequence[Mapping[str, Any]] | str,
    page_number: int,
    char_start: int,
    char_end: int,
) -> str:
    """Reconstruye ``exact_quote`` desde coordenadas (SPEC-02 §6)."""
    normalized = normalize_pages(pages)
    if not 1 <= page_number <= len(normalized):
        raise ValueError(
            f"page_number {page_number} fuera de rango (1..{len(normalized)})."
        )
    return normalized[page_number - 1][char_start:char_end]


def verify_chunk(
    pages: Sequence[str] | Sequence[Mapping[str, Any]] | str, chunk: Chunk
) -> bool:
    """Verifica que el ``text`` del chunk coincide con sus coordenadas."""
    return reconstruct_quote(
        pages, chunk.page_number, chunk.char_start, chunk.char_end
    ) == chunk.text


def find_quote_span(page_text: str, quote: str, start: int = 0) -> tuple[int, int] | None:
    """Localiza ``quote`` en ``page_text`` y devuelve su span absoluto de página."""
    if not quote:
        return None
    index = page_text.find(quote, start)
    if index < 0:
        return None
    return index, index + len(quote)


__all__ = [
    "Chunk",
    "DEFAULT_MAX_CHARS",
    "DEFAULT_OVERLAP",
    "normalize_pages",
    "chunk_page",
    "chunk_pages",
    "reconstruct_quote",
    "verify_chunk",
    "find_quote_span",
]
