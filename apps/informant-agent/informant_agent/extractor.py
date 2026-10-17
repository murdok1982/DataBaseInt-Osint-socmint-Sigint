"""Extracción guiada por contexto (**Fase 1**, SPEC-02 §1).

Produce un objeto que valida **100 %** contra ``contracts/extraction-schema.json``.

Diseño
------
* :class:`ContextGuidedExtractor` inyecta el contexto del pre-vuelo (diccionario de
  entidades canónicas, tesis y RFIs) y:
  - resuelve ``existing_canonical_id`` cuando el pre-vuelo aporta la entidad canónica
    (SPEC-02 §4.5);
  - **verifica el grounding**: toda tripleta exige ``exact_quote`` **literal** en la
    página indicada (``page_number``); las assertions no verificadas se **descartan**
    (prohibición de inventar);
  - delega los ``analytical_context_links`` en :class:`~.coherence.LinkAnalyzer`.
* Las relaciones provienen de dos fuentes deterministas:
  1. ``document.assertions`` (relaciones explícitas que la fuente estructurada aporta);
  2. si no hay assertions, un **detector por patrones** (regex) sobre el texto.
* El resultado se valida con Pydantic **y** con el JSON Schema sellado.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .coherence import LinkAnalyzer
from .collectors.base import Assertion, Page, RawDocument
from .contract import validate_extraction
from .models import ExtractionResult, PreflightResponse

#: Confianza por defecto por predicado (heurística determinista, ≥0.50).
DEFAULT_PREDICATE_CONFIDENCE: dict[str, float] = {
    "CONTROLS": 0.70,
    "FINANCED": 0.75,
    "USES_ASSET": 0.70,
    "LOCATED_AT": 0.65,
    "ASSOCIATED_WITH": 0.60,
    "PARTICIPATED_IN": 0.60,
}

_MAX_QUOTE = 2000

#: Fragmento de nombre propio (con acentos), 1–6 palabras capitalizadas.
_ENTITY = r"[A-Z][\w'’&.\-]*(?:\s+(?:de\s+|del\s+|of\s+|la\s+|los\s+|the\s+)?[A-Z][\w'’&.\-]*){0,5}"


@dataclass(frozen=True)
class _Pattern:
    regex: re.Pattern[str]
    predicate: str
    subject_label: str | None = None
    object_label: str | None = None


#: Detector por patrones (ES/EN). Los grupos se llaman ``subject`` y ``object``.
_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        re.compile(
            rf"(?P<subject>{_ENTITY})\s+(?:financi[oó]|financia(?:ron|ba)?|financed|funded|founds?)\s+(?P<object>{_ENTITY})"
        ),
        "FINANCED",
        object_label="Organization",
    ),
    _Pattern(
        re.compile(
            rf"(?P<subject>{_ENTITY})\s+(?:controla|controlaba|controls?|owns?|posee|es\s+matriz\s+de)\s+(?P<object>{_ENTITY})"
        ),
        "CONTROLS",
        object_label="Organization",
    ),
    _Pattern(
        re.compile(
            rf"(?P<subject>{_ENTITY})\s+(?:usa|utiliza|usaba|uses?|utiliz[oó]|operates?|explota)\s+(?P<object>{_ENTITY})"
        ),
        "USES_ASSET",
        object_label="CyberAsset",
    ),
    _Pattern(
        re.compile(
            rf"(?P<subject>{_ENTITY})\s+(?:est[aá]\s+ubicad[oa]|is\s+located|located|based|tiene\s+sede|con\s+sede)\s+(?:en|in)\s+(?P<object>{_ENTITY})"
        ),
        "LOCATED_AT",
        object_label="Location",
    ),
    _Pattern(
        re.compile(
            rf"(?P<subject>{_ENTITY})\s+(?:est[aá]\s+asociad[oa]|associated|linked|vinculad[oa])\s+(?:con|with|to|a)\s+(?P<object>{_ENTITY})"
        ),
        "ASSOCIATED_WITH",
    ),
    _Pattern(
        re.compile(
            rf"(?P<subject>{_ENTITY})\s+(?:particip[oó]|participated|attended|asisti[oó])\s+(?:en|in)\s+(?P<object>{_ENTITY})"
        ),
        "PARTICIPATED_IN",
        object_label="Event",
    ),
)

_ORG_HINTS = (
    "corp", "inc", "ltd", "llc", "gmbh", "plc", "s.a.", "sa ", "bank", "banco",
    "group", "grupo", "fund", "fondo", "foundation", "fundación", "company",
    "holding", "ministry", "ministerio", "agency", "agencia", "university",
    "universidad", "s.a",
)
_EVENT_HINTS = (
    "summit", "cumbre", "conference", "conferencia", "congress", "congreso",
    "forum", "foro", "festival", "meeting", "reunión", "reunion", "olympics",
)
_LOCATION_GAZETTEER = frozenset(
    {
        "madrid", "barcelona", "moscu", "moscow", "berlin", "berlín", "london",
        "londres", "paris", "parís", "geneva", "ginebra", "kiev", "kyiv",
        "beijing", "pekin", "washington", "tehran", "teheran", "damascus",
        "damasco", "brussels", "bruselas", "rome", "roma", "lisbon", "lisboa",
        "new york", "nueva york", "bogota", "bogotá", "lima", "santiago",
    }
)
_CYBER_RE = re.compile(
    r"^(?:(?:\d{1,3}\.){3}\d{1,3}|(?:https?://)?[\w.\-]+\.[a-z]{2,})$", re.IGNORECASE
)

_ALLOWED_LABELS = frozenset({"Person", "Organization", "CyberAsset", "Location", "Event"})


def normalize_name(value: str) -> str:
    """Minúsculas, sin acentos y espacios colapsados (SPEC-02 §4.1)."""
    decomposed = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", value.lower().strip())


def _infer_label(name: str) -> str | None:
    """Infiere un label **solo** cuando hay señal clara (si no, ``None``)."""
    lowered = normalize_name(name)
    if _CYBER_RE.match(lowered):
        return "CyberAsset"
    if any(hint.strip() and hint.strip() in lowered for hint in _ORG_HINTS):
        return "Organization"
    if lowered in _LOCATION_GAZETTEER:
        return "Location"
    if any(hint in lowered for hint in _EVENT_HINTS):
        return "Event"
    return None


class ContextGuidedExtractor:
    """Extrae tripletas + vínculos analíticos guiados por el contexto del pre-vuelo."""

    def __init__(self, link_analyzer: LinkAnalyzer | None = None):
        self.link_analyzer = link_analyzer or LinkAnalyzer()

    # ------------------------------------------------------------------ #
    def build(self, document: RawDocument, context: PreflightResponse) -> dict[str, Any]:
        """Construye y valida el payload de extracción (contrato sellado)."""
        canonical = _canonical_index(context)

        assertions: tuple[Assertion, ...] = document.assertions or tuple(
            _detect_assertions(document.pages)
        )

        triplets: list[dict[str, Any]] = []
        for assertion in assertions:
            triplet = self._triplet_from_assertion(assertion, document.pages, canonical)
            if triplet is not None:
                triplets.append(triplet)

        links = self.link_analyzer.evaluate(document, context)

        payload: dict[str, Any] = {
            "report_metadata": {
                "title": document.title,
                "source_type": document.source_type,
                "source_url_or_origin": document.source_url_or_origin,
                "reliability_rating": document.reliability_rating,
            },
            "extracted_triplets": triplets,
            "analytical_context_links": links,
        }

        # 1) Validación estricta Pydantic (extra="forbid").
        ExtractionResult.model_validate(payload)
        # 2) Validación 100% contra el contrato sellado.
        validate_extraction(payload)
        return payload

    # ------------------------------------------------------------------ #
    def _triplet_from_assertion(
        self,
        assertion: Assertion,
        pages: Sequence[Page],
        canonical: dict[str, tuple[str | None, str | None]],
    ) -> dict[str, Any] | None:
        page = _page_by_number(pages, assertion.page_number)
        if page is None:
            return None  # sin página ⇒ sin provenance válida

        quote = assertion.quote
        if not quote or quote not in page.text:
            return None  # grounding: la cita debe ser literal (no se inventa)
        if len(quote) > _MAX_QUOTE:
            return None

        if assertion.char_start is not None and assertion.char_end is not None:
            start, end = assertion.char_start, assertion.char_end
            if not (0 <= start <= end <= len(page.text)) or page.text[start:end] != quote:
                start = page.text.find(quote)
                end = start + len(quote)
        else:
            start = page.text.find(quote)
            end = start + len(quote)

        predicate_type = assertion.predicate
        confidence = assertion.confidence
        if confidence is None:
            confidence = DEFAULT_PREDICATE_CONFIDENCE.get(predicate_type, 0.50)

        try:
            subject = _entity(assertion.subject_name, assertion.subject_label, canonical)
            obj = _entity(assertion.object_name, assertion.object_label, canonical)
        except KeyError:
            return None

        return {
            "subject": subject,
            "predicate": {"type": predicate_type, "confidence": confidence},
            "object": obj,
            "provenance": {
                "page_number": assertion.page_number,
                "char_start": start,
                "char_end": end,
                "exact_quote": quote,
            },
        }


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _page_by_number(pages: Sequence[Page], number: int) -> Page | None:
    for page in pages:
        if page.page_number == number:
            return page
    return None


def _canonical_index(context: PreflightResponse) -> dict[str, tuple[str | None, str | None]]:
    """``normalize_name(canonical_name) -> (id, label)`` del diccionario canónico."""
    index: dict[str, tuple[str | None, str | None]] = {}
    for entity in context.canonical_entities:
        if not entity.canonical_name:
            continue
        label = entity.label if entity.label in _ALLOWED_LABELS else None
        index[normalize_name(entity.canonical_name)] = (entity.id, label)
    return index


def _entity(
    name: str,
    hint_label: str | None,
    canonical: dict[str, tuple[str | None, str | None]],
) -> dict[str, Any]:
    """Entidad resuelta: canonical id (si procede) + label inferido."""
    clean = name.strip()
    if not clean:
        raise KeyError(name)

    existing_id: str | None = None
    canonical_label: str | None = None
    lookup = canonical.get(normalize_name(clean))
    if lookup is not None:
        existing_id, canonical_label = lookup

    label = canonical_label or _infer_label(clean) or (hint_label if hint_label in _ALLOWED_LABELS else None) or "Person"

    entity: dict[str, Any] = {"label": label, "name": clean}
    if existing_id is not None:
        entity["existing_canonical_id"] = existing_id
    return entity


def _detect_assertions(pages: Sequence[Page]) -> Iterable[Assertion]:
    """Detector determinista por patrones (regex) sobre el texto de cada página."""
    seen: set[tuple] = set()
    for page in pages:
        for pattern in _PATTERNS:
            for match in pattern.regex.finditer(page.text):
                subject = match.group("subject").strip().strip(".,;:!?")
                obj = match.group("object").strip().strip(".,;:!?")
                quote = match.group(0).strip()
                if not subject or not obj or not quote:
                    continue
                key = (pattern.predicate, normalize_name(subject), normalize_name(obj), page.page_number)
                if key in seen:
                    continue
                seen.add(key)
                yield Assertion(
                    predicate=pattern.predicate,
                    subject_name=subject,
                    object_name=obj,
                    quote=quote,
                    page_number=page.page_number,
                    subject_label=pattern.subject_label,
                    object_label=pattern.object_label,
                    char_start=match.start(),
                    char_end=match.end(),
                )


__all__ = [
    "DEFAULT_PREDICATE_CONFIDENCE",
    "ContextGuidedExtractor",
    "normalize_name",
]
