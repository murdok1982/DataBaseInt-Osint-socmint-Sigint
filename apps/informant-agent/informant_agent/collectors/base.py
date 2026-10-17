"""Modelos de fuente y la interfaz ``Collector`` (SPEC-02 §1 Fase 0).

Un :class:`RawDocument` es una fuente OSINT ya normalizada a páginas con texto y,
opcionalmente, *assertions* estructuradas (relaciones explícitas). El recolector
**nunca** decide coherencia: solo entrega fuentes; la extracción y la referencia
analítica las hacen ``extractor`` y ``coherence``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class Page:
    """Página de una fuente (``page_number`` ≥ 1 y su texto)."""

    page_number: int
    text: str


@dataclass(frozen=True)
class Assertion:
    """Relación explícita aportada por la fuente (se valida su ``quote`` literal).

    El extractor **descarta** cualquier assertion cuyo ``quote`` no aparezca
    literalmente en la página indicada (regla de rigor de evidencia).
    """

    predicate: str
    subject_name: str
    object_name: str
    quote: str
    page_number: int = 1
    confidence: float | None = None
    subject_label: str | None = None
    object_label: str | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True)
class RawDocument:
    """Fuente OSINT normalizada que el Informante IA puede procesar."""

    title: str
    source_type: str
    source_url_or_origin: str
    reliability_rating: str
    pages: tuple[Page, ...] = field(default_factory=tuple)
    local_path: Path | None = None
    raw_bytes: bytes | None = None
    keywords: tuple[str, ...] = field(default_factory=tuple)
    entity_hints: tuple[str, ...] = field(default_factory=tuple)
    assertions: tuple[Assertion, ...] = field(default_factory=tuple)

    @property
    def filename(self) -> str:
        if self.local_path is not None:
            return self.local_path.name
        return f"{_slug(self.title) or 'document'}.txt"

    def full_text(self) -> str:
        return "\n".join(page.text for page in self.pages)

    def content_bytes(self) -> bytes:
        """Bytes físicos a enviar como ``file`` en ``/ingest/informant``."""
        if self.local_path is not None and self.local_path.is_file():
            return self.local_path.read_bytes()
        if self.raw_bytes is not None:
            return self.raw_bytes
        # Último recurso determinista: el texto de las páginas.
        return self.full_text().encode("utf-8")


def _slug(text: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in text).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned[:80]


@runtime_checkable
class Collector(Protocol):
    """Interfaz de recolección de fuentes abiertas (SPEC-02 §1 Fase 0)."""

    def collect(self) -> Iterable[RawDocument]:
        """Itera las fuentes disponibles (streaming, sin red obligatoria)."""
        ...


__all__ = ["Assertion", "Collector", "Page", "RawDocument"]
