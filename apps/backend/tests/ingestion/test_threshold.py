"""Tests — umbral de confianza 0.50 (SPEC-02 §2 · §3 · §6).

Autorizado por: SPEC-02 §3.
- ``confidence < 0.50`` → ``PENDING_HUMAN_REVIEW`` y el grafo NO recibe escrituras.
- ``confidence >= 0.50`` → se resuelve (Entity Resolution, SPEC-01 §4) e impacta.
- ``confidence`` omitida → ``DEFAULT_CONFIDENCE = 0.50``; fuera de rango → se
  **rechaza** (nunca se corrige), SPEC-02 §2.
- El ``status`` de la tripleta es **derivado** por el pipeline (no lo emite el LLM).
"""

from __future__ import annotations

import uuid

import pytest

from app.services.entity_resolver import EntityResolver
from app.services.nlp_extractor import (
    AUTO_ACCEPTED,
    DEFAULT_CONFIDENCE,
    FakeLLMClient,
    NLPExtractor,
    PENDING_HUMAN_REVIEW,
    SchemaValidationError,
    normalize_relation_label,
)
from tests.conftest import FakeRepository

PAGE = (
    "El informe señala que Juan Pérez, con domicilio en Madrid, financió a "
    "Acme Corp para adquirir equipos de cifrado."
)

REPORT_ID = "22222222-2222-4222-8222-222222222222"


def make_triplet(confidence: float | None = 0.9) -> dict:
    predicate: dict = {"type": "FINANCED"}
    if confidence is not None:
        predicate["confidence"] = confidence
    return {
        "subject": {"label": "Person", "name": "Juan Pérez", "attributes": {}},
        "predicate": predicate,
        "object": {
            "label": "Organization",
            "name": "Acme Corp",
            "attributes": {"registration_id": "REG-1"},
        },
    }


def build(repo: FakeRepository, confidence: float | None) -> NLPExtractor:
    resolver = EntityResolver(repo)
    llm = FakeLLMClient(
        lambda chunk: {"extracted_triplets": [make_triplet(confidence)]}
    )
    return NLPExtractor(llm, resolver, repository=repo)


# ---------------------------------------------------------------------
# Bajo el umbral: no toca el grafo
# ---------------------------------------------------------------------
def test_below_threshold_goes_to_pending_and_does_not_touch_graph(
    repo: FakeRepository,
):
    extractor = build(repo, 0.49)
    outcome = extractor.ingest(REPORT_ID, [PAGE])

    assert len(outcome.pending) == 1
    assert outcome.accepted == []
    assert outcome.status_of(outcome.pending[0]) == PENDING_HUMAN_REVIEW
    # El grafo NO recibe ninguna escritura (ni nodos ni aristas).
    assert repo.nodes == {}
    assert repo.edges == []
    assert outcome.written is False


# ---------------------------------------------------------------------
# En/por encima del umbral: resuelve e impacta
# ---------------------------------------------------------------------
def test_at_threshold_is_accepted_and_impacts_graph(repo: FakeRepository):
    extractor = build(repo, 0.50)
    outcome = extractor.ingest(REPORT_ID, [PAGE])

    assert len(outcome.accepted) == 1
    assert outcome.pending == []
    assert outcome.status_of(outcome.accepted[0]) == AUTO_ACCEPTED
    # Entity Resolution (SPEC-01 §4) creó/actualizó nodos y una arista.
    assert len(repo.nodes) == 2
    assert len(repo.edges) == 1
    assert repo.edges[0]["rel_type"] == "FINANCED"
    assert outcome.written is True


def test_above_threshold_also_impacts_graph(repo: FakeRepository):
    extractor = build(repo, 0.95)
    outcome = extractor.ingest(REPORT_ID, [PAGE])
    assert len(outcome.accepted) == 1
    assert len(repo.edges) == 1


def test_omitted_confidence_uses_contract_default(repo: FakeRepository):
    """``confidence`` omitida → ``DEFAULT_CONFIDENCE`` (0.50) → aceptada."""
    assert DEFAULT_CONFIDENCE == 0.50
    extractor = build(repo, None)
    outcome = extractor.ingest(REPORT_ID, [PAGE])
    assert outcome.accepted[0].predicate.confidence == DEFAULT_CONFIDENCE
    assert len(repo.edges) == 1


def test_out_of_range_confidence_is_rejected_not_corrected(repo: FakeRepository):
    """``confidence`` fuera de rango se rechaza (SPEC-02 §2), no se corrige."""
    extractor = build(repo, 1.5)
    with pytest.raises(SchemaValidationError):
        extractor.ingest(REPORT_ID, [PAGE])
    assert repo.edges == []


# ---------------------------------------------------------------------
# Trazabilidad: evidencia verificable (página y span)
# ---------------------------------------------------------------------
def test_evidence_quote_is_verifiable_slice_of_page(repo: FakeRepository):
    extractor = build(repo, 0.9)
    outcome = extractor.ingest(REPORT_ID, [PAGE])
    provenance = outcome.accepted[0].provenance
    assert provenance.page_number == 1
    assert provenance.exact_quote == PAGE[
        provenance.char_start : provenance.char_end
    ]


def test_locates_quote_span_inside_chunk(repo: FakeRepository):
    quote = "financió a Acme Corp"

    def llm(chunk):  # noqa: ANN001
        return {
            "extracted_triplets": [
                {
                    **make_triplet(0.9),
                    "provenance": {"page_number": 1, "exact_quote": quote},
                }
            ]
        }

    extractor = NLPExtractor(FakeLLMClient(llm), EntityResolver(repo), repository=repo)
    outcome = extractor.ingest(str(uuid.uuid4()), [PAGE])
    provenance = outcome.accepted[0].provenance
    assert provenance.exact_quote == quote
    assert PAGE[provenance.char_start : provenance.char_end] == quote


# ---------------------------------------------------------------------
# Normalización ES → tipo Cypher canónico (SPEC-01 §3.1)
# ---------------------------------------------------------------------
def test_relation_label_normalization_map():
    assert normalize_relation_label("UBICADO_EN") == "LOCATED_AT"
    assert normalize_relation_label("FINANCIÓ_A") == "FINANCED"
    assert normalize_relation_label("participó_en") == "PARTICIPATED_IN"
    assert normalize_relation_label("ASOCIADO_CON") == "ASSOCIATED_WITH"
    assert normalize_relation_label("CONTROLADO_POR") == "CONTROLS"
    assert normalize_relation_label("USA_ACTIVO") == "USES_ASSET"
    # Idempotente sobre el tipo ya canónico.
    assert normalize_relation_label("LOCATED_AT") == "LOCATED_AT"


def test_unknown_relation_label_is_rejected():
    with pytest.raises(ValueError):
        normalize_relation_label("ENEMY_OF")


def test_spanish_relation_label_end_to_end(repo: FakeRepository):
    def spanish(chunk):  # noqa: ANN001
        return {
            "extracted_triplets": [
                {
                    "subject": {"label": "Person", "name": "Juan Pérez", "attributes": {}},
                    "predicate": {"type": "UBICADO_EN", "confidence": 0.8},
                    "object": {"label": "Location", "name": "Madrid", "attributes": {}},
                }
            ]
        }

    extractor = NLPExtractor(FakeLLMClient(spanish), EntityResolver(repo), repository=repo)
    outcome = extractor.ingest(str(uuid.uuid4()), [PAGE])
    assert outcome.accepted[0].predicate.type.value == "LOCATED_AT"
    assert repo.edges[0]["rel_type"] == "LOCATED_AT"
