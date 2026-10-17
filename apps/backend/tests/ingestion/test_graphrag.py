"""Tests — GraphRAG con citas obligatorias (SPEC-03 §5 · §6).

Autorizado por: SPEC-03 §5 (respuesta con `citations`) y §6
("GraphRAG nunca devuelve respuesta sin `citations`").
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.services.graphrag_engine import (
    NO_EVIDENCE_ANSWER,
    Citation,
    FakeRagEngine,
    GraphRagEngine,
    InMemoryGraphRepository,
    NoCitationsError,
    RagAnswer,
    Subgraph,
)

REPORT_ID = "44444444-4444-4444-8444-444444444444"


def citation() -> dict:
    return {
        "report_id": REPORT_ID,
        "page_number": 3,
        "exact_quote": "Acme Corp financió la compra de equipos de cifrado",
    }


# ---------------------------------------------------------------------
# MODELO: respuesta sin citas es rechazada (SPEC-03 §6)
# ---------------------------------------------------------------------
def test_answer_without_citations_is_rejected():
    with pytest.raises(ValidationError):
        RagAnswer(answer="Algo", subgraph=Subgraph(nodes=[], edges=[]), citations=[])


def test_answer_with_citations_is_valid():
    answer = RagAnswer(
        answer="Acme Corp financió equipos.",
        subgraph=Subgraph(nodes=[{"id": "o1"}], edges=[]),
        citations=[Citation(**citation())],
    )
    as_dict = answer.as_dict()
    assert as_dict["citations"][0]["report_id"] == REPORT_ID
    assert as_dict["citations"][0]["page_number"] == 3


# ---------------------------------------------------------------------
# MOTOR sobre repositorio en memoria
# ---------------------------------------------------------------------
def build_repo(with_provenance: bool = True) -> InMemoryGraphRepository:
    props = {"confidence": 0.9}
    if with_provenance:
        props.update(
            {
                "source_report_id": REPORT_ID,
                "evidence_quote": citation()["exact_quote"],
                "page_number": 3,
            }
        )
    repo = InMemoryGraphRepository()
    repo.add_node({"id": "o1", "label": "Organization", "name": "Acme Corp"})
    repo.add_node({"id": "p1", "label": "Person", "name": "Juan Pérez"})
    repo.add_edge("o1", "p1", "FINANCED", props)
    return repo


def test_engine_returns_answer_with_citations():
    engine = GraphRagEngine(build_repo(True))
    result = engine.answer("Acme Corp")
    assert isinstance(result, RagAnswer)
    assert result.citations
    assert result.citations[0].report_id == REPORT_ID
    assert result.citations[0].page_number == 3
    assert result.citations[0].exact_quote == citation()["exact_quote"]
    assert {node["id"] for node in result.subgraph.nodes} >= {"o1", "p1"}


def test_engine_rejects_answer_without_citable_evidence():
    engine = GraphRagEngine(build_repo(False))
    with pytest.raises(NoCitationsError):
        engine.answer("Acme Corp")


def test_engine_raises_when_query_matches_nothing():
    engine = GraphRagEngine(build_repo(True))
    with pytest.raises(NoCitationsError):
        engine.answer("entidad inexistente")


def test_engine_with_llm_uses_synthesis():
    class FakeLLM:
        def generate(self, prompt: str, *, context: str = "") -> str:
            assert context  # recibe el contexto del subgrafo
            return f"Respuesta sintetizada sobre: {prompt}"

    engine = GraphRagEngine(build_repo(True), llm=FakeLLM())
    result = engine.answer("Acme Corp")
    assert result.answer.startswith("Respuesta sintetizada")


# ---------------------------------------------------------------------
# Motor fake (interfaz RagEngine)
# ---------------------------------------------------------------------
def test_fake_engine_with_citations_is_valid():
    engine = FakeRagEngine(citations=[citation()])
    result = engine.answer("¿Quién financió Acme?")
    assert result.citations[0].exact_quote == citation()["exact_quote"]


def test_fake_engine_without_citations_is_rejected():
    engine = FakeRagEngine(citations=[])
    with pytest.raises(NoCitationsError):
        engine.answer("¿Quién financió Acme?")


# ---------------------------------------------------------------------
# Citas de análisis humanos (SPEC-03 §5) y ausencia de evidencia (SPEC-03 §8)
# ---------------------------------------------------------------------
def test_citation_requires_an_origin():
    with pytest.raises(ValidationError):
        Citation(exact_quote="texto sin origen")
    # Un origen de análisis es válido sin report_id.
    analysis_citation = Citation(
        analysis_id="an-1", code="AN-1", exact_quote="Evidencia del análisis."
    )
    assert analysis_citation.report_id is None
    assert analysis_citation.as_dict().get("report_id") is None


def test_engine_collects_analysis_citations():
    repo = InMemoryGraphRepository()
    repo.add_node(
        {
            "id": "a1",
            "label": "Analysis",
            "analysis_id": "an-1",
            "code": "AN-ACME",
            "exact_quote": "Tesis corroborada por el informe.",
        }
    )
    engine = GraphRagEngine(repo)
    result = engine.answer("an-1")
    assert result.citations[0].analysis_id == "an-1"
    assert result.citations[0].code == "AN-ACME"


def test_answer_or_empty_returns_no_evidence_without_citations():
    engine = GraphRagEngine(build_repo(False))
    payload = engine.answer_or_empty("Acme Corp")
    assert payload["citations"] == []
    assert payload["answer"] == NO_EVIDENCE_ANSWER
    assert "subgraph" in payload


def test_answer_or_empty_returns_evidence_when_available():
    engine = GraphRagEngine(build_repo(True))
    payload = engine.answer_or_empty("Acme Corp")
    assert payload["citations"]
    assert payload["citations"][0]["report_id"] == REPORT_ID


def test_fake_engine_answer_or_empty_without_citations():
    payload = FakeRagEngine(citations=[]).answer_or_empty("nada")
    assert payload == {
        "answer": NO_EVIDENCE_ANSWER,
        "subgraph": {"nodes": [], "edges": []},
        "citations": [],
    }
