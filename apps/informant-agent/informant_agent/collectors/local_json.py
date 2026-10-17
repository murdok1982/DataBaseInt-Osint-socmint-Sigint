"""Recolector de ejemplo: documentos OSINT en JSON local.

Formato por archivo (``*.json``)::

    {
      "title": "Informe X",
      "source_type": "NEWS",                     # enum del contrato
      "source_url_or_origin": "https://...",
      "reliability_rating": "B",                  # A–F
      "keywords": ["Acme Corp", "State Bank"],    # opcional (Fase 0)
      "entity_hints": ["Acme Corp"],              # opcional
      "pages": [ {"page_number": 1, "text": "..."} ],
      "assertions": [                             # opcional (relaciones explícitas)
        {
          "subject_name": "State Bank",
          "predicate": "FINANCED",
          "object_name": "Acme Corp",
          "quote": "State Bank financed Acme Corp",
          "page_number": 1,
          "confidence": 0.8
        }
      ]
    }

El recolector solo lee; la validación del contrato la hace el extractor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .base import Assertion, Page, RawDocument


class LocalJsonCollector:
    """Itera documentos ``*.json`` de un directorio (orden determinista)."""

    def __init__(self, source_dir: str | Path):
        self.source_dir = Path(source_dir).resolve()

    def collect(self) -> Iterable[RawDocument]:
        if not self.source_dir.is_dir():
            raise FileNotFoundError(f"Directorio de fuentes inexistente: {self.source_dir}")
        for path in sorted(self.source_dir.glob("*.json")):
            yield self._load(path)

    @staticmethod
    def _load(path: Path) -> RawDocument:
        with path.open("r", encoding="utf-8") as fh:
            data: dict[str, Any] = json.load(fh)

        pages = tuple(
            Page(page_number=int(p["page_number"]), text=str(p["text"]))
            for p in data.get("pages", [])
        )
        assertions = tuple(_assertion_from_dict(a) for a in data.get("assertions", []))

        origin = data.get("source_url_or_origin") or path.resolve().as_uri()
        return RawDocument(
            title=str(data["title"]),
            source_type=str(data["source_type"]),
            source_url_or_origin=str(origin),
            reliability_rating=str(data.get("reliability_rating", "F")),
            pages=pages,
            local_path=path,
            keywords=tuple(str(k) for k in data.get("keywords", [])),
            entity_hints=tuple(str(k) for k in data.get("entity_hints", [])),
            assertions=assertions,
        )


def _assertion_from_dict(raw: dict[str, Any]) -> Assertion:
    return Assertion(
        predicate=str(raw["predicate"]),
        subject_name=str(raw["subject_name"]),
        object_name=str(raw["object_name"]),
        quote=str(raw["quote"]),
        page_number=int(raw.get("page_number", 1)),
        confidence=(float(raw["confidence"]) if raw.get("confidence") is not None else None),
        subject_label=raw.get("subject_label"),
        object_label=raw.get("object_label"),
        char_start=(int(raw["char_start"]) if raw.get("char_start") is not None else None),
        char_end=(int(raw["char_end"]) if raw.get("char_end") is not None else None),
    )


__all__ = ["LocalJsonCollector"]
