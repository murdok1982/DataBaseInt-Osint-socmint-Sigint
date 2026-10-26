"""Entity Resolution v3.1 — selectores, alias e historial (SPEC-02 §10).

Zona: Agente 2 — ``apps/backend/app/services/**``.

Diferencias con el resolver v2
-------------------------------
El resolver v2 comparaba **nombres** y podía fusionar con un score. El modelo
v3.1 prohíbe esa inferencia: un nombre visible nunca identifica por sí solo a
una persona. La identidad se ancla a un **selector** (teléfono, cuenta, IMSI,
wallet, etc.) normalizado y con hash. Los nombres y alias sólo generan
candidatos que requieren revisión humana.

Estados de salida (mapean a ``hermes-tool-result.json``):

``MATCH_EXACT``
    Un único selector activo en la ventana ``as_of``.
``MATCH_ALIAS``
    Un único alias histórico activo; es un candidato fuerte, no una fusión.
``MERGE_CANDIDATE``
    Score >= 0.88; se propone ``ALIAS_OF``/fusión, nunca se aplica solo.
``NEEDS_REVIEW``
    Score 0.70–0.88 o candidato bloqueado por analista.
``NEW_STAGED``
    Sin coincidencia suficiente; se propone una entidad nueva.
``AMBIGUOUS``
    Más de un selector/alias activo o empate de candidatos.

El módulo es puro salvo por el repositorio inyectado (duck typing), de modo que
puede probarse sin PostgreSQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Mapping, Optional, Protocol, Sequence

from app.services.entity_resolver import normalize_name, similarity_score
from app.services.selector_normalizer import selector_fingerprint


class ResolutionStatus(str, Enum):
    MATCH_EXACT = "MATCH_EXACT"
    MATCH_ALIAS = "MATCH_ALIAS"
    MERGE_CANDIDATE = "MERGE_CANDIDATE"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    NEW_STAGED = "NEW_STAGED"
    AMBIGUOUS = "AMBIGUOUS"


#: Umbrales de SPEC-02 §10.1.
MERGE_THRESHOLD = 0.88
REVIEW_THRESHOLD = 0.70

#: Pesos de la fórmula cuando no hay selector exacto.
WEIGHT_ALIAS = 0.45
WEIGHT_TRIGRAM = 0.25
WEIGHT_EMBEDDING = 0.20
WEIGHT_CONTEXT = 0.10

#: Un ``DISPLAY_NAME`` nunca es selector automático (SPEC-02 §10.2).
NON_IDENTIFYING_TYPES = frozenset({"DISPLAY_NAME", "OTHER"})


@dataclass(frozen=True)
class Candidate:
    """Candidato de resolución con sus componentes de score."""

    entity_id: str
    canonical_name: str = ""
    alias_exact: float = 0.0
    trigram: float = 0.0
    embedding: float = 0.0
    context: float = 0.0
    reason: str = "TRIGRAM"
    locked_by_analyst: bool = False

    @property
    def score(self) -> float:
        """Score ponderado de SPEC-02 §10.1, acotado a ``[0, 1]``."""
        value = (
            WEIGHT_ALIAS * self.alias_exact
            + WEIGHT_TRIGRAM * self.trigram
            + WEIGHT_EMBEDDING * self.embedding
            + WEIGHT_CONTEXT * self.context
        )
        return max(0.0, min(1.0, value))


@dataclass(frozen=True)
class ResolutionResult:
    """Resultado explicable de una resolución."""

    status: ResolutionStatus
    entity_id: Optional[str] = None
    score: float = 0.0
    candidates: tuple[Candidate, ...] = field(default_factory=tuple)
    reason: str = ""
    warnings: tuple[str, ...] = field(default_factory=tuple)

    @property
    def requires_human_review(self) -> bool:
        return self.status in {
            ResolutionStatus.MERGE_CANDIDATE,
            ResolutionStatus.NEEDS_REVIEW,
            ResolutionStatus.AMBIGUOUS,
        }

    @property
    def creates_proposal(self) -> bool:
        return self.status in {
            ResolutionStatus.MERGE_CANDIDATE,
            ResolutionStatus.NEW_STAGED,
        }


class ResolutionRepository(Protocol):
    """Subconjunto de lectura que el resolver necesita del repositorio."""

    def find_selector_matches(
        self, observable_type: str, value_hash: str, as_of: str
    ) -> Sequence[Mapping[str, Any]]: ...

    def find_alias_matches(
        self, normalized_value: str, as_of: str
    ) -> Sequence[Mapping[str, Any]]: ...

    def search_candidates(
        self, query: str, *, limit: int = 20
    ) -> Sequence[Mapping[str, Any]]: ...


def weighted_score(
    *,
    alias_exact: float = 0.0,
    trigram: float = 0.0,
    embedding: float = 0.0,
    context: float = 0.0,
) -> float:
    """Score de la fórmula de SPEC-02 §10.1 (sin selector exacto)."""
    value = (
        WEIGHT_ALIAS * max(0.0, min(1.0, alias_exact))
        + WEIGHT_TRIGRAM * max(0.0, min(1.0, trigram))
        + WEIGHT_EMBEDDING * max(0.0, min(1.0, embedding))
        + WEIGHT_CONTEXT * max(0.0, min(1.0, context))
    )
    return max(0.0, min(1.0, value))


def candidate_from_mapping(row: Mapping[str, Any]) -> Candidate:
    """Construye un :class:`Candidate` desde una fila de PostgreSQL/JSON."""
    return Candidate(
        entity_id=str(row.get("entity_id") or row.get("id") or ""),
        canonical_name=str(row.get("canonical_name") or row.get("name") or ""),
        alias_exact=float(row.get("alias_exact") or row.get("alias_score") or 0.0),
        trigram=float(row.get("trigram") or row.get("trigram_score") or 0.0),
        embedding=float(row.get("embedding") or row.get("embedding_score") or 0.0),
        context=float(row.get("context") or row.get("context_score") or 0.0),
        reason=str(row.get("reason") or "TRIGRAM"),
        locked_by_analyst=bool(row.get("locked_by_analyst") or False),
    )


def rank_candidates(candidates: Iterable[Candidate]) -> tuple[Candidate, ...]:
    """Ordena por score descendente y desempata por ``entity_id``."""
    return tuple(sorted(candidates, key=lambda c: (-c.score, c.entity_id)))


def decide_from_candidates(
    candidates: Iterable[Candidate],
    *,
    selector_matches: int = 0,
    alias_matches: int = 0,
    exact_entity_id: Optional[str] = None,
) -> ResolutionResult:
    """Decide el estado sin tocar la base de datos.

    ``selector_matches`` y ``alias_matches`` son conteos de coincidencias
    activas; >1 se considera ambiguo. Los candidatos ya vienen puntuados.
    """
    if selector_matches > 1:
        return ResolutionResult(
            ResolutionStatus.AMBIGUOUS,
            reason="Más de un selector activo en la ventana temporal.",
            warnings=("SELECTOR_OVERLAP",),
        )
    if selector_matches == 1:
        return ResolutionResult(
            ResolutionStatus.MATCH_EXACT,
            entity_id=exact_entity_id,
            score=1.0,
            reason="Selector exacto activo.",
        )
    if alias_matches > 1:
        return ResolutionResult(
            ResolutionStatus.AMBIGUOUS,
            reason="Más de un alias activo en la ventana temporal.",
            warnings=("ALIAS_COLLISION",),
        )
    if alias_matches == 1:
        # Un alias exacto es evidencia de continuidad, pero no prueba identidad
        # por sí solo: se devuelve como candidato para revisión/propuesta.
        top = rank_candidates(candidates)
        return ResolutionResult(
            ResolutionStatus.MATCH_ALIAS,
            entity_id=top[0].entity_id if top else None,
            score=top[0].score if top else 1.0,
            candidates=top,
            reason="Alias histórico activo; requiere corroboración.",
            warnings=("ALIAS_ONLY",),
        )

    ranked = rank_candidates(candidates)
    if not ranked:
        return ResolutionResult(
            ResolutionStatus.NEW_STAGED,
            reason="Sin selectores, alias ni candidatos suficientes.",
        )

    top = ranked[0]
    if len(ranked) > 1 and abs(ranked[0].score - ranked[1].score) < 1e-9:
        return ResolutionResult(
            ResolutionStatus.AMBIGUOUS,
            candidates=ranked,
            score=top.score,
            reason="Empate de candidatos; no se elige automáticamente.",
            warnings=("CANDIDATE_TIE",),
        )
    if top.locked_by_analyst:
        return ResolutionResult(
            ResolutionStatus.NEEDS_REVIEW,
            entity_id=top.entity_id,
            score=top.score,
            candidates=ranked,
            reason="Candidato bloqueado por analista; no se auto-fusiona.",
        )
    if top.score >= MERGE_THRESHOLD:
        return ResolutionResult(
            ResolutionStatus.MERGE_CANDIDATE,
            entity_id=top.entity_id,
            score=top.score,
            candidates=ranked,
            reason="Score >= 0.88; se propone ALIAS_OF/fusión.",
        )
    if top.score >= REVIEW_THRESHOLD:
        return ResolutionResult(
            ResolutionStatus.NEEDS_REVIEW,
            entity_id=top.entity_id,
            score=top.score,
            candidates=ranked,
            reason="Score 0.70–0.88; candidato para revisión humana.",
        )
    return ResolutionResult(
        ResolutionStatus.NEW_STAGED,
        candidates=ranked,
        score=top.score,
        reason="Score < 0.70; se propone entidad nueva.",
    )


class EntityResolverV3:
    """Resolver determinista que consulta un repositorio inyectado."""

    def __init__(self, repository: ResolutionRepository) -> None:
        self.repository = repository

    def resolve(
        self,
        *,
        observable_type: str,
        value: str,
        as_of: str,
        display_name: Optional[str] = None,
    ) -> ResolutionResult:
        """Resuelve un selector y devuelve una decisión explicable."""
        normalized, value_hash = selector_fingerprint(observable_type, value)
        matches = list(
            self.repository.find_selector_matches(
                observable_type.upper(), value_hash, as_of
            )
        )
        exact_id = str(matches[0].get("entity_id")) if len(matches) == 1 else None

        alias_matches: list[Mapping[str, Any]] = []
        if observable_type.upper() not in NON_IDENTIFYING_TYPES:
            alias_matches = list(
                self.repository.find_alias_matches(normalized, as_of)
            )

        query = normalize_name(display_name or normalized)
        candidates: list[Candidate] = []
        if not matches:
            for row in self.repository.search_candidates(query, limit=20):
                candidate = candidate_from_mapping(row)
                # Rellena trigram si el repositorio sólo devolvió ``score``.
                if candidate.trigram == 0.0 and row.get("score") is not None:
                    candidate = Candidate(
                        entity_id=candidate.entity_id,
                        canonical_name=candidate.canonical_name,
                        alias_exact=candidate.alias_exact,
                        trigram=float(row.get("score") or 0.0),
                        embedding=candidate.embedding,
                        context=candidate.context,
                        reason=candidate.reason,
                        locked_by_analyst=candidate.locked_by_analyst,
                    )
                if candidate.entity_id:
                    candidates.append(candidate)

        result = decide_from_candidates(
            candidates,
            selector_matches=len(matches),
            alias_matches=len(alias_matches),
            exact_entity_id=exact_id,
        )
        if alias_matches and result.status in {
            ResolutionStatus.MATCH_ALIAS,
            ResolutionStatus.NEEDS_REVIEW,
            ResolutionStatus.MERGE_CANDIDATE,
        }:
            # Preserve the alias candidate even when the repo returned no
            # separate fuzzy rows.
            alias_ids = {
                str(row.get("entity_id")) for row in alias_matches if row.get("entity_id")
            }
            if result.entity_id is None and len(alias_ids) == 1:
                result = ResolutionResult(
                    ResolutionStatus.MATCH_ALIAS,
                    entity_id=next(iter(alias_ids)),
                    score=1.0,
                    candidates=result.candidates,
                    reason="Alias histórico activo; requiere corroboración.",
                    warnings=result.warnings,
                )
        return result


def score_name_candidate(query: str, candidate_name: str) -> float:
    """Utilidad de trigramas para tests y para el repositorio en memoria."""
    return similarity_score(query, candidate_name)


__all__ = [
    "Candidate",
    "EntityResolverV3",
    "MERGE_THRESHOLD",
    "NON_IDENTIFYING_TYPES",
    "REVIEW_THRESHOLD",
    "ResolutionRepository",
    "ResolutionResult",
    "ResolutionStatus",
    "WEIGHT_ALIAS",
    "WEIGHT_CONTEXT",
    "WEIGHT_EMBEDDING",
    "WEIGHT_TRIGRAM",
    "candidate_from_mapping",
    "decide_from_candidates",
    "rank_candidates",
    "score_name_candidate",
    "weighted_score",
]
