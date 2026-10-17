"""Extracción: grounding (``exact_quote`` literal) y ``existing_canonical_id``.

Autorizado por: SPEC-02 §2 ("``exact_quote`` literal; no se inventa ni deduce") y
§4.5 (uso de ``existing_canonical_id`` cuando el pre-vuelo lo aporta).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from informant_agent.collectors.base import Assertion
from informant_agent.contract import ExtractionContractError, validate_extraction
from informant_agent.extractor import ContextGuidedExtractor, normalize_name

from .conftest import CANONICAL_ID


def test_existing_canonical_id_is_used_from_preflight(context, make_document):
    doc = make_document(
        "State Bank financed Acme Corp.",
        assertions=[
            Assertion(
                predicate="FINANCED",
                subject_name="State Bank",
                object_name="Acme Corp",
                quote="State Bank financed Acme Corp",
                page_number=1,
            )
        ],
    )
    payload = ContextGuidedExtractor().build(doc, context)
    triplet = payload["extracted_triplets"][0]

    assert triplet["object"]["existing_canonical_id"] == CANONICAL_ID
    assert triplet["object"]["label"] == "Organization"
    # El sujeto no está en el diccionario canónico → sin id.
    assert "existing_canonical_id" not in triplet["subject"]


def test_quote_that_is_not_literal_is_dropped(context, make_document):
    doc = make_document(
        "State Bank financed Acme Corp.",
        assertions=[
            Assertion(
                predicate="FINANCED",
                subject_name="State Bank",
                object_name="Acme Corp",
                quote="a quote that does not appear anywhere",  # inventada
                page_number=1,
            )
        ],
    )
    payload = ContextGuidedExtractor().build(doc, context)
    assert payload["extracted_triplets"] == []  # no se inventa evidencia
    validate_extraction(payload)


def test_char_offsets_point_to_a_literal_substring(context, make_document):
    text = "State Bank financed Acme Corp."
    doc = make_document(text)  # sin assertions → detector por patrones
    payload = ContextGuidedExtractor().build(doc, context)

    assert payload["extracted_triplets"], "el detector por patrones debe extraer una tripleta"
    for triplet in payload["extracted_triplets"]:
        prov = triplet["provenance"]
        assert text[prov["char_start"] : prov["char_end"]] == prov["exact_quote"]
        assert prov["page_number"] == 1


def test_detected_triplet_uses_predicate_and_canonical_id(context, make_document):
    doc = make_document("State Bank financed Acme Corp.")
    payload = ContextGuidedExtractor().build(doc, context)
    triplet = payload["extracted_triplets"][0]

    assert triplet["predicate"]["type"] == "FINANCED"
    assert triplet["predicate"]["confidence"] >= 0.5
    assert triplet["object"]["existing_canonical_id"] == CANONICAL_ID


def test_out_of_range_confidence_is_rejected(context, make_document):
    doc = make_document(
        "State Bank financed Acme Corp.",
        assertions=[
            Assertion(
                predicate="FINANCED",
                subject_name="State Bank",
                object_name="Acme Corp",
                quote="State Bank financed Acme Corp",
                page_number=1,
                confidence=1.5,  # fuera de rango → se rechaza, nunca se corrige
            )
        ],
    )
    with pytest.raises(ValidationError):
        ContextGuidedExtractor().build(doc, context)


def test_unknown_page_is_dropped(context, make_document):
    doc = make_document(
        "State Bank financed Acme Corp.",
        assertions=[
            Assertion(
                predicate="FINANCED",
                subject_name="State Bank",
                object_name="Acme Corp",
                quote="State Bank financed Acme Corp",
                page_number=99,  # no existe
            )
        ],
    )
    payload = ContextGuidedExtractor().build(doc, context)
    assert payload["extracted_triplets"] == []


def test_normalize_name_strips_accents_and_case():
    assert normalize_name("  Acmé   Corp ") == "acme corp"
