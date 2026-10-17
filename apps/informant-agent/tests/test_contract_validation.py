"""Validación 100% contra el contrato **sellado** ``extraction-schema.json``.

Autorizado por: SPEC-02 §2 y §6 ("la salida valida al 100% contra
``extraction-schema.json`` y Pydantic") y el prompt del Agente 5.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from informant_agent.contract import (
    ExtractionContractError,
    is_valid,
    load_sealed_schema,
    validate_extraction,
    validate_triplet,
)
from informant_agent.models import ExtractionResult

from .conftest import CANONICAL_ID


def _valid_payload() -> dict:
    return {
        "report_metadata": {
            "title": "Open-source report",
            "source_type": "NEWS",
            "source_url_or_origin": "https://example.test/source",
            "reliability_rating": "B",
        },
        "extracted_triplets": [
            {
                "subject": {"label": "Organization", "name": "State Bank"},
                "predicate": {"type": "FINANCED", "confidence": 0.8},
                "object": {
                    "existing_canonical_id": CANONICAL_ID,
                    "label": "Organization",
                    "name": "Acme Corp",
                },
                "provenance": {
                    "page_number": 1,
                    "char_start": 0,
                    "char_end": 28,
                    "exact_quote": "State Bank financed Acme Corp",
                },
            }
        ],
        "analytical_context_links": [
            {
                "target_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "link_category": "CONTRADICTS",
                "confidence": 0.85,
                "coherence_justification": "El documento refuta la tesis; se preserva.",
                "exact_quote": "State Bank did not finance Acme Corp",
            }
        ],
    }


# --------------------------------------------------------------------------- #
# El contrato sellado es la fuente de verdad
# --------------------------------------------------------------------------- #
def test_sealed_schema_shape_is_v2():
    schema = load_sealed_schema()
    assert schema["$id"].endswith("extraction-schema.json")
    assert "v2" in schema["title"]
    assert set(schema["required"]) == {
        "report_metadata",
        "extracted_triplets",
        "analytical_context_links",
    }
    labels = schema["$defs"]["entityLabel"]["enum"]
    predicates = schema["$defs"]["predicateType"]["enum"]
    links = schema["$defs"]["linkCategory"]["enum"]
    assert labels == ["Person", "Organization", "CyberAsset", "Location", "Event"]
    assert len(predicates) == 6
    assert links == ["CORROBORATES", "CONTRADICTS", "EXTENDS", "ANSWERS_REQUIREMENT"]


# --------------------------------------------------------------------------- #
# Camino feliz
# --------------------------------------------------------------------------- #
def test_valid_payload_passes_contract_and_pydantic():
    payload = _valid_payload()
    validate_extraction(payload)
    validate_triplet(payload["extracted_triplets"][0])
    assert is_valid(payload) is True
    model = ExtractionResult.model_validate(payload)
    assert model.extracted_triplets[0].predicate.type == "FINANCED"
    assert model.extracted_triplets[0].object.existing_canonical_id == CANONICAL_ID


def test_pydantic_dump_roundtrips_through_jsonschema():
    model = ExtractionResult.model_validate(_valid_payload())
    dumped = model.model_dump(mode="json")
    validate_extraction(dumped)


def test_empty_arrays_are_valid():
    payload = _valid_payload()
    payload["extracted_triplets"] = []
    payload["analytical_context_links"] = []
    assert is_valid(payload) is True


# --------------------------------------------------------------------------- #
# Casos inválidos (deben rechazarse, nunca corregirse)
# --------------------------------------------------------------------------- #
def test_invalid_entity_label_is_rejected():
    payload = _valid_payload()
    payload["extracted_triplets"][0]["subject"]["label"] = "Report"  # no extraíble
    with pytest.raises(ExtractionContractError):
        validate_extraction(payload)
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_invalid_predicate_is_rejected():
    payload = _valid_payload()
    payload["extracted_triplets"][0]["predicate"]["type"] = "OWNS_DEVICE"  # fuera de v2
    assert is_valid(payload) is False
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_confidence_out_of_range_is_rejected():
    payload = _valid_payload()
    payload["extracted_triplets"][0]["predicate"]["confidence"] = 1.5
    with pytest.raises(ExtractionContractError):
        validate_extraction(payload)
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(payload)


def test_missing_root_key_is_rejected():
    payload = _valid_payload()
    del payload["analytical_context_links"]
    with pytest.raises(ExtractionContractError):
        validate_extraction(payload)


def test_missing_provenance_is_rejected():
    payload = _valid_payload()
    del payload["extracted_triplets"][0]["provenance"]
    with pytest.raises(ExtractionContractError):
        validate_extraction(payload)


def test_extra_property_is_rejected():
    payload = _valid_payload()
    payload["extra"] = "no permitido"  # additionalProperties: false
    with pytest.raises(ExtractionContractError):
        validate_extraction(payload)


def test_page_number_must_be_positive():
    payload = _valid_payload()
    payload["extracted_triplets"][0]["provenance"]["page_number"] = 0
    with pytest.raises(ExtractionContractError):
        validate_extraction(payload)
