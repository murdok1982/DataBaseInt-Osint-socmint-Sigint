"""Orquestador del ciclo completo del Informante IA (SPEC-02 §1).

Ciclo por documento:

1. **Fase 0** — ``preflight`` (nunca operar a ciegas).
2. **Fase 1** — extracción guiada por contexto + validación contra el contrato.
3. **Envío** — ``ingest_informant`` con ``X-Preflight-Token`` del paso 1.

El pipeline **no** puede saltarse el pre-vuelo: si el token falta o está vacío se
eleva :class:`~.api_client.PreflightRequired` y no se contacta ``/ingest/informant``.
Tampoco envía payloads que no validen al 100% contra el contrato sellado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from .api_client import InformantApi, PreflightRequired
from .collectors.base import RawDocument
from .contract import validate_extraction
from .extractor import ContextGuidedExtractor
from .models import IngestJob, PreflightResponse

#: Validador inyectable (por defecto, contrato sellado).
Validator = Callable[[Mapping[str, Any]], None]

_STOP = {"the", "a", "an", "of", "and", "or", "to", "in", "on", "for", "el", "la", "los", "las", "de", "del", "y", "o", "en"}


@dataclass
class RunResult:
    """Resultado del ciclo completo para una fuente."""

    document: RawDocument
    preflight: PreflightResponse
    payload: dict[str, Any]
    job: IngestJob

    @property
    def triplet_count(self) -> int:
        return len(self.payload.get("extracted_triplets", []))

    @property
    def link_categories(self) -> list[str]:
        return [link["link_category"] for link in self.payload.get("analytical_context_links", [])]


class InformantPipeline:
    """Ejecuta ``preflight → extraer → validar → ingerir`` por cada fuente."""

    def __init__(
        self,
        api: InformantApi,
        *,
        extractor: ContextGuidedExtractor | None = None,
        validator: Validator = validate_extraction,
    ):
        self.api = api
        self.extractor = extractor or ContextGuidedExtractor()
        self.validator = validator

    # ------------------------------------------------------------------ #
    def run(self, document: RawDocument) -> RunResult:
        # 1) Fase 0 — pre-vuelo obligatorio.
        keywords = derive_keywords(document)
        entity_hints = list(document.entity_hints) or None
        preflight = self.api.preflight(keywords=keywords, entity_hints=entity_hints)

        token = getattr(preflight, "preflight_token", None)
        if not token:
            raise PreflightRequired(
                "El pre-vuelo no devolvió preflight_token; se aborta para no "
                "incurrir en ingesta ciega (SPEC-03 §5)."
            )

        # 2) Fase 1 — extracción guiada + validación estricta.
        payload = self.extractor.build(document, preflight)
        self.validator(payload)

        # 3) Envío M2M con el token del pre-vuelo de ESTA ejecución.
        job = self.api.ingest_informant(
            filename=document.filename,
            content=document.content_bytes(),
            payload=payload,
            preflight_token=token,
        )
        return RunResult(document=document, preflight=preflight, payload=payload, job=job)

    def run_all(self, documents: Iterable[RawDocument]) -> list[RunResult]:
        return [self.run(document) for document in documents]


def derive_keywords(document: RawDocument) -> list[str]:
    """Palabras clave preliminares para el pre-vuelo (SPEC-02 §1 Fase 0 paso 1)."""
    keywords: list[str] = []

    def _push(value: str) -> None:
        value = value.strip()
        if value and value.lower() not in {k.lower() for k in keywords}:
            keywords.append(value)

    for keyword in document.keywords:
        _push(keyword)

    if not keywords:
        for word in re.split(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]+", document.title):
            if len(word) > 3 and word.lower() not in _STOP:
                _push(word)

    if not keywords and document.title.strip():
        _push(document.title)

    if not keywords:
        keywords.append("osint")

    return keywords


__all__ = ["InformantPipeline", "RunResult", "derive_keywords"]
