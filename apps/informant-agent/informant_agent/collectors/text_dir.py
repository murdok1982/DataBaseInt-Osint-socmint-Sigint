"""Recolector de ejemplo: archivos de texto plano (``*.txt``) → una página.

Demuestra que la interfaz :class:`Collector` es agnóstica al formato: cada archivo
se convierte en un :class:`RawDocument` con una única :class:`Page`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from .base import Page, RawDocument


class TextDirCollector:
    """Itera archivos ``*.txt`` de un directorio (orden determinista)."""

    def __init__(
        self,
        source_dir: str | Path,
        *,
        source_type: str = "NEWS",
        reliability_rating: str = "C",
    ):
        self.source_dir = Path(source_dir).resolve()
        self.source_type = source_type
        self.reliability_rating = reliability_rating

    def collect(self) -> Iterable[RawDocument]:
        if not self.source_dir.is_dir():
            raise FileNotFoundError(f"Directorio de fuentes inexistente: {self.source_dir}")
        for path in sorted(self.source_dir.glob("*.txt")):
            text = path.read_text(encoding="utf-8")
            yield RawDocument(
                title=path.stem,
                source_type=self.source_type,
                source_url_or_origin=path.resolve().as_uri(),
                reliability_rating=self.reliability_rating,
                pages=(Page(page_number=1, text=text),),
                local_path=path,
            )


__all__ = ["TextDirCollector"]
