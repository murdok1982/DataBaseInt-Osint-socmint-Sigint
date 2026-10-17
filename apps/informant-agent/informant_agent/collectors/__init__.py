"""Recolectores de fuentes abiertas.

* :class:`LocalJsonCollector` — documentos JSON locales (``*.json``).
* :class:`TextDirCollector` — archivos de texto plano (``*.txt``), una página.
* :func:`collector_from_settings` — elige el recolector por configuración.
"""

from __future__ import annotations

from .base import Assertion, Collector, Page, RawDocument
from .local_json import LocalJsonCollector
from .text_dir import TextDirCollector

__all__ = [
    "Assertion",
    "Collector",
    "LocalJsonCollector",
    "Page",
    "RawDocument",
    "TextDirCollector",
    "collector_from_settings",
]


def collector_from_settings(settings) -> Collector:
    """Devuelve el recolector por defecto a partir de :class:`Settings`."""
    source_dir = getattr(settings, "source_dir", None)
    if not source_dir:
        raise ValueError(
            "Define INFORMANT_SOURCE_DIR (o --source-dir) para indicar el "
            "directorio de fuentes abiertas."
        )
    return LocalJsonCollector(source_dir)
