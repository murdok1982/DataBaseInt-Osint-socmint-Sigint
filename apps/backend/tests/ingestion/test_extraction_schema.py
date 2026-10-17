"""Tests — validación estricta contra `extraction-schema.json` (SPEC-02 §2/§6).

Autorizado por: SPEC-02 §2 (contrato de salida v2.0) y §6 (la salida valida al
100% contra el contrato sellado y contra el modelo Pydantic).

Raíz del contrato v2.0:
``{report_metadata, extracted_triplets[], analytical_context_links[]}``.
``report_id`` **no** forma parte del contrato: lo asigna el servidor.
"""

from __future__ import annotations

from pydantic import ValidationError

from app.schemas.extraction import (
    AnalyticalContextLink,
    Entity,
    EntityLabel,
    ExtractionResult,
    LinkCategory,
    Predicate,
    PredicateType,
    Provenance,
    Triplet,
    is_valid_contract,
    load_contract_schema,
    validate_contract,
    validate_link,
    validate_triplet,
)
from app.services.nlp_extractor import (
    SchemaValidationError,
    is_valid,
    load_contract_schema as load_contract_schema_from_extractor,
    validate_extraction,
)

# ---------------------------------------------------------------------
# Payload canónico v2.0
# ---------------------------------------------------------------------
def valid_triplet() -> dict:
    """Tripleta canónica del contrato v2.0 (sin ``report_id``)."""
    return {
        "subject": {
            "label": "Person",
            "name": "Juan Pérez",
            "attributes": {"nationality": "ES"},
        },
        "predicate": {"type": "FINANCED", "confidence": 0.73},
        "object": {
            "label": "Organization",
            "name": "Acme Corp",
            "attributes": {"registration_id": "REG-1"},
        },
        "provenance": {
            "page_number": 2,
            "char_start": 10,
            "char_end": 52,
            "exact_quote": "Juan Pérez financió a Acme Corp",
        },
    }


def valid_link() -> dict:
    """Vínculo analítico canónico del contrato v2.0."""
    return {
        "target_id": "55555555-5555-4555-8555-555555555555",
        "link_category": "CORROBORATES",
        "confidence": 0.8,
        "coherence_justification": "La tesis del expediente coincide con el informe.",
        "exact_quote": "Juan Pérez financió a Acme Corp",
    }


def valid_metadata() -> dict:
    return {
        "title": "Informe OSINT de prueba",
        "source_type": "OSINT_PDF",
        "source_url_or_origin": "https://example.org/informe.pdf",
        "reliability_rating": "B",
    }


def payload(
    triplet: dict | None = None, links: list[dict] | None = None
) -> dict:
    return {
        "report_metadata": valid_metadata(),
        "extracted_triplets": [triplet or valid_triplet()],
        "analytical_context_links": links if links is not None else [],
    }


# ---------------------------------------------------------------------
# Camino feliz
# ---------------------------------------------------------------------
def test_valid_triplet_passes_jsonschema():
    validate_triplet(valid_triplet())
    validate_contract(payload())
    assert is_valid_contract(payload()) is True
    assert is_valid(payload()) is True


def test_valid_triplet_passes_pydantic_models():
    result = ExtractionResult.model_validate(payload())
    assert isinstance(result.extracted_triplets[0], Triplet)
    assert result.extracted_triplets[0].subject.label is EntityLabel.PERSON
    assert result.extracted_triplets[0].predicate.type is PredicateType.FINANCED
    assert result.extracted_triplets[0].provenance.page_number == 2
    assert result.report_metadata.title == "Informe OSINT de prueba"


def test_schema_file_is_sealed_contract():
    schema = load_contract_schema()
    assert schema["$id"].endswith("extraction-schema.json")
    assert schema["$comment"].startswith("SEALED v2.0.0")
    labels = schema["$defs"]["entityLabel"]["enum"]
    predicates = schema["$defs"]["predicateType"]["enum"]
    links = schema["$defs"]["linkCategory"]["enum"]
    assert labels == ["Person", "Organization", "CyberAsset", "Location", "Event"]
    assert "Report" not in labels
    assert len(predicates) == 6
    assert len(links) == 4


def test_python_models_are_equivalent_to_the_contract_enums():
    """SPEC-02 §2: el JSON Schema y el modelo Pydantic deben ser equivalentes."""
    schema = load_contract_schema()
    assert [label.value for label in EntityLabel] == schema["$defs"]["entityLabel"]["enum"]
    assert [pred.value for pred in PredicateType] == schema["$defs"]["predicateType"]["enum"]
    assert [cat.value for cat in LinkCategory] == schema["$defs"]["linkCategory"]["enum"]


def test_reexports_preserve_import_paths():
    """Los validadores re-exportados por el extractor son los del esquema."""
    assert load_contract_schema_from_extractor is load_contract_schema


# ---------------------------------------------------------------------
# Vínculos analíticos (analytical_context_links)
# ---------------------------------------------------------------------
def test_valid_link_passes_jsonschema_and_pydantic():
    validate_link(valid_link())
    result = ExtractionResult.model_validate(payload(links=[valid_link()]))
    assert isinstance(result.analytical_context_links[0], AnalyticalContextLink)
    assert result.analytical_context_links[0].link_category is LinkCategory.CORROBORATES


def test_invalid_link_category_is_rejected():
    link = valid_link()
    link["link_category"] = "IGNORES"  # fuera de SPEC-02 §2
    try:
        validate_link(link)
    except SchemaValidationError:
        pass
    else:  # pragma: no cover - no debe llegar
        raise AssertionError("validate_link aceptó una categoría inválida")
    try:
        AnalyticalContextLink.model_validate(link)
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("El modelo Pydantic aceptó una categoría inválida")


# ---------------------------------------------------------------------
# Casos inválidos (deben fallar)
# ---------------------------------------------------------------------
def test_invalid_label_is_rejected():
    triplet = valid_triplet()
    triplet["subject"]["label"] = "Report"  # no es label extraíble
    try:
        validate_triplet(triplet)
    except SchemaValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("validate_triplet aceptó un label no extraíble")
    assert is_valid(payload(triplet)) is False
    try:
        Triplet.model_validate(triplet)
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("El modelo Pydantic aceptó un label no extraíble")


def test_invalid_predicate_is_rejected():
    triplet = valid_triplet()
    triplet["predicate"]["type"] = "ENEMY_OF"  # fuera de SPEC-01 §3.1
    try:
        validate_triplet(triplet)
    except SchemaValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("validate_triplet aceptó un predicado inválido")
    try:
        Triplet.model_validate(triplet)
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("El modelo Pydantic aceptó un predicado inválido")


def test_mentioned_in_is_not_extractable():
    """``MENTIONED_IN`` existe en el grafo pero NO es extraíble (SPEC-01 §3.1)."""
    triplet = valid_triplet()
    triplet["predicate"]["type"] = "MENTIONED_IN"
    assert is_valid(payload(triplet)) is False


def test_confidence_out_of_range_is_rejected():
    triplet = valid_triplet()
    triplet["predicate"]["confidence"] = 1.5
    try:
        validate_triplet(triplet)
    except SchemaValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("validate_triplet aceptó confidence 1.5")
    try:
        Predicate.model_validate(triplet["predicate"])
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("El modelo Pydantic aceptó confidence 1.5")


def test_negative_confidence_and_missing_fields_are_rejected():
    triplet = valid_triplet()
    triplet["predicate"]["confidence"] = -0.1
    assert is_valid(payload(triplet)) is False

    incomplete = valid_triplet()
    del incomplete["object"]
    try:
        validate_contract(payload(incomplete))
    except SchemaValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("validate_contract aceptó una tripleta sin objeto")


def test_extra_property_is_rejected():
    triplet = valid_triplet()
    triplet["extra"] = "no permitido"  # additionalProperties: false
    try:
        validate_extraction(payload(triplet))
    except SchemaValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("validate_extraction aceptó una propiedad extra")
    try:
        Triplet.model_validate(triplet)
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("El modelo Pydantic aceptó una propiedad extra")


def test_report_id_is_not_allowed_in_provenance():
    """``report_id`` lo asigna el servidor: no pertenece al contrato (v2.0)."""
    triplet = valid_triplet()
    triplet["provenance"]["report_id"] = "11111111-1111-4111-8111-111111111111"
    assert is_valid(payload(triplet)) is False
    try:
        Provenance.model_validate(triplet["provenance"])
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Provenance aceptó report_id (prohibido en v2.0)")


def test_missing_report_metadata_is_rejected():
    broken = payload()
    del broken["report_metadata"]
    assert is_valid(broken) is False
    try:
        ExtractionResult.model_validate(broken)
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("ExtractionResult aceptó un payload sin metadata")


def test_invalid_reliability_rating_is_rejected():
    broken = payload()
    broken["report_metadata"]["reliability_rating"] = "Z"
    assert is_valid(broken) is False


def test_provenance_requires_quote_and_page():
    try:
        Provenance.model_validate({"page_number": 0, "exact_quote": ""})
    except ValidationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("Provenance aceptó page_number=0 y quote vacío")
    assert isinstance(Entity(label="Location", name="Madrid"), Entity)


def test_existing_canonical_id_is_optional_and_nullable():
    triplet = valid_triplet()
    triplet["subject"]["existing_canonical_id"] = "66666666-6666-4666-8666-666666666666"
    assert is_valid(payload(triplet)) is True

    triplet["subject"]["existing_canonical_id"] = None
    assert is_valid(payload(triplet)) is True

    parsed = Triplet.model_validate(triplet)
    assert parsed.subject.existing_canonical_id is None
