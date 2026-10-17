"""Modelos Pydantic que **espejan** el contrato sellado.

Contrato rector: ``contracts/extraction-schema.json`` (SEALED v2.0.0 — 2026-10-17).
Regla del contrato: ``additionalProperties: false`` → ``extra="forbid"``.

* Raíz: ``{report_metadata, extracted_triplets, analytical_context_links}``.
* ``report_metadata``: ``{title, source_type, source_url_or_origin, reliability_rating}``.
* Entidades extraíbles: 5 labels (``Person``, ``Organization``, ``CyberAsset``,
  ``Location``, ``Event``) — ver SPEC-01 §1.
* Predicados: 6 aristas OSINT (SPEC-01 §3.1).
* ``predicate`` es un **objeto** ``{type, confidence}`` (SPEC-02 §2).
* ``provenance`` exige ``page_number`` + ``exact_quote`` literal (SPEC-02 §2).

Los modelos de Pre-Flight/Ingesta espejan ``contracts/openapi.yaml``
(``PreflightRequest``, ``PreflightResponse``, ``IngestJob``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------- #
# Enumeraciones del contrato sellado (extraction-schema.json $defs)
# --------------------------------------------------------------------------- #
EntityLabel = Literal["Person", "Organization", "CyberAsset", "Location", "Event"]
PredicateType = Literal[
    "CONTROLS",
    "FINANCED",
    "USES_ASSET",
    "LOCATED_AT",
    "ASSOCIATED_WITH",
    "PARTICIPATED_IN",
]
LinkCategory = Literal["CORROBORATES", "CONTRADICTS", "EXTENDS", "ANSWERS_REQUIREMENT"]
SourceType = Literal["OSINT_PDF", "NEWS", "TELEGRAM", "RSS", "PUBLIC_REGISTRY"]
ReliabilityRating = Literal["A", "B", "C", "D", "E", "F"]
RequirementPriority = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]

#: Espejo de ``additionalProperties: false``.
_STRICT = ConfigDict(extra="forbid")
#: Los esquemas de openapi.yaml no declaran ``additionalProperties: false``.
_TOLERANT = ConfigDict(extra="allow")


class ReportMetadata(BaseModel):
    """``$defs.report_metadata``."""

    model_config = _STRICT

    title: str = Field(min_length=1)
    source_type: SourceType
    source_url_or_origin: str
    reliability_rating: ReliabilityRating


class Entity(BaseModel):
    """``$defs.entity`` — usa ``existing_canonical_id`` cuando el pre-vuelo lo aporte."""

    model_config = _STRICT

    existing_canonical_id: str | None = None
    label: EntityLabel
    name: str = Field(min_length=1, max_length=512)
    attributes: dict[str, Any] = Field(default_factory=dict)


class Predicate(BaseModel):
    """``$defs.predicate`` — objeto ``{type, confidence}`` (SPEC-02 §2)."""

    model_config = _STRICT

    type: PredicateType
    confidence: float = Field(ge=0.0, le=1.0)


class Provenance(BaseModel):
    """``$defs.provenance`` — rigor de evidencia (SPEC-02 §2)."""

    model_config = _STRICT

    page_number: int = Field(ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    exact_quote: str = Field(min_length=1, max_length=2000)


class Triplet(BaseModel):
    """``$defs.triplet``."""

    model_config = _STRICT

    subject: Entity
    predicate: Predicate
    object: Entity
    provenance: Provenance


class AnalyticalContextLink(BaseModel):
    """``$defs.analytical_context_link`` — referencia a Análisis/IntelRequirement."""

    model_config = _STRICT

    target_id: str
    link_category: LinkCategory
    confidence: float = Field(ge=0.0, le=1.0)
    coherence_justification: str = Field(min_length=1)
    exact_quote: str = Field(min_length=1)


class ExtractionResult(BaseModel):
    """Raíz del contrato de salida del Informante IA (SPEC-02 §2)."""

    model_config = _STRICT

    report_metadata: ReportMetadata
    extracted_triplets: list[Triplet] = Field(default_factory=list)
    analytical_context_links: list[AnalyticalContextLink] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Modelos de la API M2M (openapi.yaml)
# --------------------------------------------------------------------------- #
class PreflightAnalysis(BaseModel):
    """``PreflightResponse.analyses[]``."""

    model_config = _TOLERANT

    id: str
    code: str | None = None
    thesis_summary: str | None = None


class PreflightRequirement(BaseModel):
    """``PreflightResponse.requirements[]`` (IntelRequirement abierto)."""

    model_config = _TOLERANT

    id: str
    question: str | None = None
    priority: RequirementPriority | None = None


class PreflightCanonicalEntity(BaseModel):
    """``PreflightResponse.canonical_entities[]`` (diccionario canónico)."""

    model_config = _TOLERANT

    id: str
    label: str | None = None
    canonical_name: str | None = None


class PreflightResponse(BaseModel):
    """``PreflightResponse`` (openapi.yaml, ``/context/preflight``)."""

    model_config = _TOLERANT

    preflight_token: str
    analyses: list[PreflightAnalysis] = Field(default_factory=list)
    requirements: list[PreflightRequirement] = Field(default_factory=list)
    canonical_entities: list[PreflightCanonicalEntity] = Field(default_factory=list)


class PreflightRequest(BaseModel):
    """``PreflightRequest`` (openapi.yaml): ``{keywords[], entity_hints[]}``."""

    model_config = _STRICT

    keywords: list[str] = Field(min_length=1)
    entity_hints: list[str] | None = None


class IngestJob(BaseModel):
    """``IngestJob`` (openapi.yaml) — respuesta ``202`` de la ingesta."""

    model_config = _TOLERANT

    job_id: str | None = None
    status: str | None = None
    progress: float | None = None
    pending_review: int | None = None


__all__ = [
    "AnalyticalContextLink",
    "Entity",
    "EntityLabel",
    "ExtractionResult",
    "IngestJob",
    "LinkCategory",
    "Predicate",
    "PredicateType",
    "PreflightAnalysis",
    "PreflightCanonicalEntity",
    "PreflightRequest",
    "PreflightRequirement",
    "PreflightResponse",
    "Provenance",
    "ReliabilityRating",
    "ReportMetadata",
    "RequirementPriority",
    "SourceType",
    "Triplet",
]
