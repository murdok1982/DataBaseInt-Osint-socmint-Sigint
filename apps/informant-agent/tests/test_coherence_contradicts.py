"""Referencia a Análisis Humanos: ``CONTRADICTS`` sin sobrescribir la tesis.

Autorizado por: SPEC-02 §1 Fase 2 y §6, y el DoD del Agente 5
("un documento que contradice una tesis produce ``CONTRADICTS`` (no edita la tesis)").
"""

from __future__ import annotations

from informant_agent.contract import validate_extraction
from informant_agent.extractor import ContextGuidedExtractor
from informant_agent.models import ExtractionResult

from .conftest import ANALYSIS_ID, REQUIREMENT_ID

THESIS_CONTRADICTING = "State Bank did not finance Acme Corp; the transfer was denied."
THESIS_CORROBORATING = "State Bank financed Acme Corp in 2021."


def _links(payload: dict) -> list[dict]:
    return payload["analytical_context_links"]


def _by_category(payload: dict, category: str) -> list[dict]:
    return [link for link in _links(payload) if link["link_category"] == category]


# --------------------------------------------------------------------------- #
# Contradicción
# --------------------------------------------------------------------------- #
def test_contradicting_document_produces_contradicts(context, make_document):
    doc = make_document(THESIS_CONTRADICTING, title="Denial", source_type="TELEGRAM")
    payload = ContextGuidedExtractor().build(doc, context)

    validate_extraction(payload)  # valida 100% contra el contrato sellado
    contradicts = _by_category(payload, "CONTRADICTS")
    assert len(contradicts) == 1

    link = contradicts[0]
    assert link["target_id"] == ANALYSIS_ID
    assert link["coherence_justification"].strip()  # explica la discrepancia
    assert link["exact_quote"] in doc.full_text()  # cita literal
    assert 0.0 <= link["confidence"] <= 1.0


def test_contradiction_never_edits_the_human_thesis(context, make_document):
    before = context.model_dump()
    doc = make_document(THESIS_CONTRADICTING)

    payload = ContextGuidedExtractor().build(doc, context)

    after = context.model_dump()
    assert after == before  # el contexto/tesis humana queda intacto
    # El único efecto es un *vínculo*, jamás una escritura sobre el Analysis.
    assert all(
        "conclusion_body" not in link for link in _links(payload)
    )
    # La tesis del pre-vuelo sigue siendo la misma textualmente.
    assert context.analyses[0].thesis_summary == "State Bank financed Acme Corp."


def test_contradiction_justification_mentions_preserving_analyst_context(context, make_document):
    doc = make_document(THESIS_CONTRADICTING)
    payload = ContextGuidedExtractor().build(doc, context)
    justification = _by_category(payload, "CONTRADICTS")[0]["coherence_justification"].lower()
    assert "preserv" in justification  # "se preserva la conclusión del analista"


# --------------------------------------------------------------------------- #
# Corroboración y RFI
# --------------------------------------------------------------------------- #
def test_corroborating_document_produces_corroborates(context, make_document):
    doc = make_document(THESIS_CORROBORATING)
    payload = ContextGuidedExtractor().build(doc, context)

    validate_extraction(payload)
    assert _by_category(payload, "CONTRADICTS") == []
    corroborates = _by_category(payload, "CORROBORATES")
    assert len(corroborates) == 1
    assert corroborates[0]["target_id"] == ANALYSIS_ID
    assert corroborates[0]["exact_quote"] in doc.full_text()


def test_requirement_is_answered(context, make_document):
    doc = make_document(THESIS_CORROBORATING)
    payload = ContextGuidedExtractor().build(doc, context)

    answers = _by_category(payload, "ANSWERS_REQUIREMENT")
    assert len(answers) == 1
    assert answers[0]["target_id"] == REQUIREMENT_ID
    assert answers[0]["exact_quote"] in doc.full_text()


def test_unrelated_document_keeps_links_empty(context, make_document):
    doc = make_document("The weather in the mountains was cold and rainy all week.")
    payload = ContextGuidedExtractor().build(doc, context)

    validate_extraction(payload)
    assert _links(payload) == []


def test_payload_is_valid_extraction_result(context, make_document):
    doc = make_document(THESIS_CONTRADICTING, title="Denial")
    payload = ContextGuidedExtractor().build(doc, context)
    model = ExtractionResult.model_validate(payload)
    assert model.report_metadata.title == "Denial"
