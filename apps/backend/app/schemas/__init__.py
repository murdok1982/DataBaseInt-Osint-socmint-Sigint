"""Paquete de esquemas Pydantic (contratos internos del backend).

Zona: Agente 2 — ``apps/backend/app/schemas/**``.
"""

from app.schemas.extraction import (
    EXTRACTABLE_LABELS,
    PREDICATE_STATUSES,
    PREDICATE_TYPES,
    Entity,
    EntityLabel,
    ExtractionResult,
    Predicate,
    PredicateStatus,
    PredicateType,
    Provenance,
    Triplet,
)

__all__ = [
    "Entity",
    "EntityLabel",
    "ExtractionResult",
    "Predicate",
    "PredicateStatus",
    "PredicateType",
    "Provenance",
    "Triplet",
    "EXTRACTABLE_LABELS",
    "PREDICATE_TYPES",
    "PREDICATE_STATUSES",
]
