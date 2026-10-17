"""Tests — Motor de Coherencia (Fase 2, SPEC-02 §1 · SPEC-01 §3.2).

Autorizado por: **SPEC-02 §1 Fase 2** (§6–§8) y la **Constitución, Reglas 7–9**.

Verifica:
- ``CORROBORATES`` incrementa ``coherence_score`` y enlaza evidencia;
- ``CONTRADICTS`` crea arista + ``CoherenceAlert`` y **nunca** toca
  ``conclusion_body`` (jamás se llama a ``update_analysis``);
- ``EXTENDS`` enlaza y produce cita (``CITES_ANALYSIS``);
- ``ANSWERS_REQUIREMENT`` enlaza y marca el RFI como ``ANSWERED``;
- severidad derivada de la confianza (dominio ``LOW|MEDIUM|CRITICAL``).
"""

from __future__ import annotations

import pytest

from app.services.coherence_engine import (
    CoherenceEngine,
    derive_severity,
)
from app.services.entity_resolver import EntityResolver
from app.services.nlp_extractor import FakeLLMClient, NLPExtractor
from tests.ingestion.fakes import FakeGraphRepository

REPORT_ID = "88888888-8888-4888-8888-888888888888"
CONCLUSION = "Tesis original del analista (no debe cambiar)."


def make_repo() -> FakeGraphRepository:
    repo = FakeGraphRepository()
    repo.seed_analysis(
        analysis_id="an-1",
        code="AN-ACME",
        thesis_summary="Acme financia criptografía.",
        conclusion_body=CONCLUSION,
    )
    repo.seed_requirement(requirement_id="req-1")
    return repo


def link(category: str, *, confidence: float = 0.8, target: str = "an-1") -> dict:
    return {
        "target_id": target,
        "link_category": category,
        "confidence": confidence,
        "coherence_justification": "Coincide con la evidencia del informe.",
        "exact_quote": "Acme Corp financió la compra de equipos.",
    }


# ---------------------------------------------------------------------
# Severidad derivada
# ---------------------------------------------------------------------
def test_derive_severity_thresholds():
    assert derive_severity(0.9) == "CRITICAL"
    assert derive_severity(0.85) == "CRITICAL"
    assert derive_severity(0.7) == "MEDIUM"
    assert derive_severity(0.6) == "MEDIUM"
    assert derive_severity(0.59) == "LOW"


# ---------------------------------------------------------------------
# Las 4 categorías del contrato
# ---------------------------------------------------------------------
def test_corroborates_increments_coherence_score():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(REPORT_ID, [link("CORROBORATES", confidence=0.7)])
    assert len(outcome.corroborations) == 1
    assert repo.analyses["an-1"]["coherence_score"] == pytest.approx(0.7)


def test_contradicts_creates_alert_and_never_touches_conclusion():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(
        REPORT_ID, [link("CONTRADICTS", confidence=0.9)]
    )
    assert len(outcome.contradictions) == 1
    assert len(outcome.alerts) == 1
    assert outcome.alerts[0]["alert_type"] == "CONTRADICTION"
    assert repo.contradictions[0]["severity"] == "CRITICAL"
    # Regla 7/9: la tesis humana queda intacta y no se invocó la edición humana.
    assert repo.analyses["an-1"]["conclusion_body"] == CONCLUSION
    assert repo.update_analysis_calls == []


def test_contradicts_medium_and_low_severity():
    repo = make_repo()
    engine = CoherenceEngine(repo)
    engine.apply(REPORT_ID, [link("CONTRADICTS", confidence=0.65)])
    engine.apply(REPORT_ID, [link("CONTRADICTS", confidence=0.2)])
    assert [c["severity"] for c in repo.contradictions] == ["MEDIUM", "LOW"]


def test_extends_links_and_cites_analysis():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(REPORT_ID, [link("EXTENDS", confidence=0.8)])
    assert len(outcome.extensions) == 1
    assert len(outcome.citations) == 1
    assert repo.citations[0]["relevance_score"] == pytest.approx(0.8)


def test_answers_requirement_marks_it_answered():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(
        REPORT_ID, [link("ANSWERS_REQUIREMENT", target="req-1")]
    )
    assert len(outcome.answers) == 1
    assert repo.requirements["req-1"]["status"] == "ANSWERED"


# ---------------------------------------------------------------------
# Trazabilidad doble de las aristas analíticas (SPEC-01 §3.2 v2.0)
# ---------------------------------------------------------------------
QUOTE = "Acme Corp financió la compra de equipos."


def triplet(quote: str = QUOTE, *, page_number: int = 3) -> dict:
    """Tripleta con procedencia (``exact_quote`` + ``page_number``)."""
    return {
        "subject": {"label": "Organization", "name": "Acme Corp", "attributes": {}},
        "predicate": {"type": "FINANCED", "confidence": 0.9},
        "object": {"label": "Person", "name": "Juan Pérez", "attributes": {}},
        "provenance": {
            "page_number": page_number,
            "char_start": 0,
            "char_end": len(quote),
            "exact_quote": quote,
        },
    }


def test_extends_persists_quote_and_derived_page_number():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(
        REPORT_ID, [link("EXTENDS")], triplets=[triplet(page_number=4)]
    )
    extension = outcome.extensions[0]
    assert extension["exact_quote"] == QUOTE
    assert extension["text_evidence_span"] == QUOTE
    assert extension["page_number"] == 4
    # La cita derivada de EXTENDS también lleva procedencia.
    assert repo.citations[0]["exact_quote"] == QUOTE
    assert repo.citations[0]["page_number"] == 4


def test_answers_requirement_persists_quote_and_derived_page_number():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(
        REPORT_ID,
        [link("ANSWERS_REQUIREMENT", target="req-1")],
        triplets=[triplet(page_number=2)],
    )
    assert outcome.answers[0]["exact_quote"] == QUOTE
    assert outcome.answers[0]["text_evidence_span"] == QUOTE
    assert outcome.answers[0]["page_number"] == 2


def test_cites_analysis_persists_quote_and_derived_page_number():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(
        REPORT_ID, [link("CITES_ANALYSIS")], triplets=[triplet(page_number=6)]
    )
    assert outcome.citations[0]["exact_quote"] == QUOTE
    assert outcome.citations[0]["page_number"] == 6


def test_corroborates_and_contradicts_persist_quote_and_page_number():
    repo = make_repo()
    engine = CoherenceEngine(repo)
    engine.apply(REPORT_ID, [link("CORROBORATES")], triplets=[triplet(page_number=1)])
    engine.apply(REPORT_ID, [link("CONTRADICTS")], triplets=[triplet(page_number=5)])
    assert repo.corroborations[0]["exact_quote"] == QUOTE
    assert repo.corroborations[0]["page_number"] == 1
    assert repo.contradictions[0]["exact_quote"] == QUOTE
    assert repo.contradictions[0]["page_number"] == 5


def test_page_number_is_none_without_matching_triplet():
    repo = make_repo()
    outcome = CoherenceEngine(repo).apply(
        REPORT_ID,
        [link("EXTENDS")],
        triplets=[triplet("Otra cita distinta.", page_number=9)],
    )
    # La cita del vínculo se conserva; la página no se inventa.
    assert outcome.extensions[0]["exact_quote"] == QUOTE
    assert outcome.extensions[0]["page_number"] is None


def test_apply_link_derives_page_number_from_triplets():
    repo = make_repo()
    CoherenceEngine(repo).apply_link(
        REPORT_ID, link("ANSWERS_REQUIREMENT", target="req-1"),
        triplets=[triplet(page_number=8)],
    )
    assert repo.answers[0]["page_number"] == 8


def test_unknown_category_is_rejected():
    repo = make_repo()
    with pytest.raises(ValueError):
        CoherenceEngine(repo).apply(REPORT_ID, [link("IGNORES")])


# ---------------------------------------------------------------------
# Integración en el pipeline (orden del pipeline: coherencia ANTES de persistir)
# ---------------------------------------------------------------------
def test_pipeline_applies_links_before_persisting():
    repo = make_repo()

    def llm(chunk):  # noqa: ANN001
        return {
            "extracted_triplets": [
                {
                    "subject": {
                        "label": "Organization",
                        "name": "Acme Corp",
                        "attributes": {"registration_id": "REG-1"},
                    },
                    "predicate": {"type": "FINANCED", "confidence": 0.9},
                    "object": {"label": "Person", "name": "Juan Pérez", "attributes": {}},
                }
            ],
            "analytical_context_links": [link("CONTRADICTS", confidence=0.9)],
        }

    extractor = NLPExtractor(
        FakeLLMClient(llm), EntityResolver(repo), repository=repo
    )
    outcome = extractor.ingest(REPORT_ID, ["Acme Corp financió a Juan Pérez."])

    # La tripleta aceptada impactó el grafo y el vínculo se aplicó.
    assert len(outcome.accepted) == 1
    assert len(repo.edges) == 1
    assert outcome.coherence is not None
    assert len(repo.contradictions) == 1
    assert repo.update_analysis_calls == []
    assert repo.analyses["an-1"]["conclusion_body"] == CONCLUSION
    # El vínculo conserva su cita aunque no coincida con ninguna tripleta.
    assert repo.contradictions[0]["exact_quote"] == (
        "Acme Corp financió la compra de equipos."
    )
    assert repo.contradictions[0]["page_number"] is None


def test_pipeline_derives_page_number_when_quote_matches_triplet():
    repo = make_repo()
    text = "Acme Corp financió la compra de equipos."

    def llm(chunk):  # noqa: ANN001
        return {
            "extracted_triplets": [
                {
                    "subject": {
                        "label": "Organization",
                        "name": "Acme Corp",
                        "attributes": {"registration_id": "REG-1"},
                    },
                    "predicate": {"type": "FINANCED", "confidence": 0.9},
                    "object": {"label": "Person", "name": "Juan Pérez", "attributes": {}},
                    "provenance": {
                        "page_number": 1,
                        "char_start": 0,
                        "char_end": len(text),
                        "exact_quote": text,
                    },
                }
            ],
            "analytical_context_links": [
                {
                    "target_id": "an-1",
                    "link_category": "EXTENDS",
                    "confidence": 0.8,
                    "coherence_justification": "Nuevo ángulo.",
                    "exact_quote": text,
                }
            ],
        }

    extractor = NLPExtractor(
        FakeLLMClient(llm), EntityResolver(repo), repository=repo
    )
    outcome = extractor.ingest(REPORT_ID, [text])

    assert outcome.coherence is not None
    extension = outcome.coherence.extensions[0]
    assert extension["exact_quote"] == text
    assert extension["text_evidence_span"] == text
    assert extension["page_number"] == 1
