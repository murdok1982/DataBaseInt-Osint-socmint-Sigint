"""Tests — idempotencia por `report_id` (SPEC-02 §5 · §6).

Autorizado por: SPEC-02 §5/§6 ("La reingesta del mismo `report_id` es idempotente")
y §2 ("`report_id` es asignado por el servidor"). El contrato v2.0 no lo incluye;
el backend lo inyecta en cada arista persistida.
"""

from __future__ import annotations

import uuid

from app.services.entity_resolver import EntityResolver
from app.services.nlp_extractor import (
    FakeLLMClient,
    InMemoryIngestRegistry,
    NLPExtractor,
)
from tests.conftest import FakeRepository

PAGE = "Acme Corp financió a Juan Pérez para adquirir el dominio evil.example.com."
REPORT_ID = "33333333-3333-4333-8333-333333333333"


def triplet() -> dict:
    return {
        "subject": {
            "label": "Organization",
            "name": "Acme Corp",
            "attributes": {"registration_id": "REG-1"},
        },
        "predicate": {"type": "FINANCED", "confidence": 0.9},
        "object": {"label": "Person", "name": "Juan Pérez", "attributes": {}},
    }


def build(repo: FakeRepository, registry: InMemoryIngestRegistry) -> NLPExtractor:
    llm = FakeLLMClient(lambda chunk: {"extracted_triplets": [triplet()]})
    return NLPExtractor(llm, EntityResolver(repo), repository=repo, registry=registry)


def test_reingest_same_report_id_does_not_duplicate(repo: FakeRepository):
    registry = InMemoryIngestRegistry()
    extractor = build(repo, registry)

    first = extractor.ingest(REPORT_ID, [PAGE])
    nodes_after_first = len(repo.nodes)
    edges_after_first = len(repo.edges)
    assert nodes_after_first == 2
    assert edges_after_first == 1
    assert first.idempotent is False

    # Reingesta del MISMO report_id.
    second = extractor.ingest(REPORT_ID, [PAGE])
    assert second.idempotent is True
    assert len(repo.nodes) == nodes_after_first
    assert len(repo.edges) == edges_after_first
    assert len(registry) == 1


def test_reingest_does_not_call_llm_again(repo: FakeRepository):
    registry = InMemoryIngestRegistry()
    extractor = build(repo, registry)
    extractor.ingest(REPORT_ID, [PAGE])
    calls_after_first = len(extractor.llm.calls)  # type: ignore[attr-defined]

    extractor.ingest(REPORT_ID, [PAGE])
    assert len(extractor.llm.calls) == calls_after_first  # type: ignore[attr-defined]


def test_different_report_ids_are_independent(repo: FakeRepository):
    registry = InMemoryIngestRegistry()
    extractor = build(repo, registry)
    extractor.ingest(str(uuid.uuid4()), [PAGE])
    extractor.ingest(str(uuid.uuid4()), [PAGE])
    assert len(registry) == 2
    # Mismas entidades (match exacto por clave única) → sin duplicar nodos.
    assert len(repo.nodes) == 2


def test_reprocess_flag_forces_reexecution_without_duplicates(repo: FakeRepository):
    registry = InMemoryIngestRegistry()
    extractor = build(repo, registry)
    extractor.ingest(REPORT_ID, [PAGE])
    edges_after_first = len(repo.edges)

    outcome = extractor.ingest(REPORT_ID, [PAGE], reprocess=True)
    assert outcome.idempotent is False
    assert len(repo.edges) == edges_after_first  # el MERGE no duplica
    assert len(repo.nodes) == 2


def test_server_assigns_report_id_on_persisted_edges(repo: FakeRepository):
    """El ``report_id`` del servidor se inyecta en la arista persistida (SPEC-02 §2)."""
    registry = InMemoryIngestRegistry()
    extractor = build(repo, registry)
    extractor.ingest(REPORT_ID, [PAGE])

    assert len(repo.edges) == 1
    props = repo.edges[0]["props"]
    assert props["source_report_id"] == REPORT_ID
    # Toda arista OSINT lleva la procedencia obligatoria (Constitución, Regla 4).
    for key in (
        "confidence",
        "exact_quote",
        "text_evidence_span",
        "page_number",
        "first_seen",
        "last_seen",
        "locked_by_analyst",
    ):
        assert key in props
