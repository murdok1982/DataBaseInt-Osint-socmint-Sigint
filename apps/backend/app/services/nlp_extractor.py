"""Pipeline NLP de ingesta v2.0: preflight → chunk → extract → validate → coherence → resolve → persist.

Autorizado por: **SPEC-02 §1–§6** y **SPEC-03 §5** (consumo).
Contrato que produce/consume: ``contracts/extraction-schema.json`` (SELLADO v2.0.0).
Zona: Agente 2 — ``apps/backend/app/services/nlp_extractor.py``.

Diseño
------
- **LLM con esquema forzado.** ``LLMClient`` (Protocol) expone
  ``extract(chunk, context=...)`` y devuelve un ``dict`` candidato con
  ``extracted_triplets`` y/o ``analytical_context_links``. ``FakeLLMClient``
  permite testear offline.
- **Contexto del pre-vuelo inyectado (SPEC-02 §1 Fase 1).** El diccionario de
  entidades canónicas se usa para rellenar ``existing_canonical_id`` (SPEC-02 §4.5)
  y se pasa al LLM como contexto.
- **Validación estricta.** Toda tripleta y todo vínculo se validan contra
  ``contracts/extraction-schema.json`` **antes** de tocar el grafo (SPEC-02 §6).
- **Umbral 0.50 (SPEC-02 §3).** ``confidence < 0.50`` → ``PENDING_HUMAN_REVIEW``
  (no impacta el grafo). ``>= 0.50`` → Entity Resolution de SPEC-01 (Agente 1,
  **inyectado**, no reimplementado) y persistencia de la arista.
- **Motor de Coherencia (Fase 2).** Los ``analytical_context_links`` se aplican
  con :class:`app.services.coherence_engine.CoherenceEngine` **antes** de
  resolver/persistir (orden del pipeline).
- **Idempotencia por `report_id` (SPEC-02 §5/§6).** El ``report_id`` lo asigna el
  servidor; la reingesta devuelve el resultado cacheado.
- **Normalización ES → Cypher.** ``normalize_relation_label`` mapea los conceptos
  en español (p. ej. ``UBICADO_EN``) a los tipos canónicos (``LOCATED_AT``).
"""

from __future__ import annotations

import copy
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, runtime_checkable

from pydantic import ValidationError as PydanticValidationError

from app.schemas.extraction import (
    AnalyticalContextLink,
    Entity,
    EntityLabel,
    ExtractionResult,
    LinkCategory,
    Predicate,
    PredicateStatus,
    PredicateType,
    Provenance,
    ReportMetadata,
    SchemaValidationError,
    Triplet,
    is_valid_contract,
    load_contract_schema,
    validate_contract as validate_extraction,
    validate_link,
    validate_triplet,
)
from app.services.chunking import (
    DEFAULT_MAX_CHARS,
    DEFAULT_OVERLAP,
    Chunk,
    chunk_pages,
)
from app.services.coherence_engine import CoherenceEngine, CoherenceOutcome
from app.services.entity_resolver import EntityResolver  # Agente 1 (solo lectura)
from app.services.preflight import (
    PreflightRequiredError,
    PreflightService,
    clean_keywords,
    derive_keywords,
    normalize_name,
)

# =====================================================================
# Constantes (SPEC-02 §3)
# =====================================================================
CONFIDENCE_THRESHOLD = 0.50
AUTO_ACCEPTED = PredicateStatus.AUTO_ACCEPTED.value
PENDING_HUMAN_REVIEW = PredicateStatus.PENDING_HUMAN_REVIEW.value

#: Confianza asignada cuando el LLM no la aporta (SPEC-02 §2).
DEFAULT_CONFIDENCE = 0.50


# =====================================================================
# Normalización de etiquetas de relación: ES → tipo Cypher canónico
# =====================================================================
#: Conceptos en español de SPEC-01 §3.1 → tipo Cypher canónico (ASCII).
#: La clave está normalizada (mayúsculas, sin acentos, espacios → ``_``).
_CONCEPT_TO_PREDICATE: dict[str, str] = {
    "CONTROLADO_POR": "CONTROLS",  # concepto invertido (ver SPEC-01 §3.1)
    "FINANCIO_A": "FINANCED",
    "USA_ACTIVO": "USES_ASSET",
    "UBICADO_EN": "LOCATED_AT",
    "ASOCIADO_CON": "ASSOCIATED_WITH",
    "PARTICIPO_EN": "PARTICIPATED_IN",
}

_CANONICAL_PREDICATES: frozenset[str] = frozenset(p.value for p in PredicateType)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _normalize_label_key(label: str) -> str:
    key = _strip_accents(str(label).strip()).upper()
    for char in (" ", "-", "."):
        key = key.replace(char, "_")
    while "__" in key:
        key = key.replace("__", "_")
    return key.strip("_")


def normalize_relation_label(label: str) -> str:
    """Mapea un concepto ES (o tipo ya canónico) al tipo Cypher de SPEC-01 §3.1.

    Acepta las equivalencias funcionales de SPEC-01 §3.1 (con o sin acentos),
    ``"UBICADO_EN"`` → ``"LOCATED_AT"``, ``"FINANCIÓ_A"`` → ``"FINANCED"``, y es
    **idempotente** sobre los tipos ya canónicos. Cualquier otro valor es
    desconocido y eleva :class:`ValueError` (no se inventan predicados).
    """
    if label is None:
        raise ValueError("normalize_relation_label requiere una etiqueta no nula.")
    key = _normalize_label_key(label)
    if key in _CONCEPT_TO_PREDICATE:
        return _CONCEPT_TO_PREDICATE[key]
    if key in _CANONICAL_PREDICATES:
        return key
    raise ValueError(
        f"Relación '{label}' no reconocida. SPEC-01 §3.1 permite: "
        f"{sorted(_CANONICAL_PREDICATES)}."
    )


def is_valid(payload: Mapping[str, Any]) -> bool:
    """``True`` si ``payload`` valida contra el contrato sellado (v2.0)."""
    return is_valid_contract(payload)


# =====================================================================
# Cliente LLM (interfaz + fake offline)
# =====================================================================
@runtime_checkable
class LLMClient(Protocol):
    """Interfaz del extractor LLM (SPEC-02 §1 Fase 1).

    Devuelve un ``Mapping`` con ``extracted_triplets`` y/o
    ``analytical_context_links`` validable contra el contrato v2.0.
    """

    def extract(
        self, chunk: Chunk, *, context: Any = None
    ) -> Mapping[str, Any]:
        """Extrae de ``chunk`` un dict con el esquema v2 (recibe el contexto)."""
        ...


class FakeLLMClient:
    """Cliente LLM determinista para tests (offline).

    ``responses`` puede ser:

    - ``Callable[[Chunk], Mapping]`` → se invoca por chunk (admite ``context``),
    - ``Mapping[str, Mapping]`` → se busca por ``chunk.text`` y, si no, por
      subcadena de la clave,
    - ``None`` → devuelve ``default``.
    """

    def __init__(
        self,
        responses: Optional[Callable[..., Mapping[str, Any]] | Mapping[str, Any]] = None,
        default: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self._responses = responses
        self._default: Mapping[str, Any] = default or {
            "extracted_triplets": [],
            "analytical_context_links": [],
        }
        self.calls: list[Chunk] = []
        self.contexts: list[Any] = []

    def extract(self, chunk: Chunk, *, context: Any = None) -> Mapping[str, Any]:
        self.calls.append(chunk)
        self.contexts.append(context)
        responses = self._responses
        if callable(responses):
            try:
                return copy.deepcopy(dict(responses(chunk, context=context)))
            except TypeError:
                return copy.deepcopy(dict(responses(chunk)))
        if isinstance(responses, Mapping):
            if chunk.text in responses:
                return copy.deepcopy(dict(responses[chunk.text]))
            for key, value in responses.items():
                if key and key in chunk.text:
                    return copy.deepcopy(dict(value))
        return copy.deepcopy(dict(self._default))


# =====================================================================
# Idempotencia por report_id (SPEC-02 §5/§6)
# =====================================================================
@runtime_checkable
class IngestRegistry(Protocol):
    """Registro inyectable de ingestas ya procesadas (idempotencia)."""

    def get(self, report_id: str) -> Optional["ReportOutcome"]: ...

    def put(self, report_id: str, outcome: "ReportOutcome") -> None: ...


class InMemoryIngestRegistry:
    """Implementación en memoria del registro de idempotencia."""

    def __init__(self) -> None:
        self._results: dict[str, "ReportOutcome"] = {}

    def get(self, report_id: str) -> Optional["ReportOutcome"]:
        return self._results.get(str(report_id))

    def put(self, report_id: str, outcome: "ReportOutcome") -> None:
        self._results[str(report_id)] = outcome

    def __contains__(self, report_id: object) -> bool:
        return str(report_id) in self._results

    def __len__(self) -> int:
        return len(self._results)

    def clear(self) -> None:
        self._results.clear()


# =====================================================================
# Resultado del pipeline
# =====================================================================
@dataclass
class ReportOutcome:
    """Resultado de procesar un informe (SPEC-02 §5)."""

    report_id: str
    metadata: dict[str, Any] = field(default_factory=dict)
    triplets: list[Triplet] = field(default_factory=list)
    accepted: list[Triplet] = field(default_factory=list)
    pending: list[Triplet] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    resolutions: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    links: list[AnalyticalContextLink] = field(default_factory=list)
    coherence: Optional[CoherenceOutcome] = None
    idempotent: bool = False
    written: bool = True

    def status_of(self, triplet: Triplet) -> str:
        """Estado **derivado** de una tripleta (SPEC-02 §3)."""
        if triplet in self.pending:
            return PENDING_HUMAN_REVIEW
        if triplet in self.accepted:
            return AUTO_ACCEPTED
        return PENDING_HUMAN_REVIEW


# =====================================================================
# Pipeline
# =====================================================================
class NLPExtractor:
    """Pipeline ``preflight → chunk → extract → validate → coherence → resolve → persist``.

    :param llm: cliente LLM (``LLMClient``).
    :param resolver: :class:`app.services.entity_resolver.EntityResolver`
        **inyectado** (Agente 1). No se reimplementa la resolución.
    :param repository: repositorio de grafo (duck-typing). Si se omite, se usa
        ``resolver.repository``.
    :param coherence: :class:`CoherenceEngine` (por defecto, compuesto sobre el
        repositorio si éste expone los escritores de coherencia).
    :param preflight: :class:`PreflightService` (para ``ingest_informant``).
    :param registry: registro de idempotencia (por defecto, en memoria).
    """

    def __init__(
        self,
        llm: LLMClient,
        resolver: EntityResolver,
        *,
        repository: Any = None,
        registry: Optional[IngestRegistry] = None,
        coherence: Optional[CoherenceEngine] = None,
        preflight: Optional[PreflightService] = None,
        threshold: float = CONFIDENCE_THRESHOLD,
        max_chars: int = DEFAULT_MAX_CHARS,
        overlap: int = DEFAULT_OVERLAP,
        default_confidence: float = DEFAULT_CONFIDENCE,
        strict_schema: bool = True,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold debe estar en [0.0, 1.0].")
        self.llm = llm
        self.resolver = resolver
        self.repository = repository if repository is not None else getattr(
            resolver, "repository", None
        )
        # Nota: `registry or ...` sería incorrecto porque un registro vacío es
        # "falsy" (define `__len__`); se comprueba explícitamente contra None.
        self.registry: IngestRegistry = (
            registry if registry is not None else InMemoryIngestRegistry()
        )
        if coherence is not None:
            self.coherence: Optional[CoherenceEngine] = coherence
        elif self.repository is not None and callable(
            getattr(self.repository, "corroborate", None)
        ):
            self.coherence = CoherenceEngine(self.repository)
        else:
            self.coherence = None
        self.preflight = preflight
        self.threshold = threshold
        self.max_chars = max_chars
        self.overlap = overlap
        self.default_confidence = default_confidence
        self.strict_schema = strict_schema
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- API principal -------------------------------------------------
    def chunk(self, pages: Sequence[str] | Sequence[Mapping[str, Any]] | str) -> list[Chunk]:
        """Fragmenta el informe preservando coordenadas (SPEC-02 §1.3)."""
        return chunk_pages(pages, max_chars=self.max_chars, overlap=self.overlap)

    def ingest(
        self,
        report_id: str | uuid.UUID,
        pages: Sequence[str] | Sequence[Mapping[str, Any]] | str,
        *,
        context: Any = None,
        metadata: Optional[Mapping[str, Any]] = None,
        reprocess: bool = False,
    ) -> ReportOutcome:
        """Procesa un informe completo de forma idempotente (SPEC-02 §5/§6)."""
        key = str(report_id)
        if not reprocess:
            cached = self.registry.get(key)
            if cached is not None:
                return replace(cached, idempotent=True)

        outcome = ReportOutcome(report_id=key, metadata=dict(metadata or {}))
        canonical_index = self._canonical_index(context)

        triplets: list[Triplet] = []
        links: list[AnalyticalContextLink] = []
        for chunk in self.chunk(pages):
            chunk_triplets, chunk_links = self._extract_chunk(
                chunk, context, canonical_index, outcome
            )
            triplets.extend(chunk_triplets)
            links.extend(chunk_links)

        outcome.triplets = triplets
        outcome.links = links

        # Fase 2 — Coherence Engine (antes de resolver/persistir).
        if self.coherence is not None and links:
            outcome.coherence = self.coherence.apply(key, links, triplets=triplets)

        # Fase 1/4 — decisión (umbral) + Entity Resolution + persistencia.
        for triplet in triplets:
            self._decide_and_resolve(triplet, key, outcome)

        outcome.written = bool(outcome.accepted)
        self.registry.put(key, outcome)
        return outcome

    #: Alias declarativo del flujo (SPEC-02 §5).
    process = ingest

    def ingest_informant(
        self,
        report_id: str | uuid.UUID,
        pages: Sequence[str] | Sequence[Mapping[str, Any]] | str,
        *,
        preflight_token: Optional[str],
        metadata: Optional[Mapping[str, Any]] = None,
        keywords: Optional[Sequence[str]] = None,
        reprocess: bool = False,
    ) -> ReportOutcome:
        """Ingesta del Informante IA — **exige** un ``preflight_token`` vigente.

        Constitución Regla 3 (prohibición de ingesta ciega): sin un token válido
        eleva :class:`PreflightRequiredError` (la API lo traduce a ``409``).
        """
        if self.preflight is None:
            raise PreflightRequiredError(
                "El pipeline no tiene configurado el servicio de pre-vuelo "
                "(SPEC-02 §1 Fase 0)."
            )
        self.preflight.require_valid_token(preflight_token)
        base_keywords = clean_keywords(keywords or [])
        if not base_keywords:
            base_keywords = derive_keywords(
                (metadata or {}).get("title"),
                (metadata or {}).get("source_url_or_origin"),
            )
        context = self.preflight.build_context(base_keywords)
        return self.ingest(
            report_id, pages, context=context, metadata=metadata, reprocess=reprocess
        )

    # -- Extracción y normalización ------------------------------------
    def _extract_chunk(
        self,
        chunk: Chunk,
        context: Any,
        canonical_index: dict[str, tuple[str | None, str | None]],
        outcome: ReportOutcome,
    ) -> tuple[list[Triplet], list[AnalyticalContextLink]]:
        raw = self._call_llm(chunk, context)
        triplets = self._validated_triplets(chunk, raw, canonical_index, outcome)
        links = self._validated_links(raw, outcome)
        return triplets, links

    def _call_llm(self, chunk: Chunk, context: Any) -> Mapping[str, Any]:
        try:
            raw = self.llm.extract(chunk, context=context)
        except TypeError:  # compatibilidad con clientes sin el parámetro context
            raw = self.llm.extract(chunk)
        return raw or {}

    def _validated_triplets(
        self,
        chunk: Chunk,
        raw: Mapping[str, Any],
        canonical_index: dict[str, tuple[str | None, str | None]],
        outcome: ReportOutcome,
    ) -> list[Triplet]:
        triplets: list[Triplet] = []
        for candidate in self._candidate_triplets(raw):
            data = self._build_triplet_dict(chunk, candidate, canonical_index)
            if not self._check(validate_triplet, data, outcome, "triplet"):
                continue
            try:
                triplets.append(Triplet.model_validate(data))
            except PydanticValidationError as error:  # pragma: no cover - defensa
                message = [
                    f"{'/'.join(map(str, err['loc']))}: {err['msg']}"
                    for err in error.errors()
                ]
                outcome.rejected.append({"triplet": data, "errors": message})
                if self.strict_schema:
                    raise SchemaValidationError(message) from error
        return triplets

    def _validated_links(
        self, raw: Mapping[str, Any], outcome: ReportOutcome
    ) -> list[AnalyticalContextLink]:
        links: list[AnalyticalContextLink] = []
        for candidate in self._candidate_links(raw):
            data = dict(candidate or {})
            if not self._check(validate_link, data, outcome, "link"):
                continue
            try:
                links.append(AnalyticalContextLink.model_validate(data))
            except PydanticValidationError as error:  # pragma: no cover - defensa
                message = [
                    f"{'/'.join(map(str, err['loc']))}: {err['msg']}"
                    for err in error.errors()
                ]
                outcome.rejected.append({"link": data, "errors": message})
                if self.strict_schema:
                    raise SchemaValidationError(message) from error
        return links

    def _check(
        self,
        validator: Callable[[Any], None],
        data: Any,
        outcome: ReportOutcome,
        kind: str,
    ) -> bool:
        try:
            validator(data)
        except SchemaValidationError as error:
            outcome.rejected.append({kind: data, "errors": error.errors})
            if self.strict_schema:
                raise
            return False
        return True

    @staticmethod
    def _candidate_triplets(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        candidates = raw.get("extracted_triplets")
        if candidates is None:
            candidates = raw.get("triplets")
        if candidates is None:
            if {"subject", "predicate", "object"} <= set(raw.keys()):
                return [raw]
            return []
        if isinstance(candidates, Mapping):
            return [candidates]
        return list(candidates)

    @staticmethod
    def _candidate_links(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        candidates = raw.get("analytical_context_links")
        if candidates is None:
            candidates = raw.get("links")
        if candidates is None:
            return []
        if isinstance(candidates, Mapping):
            return [candidates]
        return list(candidates)

    @staticmethod
    def _canonical_index(context: Any) -> dict[str, tuple[str | None, str | None]]:
        if context is None:
            return {}
        canonical_index = getattr(context, "canonical_index", None)
        if callable(canonical_index):
            return dict(canonical_index())
        entities = None
        if isinstance(context, Mapping):
            entities = context.get("canonical_entities")
        if not entities:
            return {}
        index: dict[str, tuple[str | None, str | None]] = {}
        for entity in entities:
            name = (entity or {}).get("canonical_name")
            if not name:
                continue
            index[normalize_name(str(name))] = (
                (entity or {}).get("id"),
                (entity or {}).get("label"),
            )
        return index

    def _build_triplet_dict(
        self,
        chunk: Chunk,
        candidate: Mapping[str, Any],
        canonical_index: dict[str, tuple[str | None, str | None]],
    ) -> dict[str, Any]:
        candidate = dict(candidate or {})
        return {
            "subject": self._normalize_entity(candidate.get("subject"), canonical_index),
            "predicate": self._normalize_predicate(candidate.get("predicate")),
            "object": self._normalize_entity(candidate.get("object"), canonical_index),
            "provenance": self._bind_provenance(chunk, candidate.get("provenance")),
        }

    @staticmethod
    def _normalize_entity(
        entity: Any, canonical_index: dict[str, tuple[str | None, str | None]]
    ) -> dict[str, Any]:
        entity = dict(entity or {})
        name = entity.get("name")
        label = entity.get("label")
        existing = entity.get("existing_canonical_id")
        if name is not None:
            lookup = canonical_index.get(normalize_name(str(name)))
            if lookup is not None:
                canonical_id, canonical_label = lookup
                if existing is None:
                    existing = canonical_id
                if not label and canonical_label:
                    label = canonical_label
        normalized: dict[str, Any] = {"label": label, "name": name}
        if existing is not None:
            normalized["existing_canonical_id"] = existing
        attributes = entity.get("attributes")
        normalized["attributes"] = dict(attributes) if attributes else {}
        return normalized

    def _normalize_predicate(self, predicate: Any) -> dict[str, Any]:
        predicate = dict(predicate or {})
        predicate_type = predicate.get("type")
        try:
            predicate_type = normalize_relation_label(predicate_type)
        except (ValueError, TypeError):
            # Se conserva el valor original; el JSON Schema lo rechazará por enum.
            pass
        confidence = predicate.get("confidence")
        if confidence is None:
            confidence = self.default_confidence
        return {"type": predicate_type, "confidence": confidence}

    def _bind_provenance(self, chunk: Chunk, provenance: Any) -> dict[str, Any]:
        """Fija la procedencia a las coordenadas del chunk (SPEC-02 §2/§6).

        Si el LLM aporta un ``exact_quote`` verificable dentro del chunk, se
        localiza y se calculan coordenadas absolutas de página; si no, se usa el
        texto íntegro del chunk (que es un slice exacto de la página).
        """
        provenance = dict(provenance or {})
        quote = provenance.get("exact_quote")
        char_start, char_end = chunk.char_start, chunk.char_end
        if isinstance(quote, str) and quote:
            index = chunk.text.find(quote)
            if index >= 0:
                char_start = chunk.char_start + index
                char_end = char_start + len(quote)
            else:
                quote = chunk.text
                char_start, char_end = chunk.char_start, chunk.char_end
        else:
            quote = chunk.text
        if len(quote) > 2000:  # maxLength del contrato
            quote = quote[:2000]
            char_end = char_start + len(quote)
        return {
            "page_number": int(chunk.page_number),
            "char_start": int(char_start),
            "char_end": int(char_end),
            "exact_quote": quote,
        }

    # -- Decisión (SPEC-02 §3) y resolución (SPEC-01 §4) ----------------
    def _decide_and_resolve(
        self, triplet: Triplet, report_id: str, outcome: ReportOutcome
    ) -> None:
        confidence = triplet.predicate.confidence
        if confidence < self.threshold:
            outcome.pending.append(triplet)
            return

        outcome.accepted.append(triplet)

        subject = self._resolve_entity(triplet.subject)
        object_ = self._resolve_entity(triplet.object)
        outcome.resolutions.append(
            {
                "subject": subject,  # ResolutionResult (SPEC-01 §4)
                "object": object_,
                "predicate": triplet.predicate.type.value,
            }
        )

        if self.repository is not None:
            outcome.edges.append(
                self._persist_edge(
                    triplet, report_id, subject.canonical_id, object_.canonical_id
                )
            )

    def _resolve_entity(self, entity: Entity) -> Any:
        """Resuelve una entidad; ``existing_canonical_id`` tiene máxima prioridad."""
        attrs = dict(entity.attributes)
        if entity.existing_canonical_id:
            # El repositorio hace MERGE/ find_by_unique_key por `id` y respeta
            # `locked_by_analyst` (SPEC-02 §4.5 · Regla 2).
            attrs["id"] = entity.existing_canonical_id
        return self.resolver.resolve(entity.label.value, entity.name, attrs)

    def _persist_edge(
        self,
        triplet: Triplet,
        report_id: str,
        from_id: Optional[str],
        to_id: Optional[str],
    ) -> dict[str, Any]:
        now = self._now().isoformat()
        quote = triplet.provenance.exact_quote
        props: dict[str, Any] = {
            "confidence": triplet.predicate.confidence,
            "source_report_id": str(report_id),
            "exact_quote": quote,
            "text_evidence_span": quote,
            "page_number": triplet.provenance.page_number,
            "first_seen": now,
            "last_seen": now,
            "locked_by_analyst": False,
        }
        return self.repository.upsert_relationship(
            from_id, to_id, triplet.predicate.type.value, props
        )


#: Alias declarativo pedido por la spec ("NLPipeline/NLPExtractor").
NLPipeline = NLPExtractor


__all__ = [
    "AUTO_ACCEPTED",
    "CONFIDENCE_THRESHOLD",
    "DEFAULT_CONFIDENCE",
    "FakeLLMClient",
    "IngestRegistry",
    "InMemoryIngestRegistry",
    "LLMClient",
    "NLPExtractor",
    "NLPipeline",
    "PENDING_HUMAN_REVIEW",
    "ReportOutcome",
    "SchemaValidationError",
    "is_valid",
    "load_contract_schema",
    "normalize_relation_label",
    "validate_extraction",
    "validate_link",
    "validate_triplet",
]
