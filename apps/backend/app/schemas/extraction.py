"""Modelos Pydantic v2 + validación JSON Schema — Contrato de extracción (v2.0).

Autorizado por: **SPEC-02 §2** (Contrato de Salida del Informante IA) y **§6**
(la salida valida al 100% contra el contrato sellado).
Contrato espejo (SELLADO v2.0.0): ``contracts/extraction-schema.json``.
Zona: Agente 2 — ``apps/backend/app/schemas/**``.

Raíz del contrato
-----------------
``{report_metadata, extracted_triplets[], analytical_context_links[]}``.

Reglas aplicadas
----------------
- ``additionalProperties: false`` del contrato ⇒ ``extra="forbid"`` en Pydantic.
- Los **5 labels** extraíbles (``Person``, ``Organization``, ``CyberAsset``,
  ``Location``, ``Event``) y los **6 predicados** OSINT extraíbles (SPEC-01 §1/§3.1).
- ``predicate`` es un **objeto** ``{type, confidence}`` (no un ``dict`` libre).
- ``provenance`` **no** incluye ``report_id``: lo asigna el servidor.
- ``status`` de la tripleta es **derivado** por el pipeline (umbral 0.50); el LLM
  no lo emite, por eso no forma parte del modelo del contrato.
- :func:`validate_contract` valida un ``dict`` completo contra el JSON Schema
  sellado usando ``jsonschema`` (con degradación a un validador mínimo propio).
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

#: Config espejo de ``additionalProperties: false`` (contrato).
_STRICT = ConfigDict(extra="forbid")

#: Ruta del contrato sellado: ``<repo>/contracts/extraction-schema.json``.
SCHEMA_PATH = (
    Path(__file__).resolve().parents[4] / "contracts" / "extraction-schema.json"
)


# =====================================================================
# Enumeraciones del contrato (``$defs``)
# =====================================================================
class EntityLabel(str, Enum):
    """Los 5 labels extraíbles (SPEC-01 §1, contrato ``$defs.entityLabel``)."""

    PERSON = "Person"
    ORGANIZATION = "Organization"
    CYBER_ASSET = "CyberAsset"
    LOCATION = "Location"
    EVENT = "Event"


class PredicateType(str, Enum):
    """Los 6 predicados OSINT extraíbles (SPEC-01 §3.1, contrato ``$defs.predicateType``)."""

    CONTROLS = "CONTROLS"
    FINANCED = "FINANCED"
    USES_ASSET = "USES_ASSET"
    LOCATED_AT = "LOCATED_AT"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    PARTICIPATED_IN = "PARTICIPATED_IN"


class LinkCategory(str, Enum):
    """Vínculo con el trabajo del Analista (SPEC-01 §3.2, ``$defs.linkCategory``)."""

    CORROBORATES = "CORROBORATES"
    CONTRADICTS = "CONTRADICTS"
    EXTENDS = "EXTENDS"
    ANSWERS_REQUIREMENT = "ANSWERS_REQUIREMENT"


class SourceType(str, Enum):
    """Origen del informe (``$defs.report_metadata.source_type``)."""

    OSINT_PDF = "OSINT_PDF"
    NEWS = "NEWS"
    TELEGRAM = "TELEGRAM"
    RSS = "RSS"
    PUBLIC_REGISTRY = "PUBLIC_REGISTRY"


class ReliabilityRating(str, Enum):
    """Fiabilidad de la fuente, escala A–F (``$defs.report_metadata.reliability_rating``)."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"


class PredicateStatus(str, Enum):
    """Estado **derivado** de la tripleta por el pipeline (SPEC-02 §3).

    No forma parte del contrato de salida: el LLM no lo emite (§2).
    """

    AUTO_ACCEPTED = "AUTO_ACCEPTED"
    PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"


# =====================================================================
# Modelos (espejo del contrato v2.0)
# =====================================================================
class ReportMetadata(BaseModel):
    """``$defs.report_metadata`` — procedencia del informe (SPEC-02 §2)."""

    model_config = _STRICT

    title: Annotated[str, Field(min_length=1)]
    source_type: SourceType
    source_url_or_origin: str = ""
    reliability_rating: ReliabilityRating


class Entity(BaseModel):
    """``$defs.entity`` — entidad extraída (SPEC-01 §1).

    ``existing_canonical_id`` lo aporta la Fase 0 (pre-vuelo) y se usa como
    candidato de máxima prioridad en la Entity Resolution (SPEC-02 §4.5).
    """

    model_config = _STRICT

    existing_canonical_id: str | None = None
    label: EntityLabel
    name: Annotated[str, Field(min_length=1, max_length=512)]
    attributes: dict[str, Any] = Field(default_factory=dict)


class Predicate(BaseModel):
    """``$defs.predicate`` — objeto ``{type, confidence}`` (SPEC-02 §2)."""

    model_config = _STRICT

    type: PredicateType
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]


class Provenance(BaseModel):
    """``$defs.provenance`` — trazabilidad de la tripleta (SPEC-02 §2 · Regla 4).

    ``char_start``/``char_end`` son offsets **relativos a la página**
    ``page_number``, de modo que ``page_text[char_start:char_end] == exact_quote``.
    ``report_id`` lo asigna el servidor: **no** forma parte del contrato.
    """

    model_config = _STRICT

    page_number: Annotated[int, Field(ge=1)]
    char_start: Annotated[int, Field(ge=0)] | None = None
    char_end: Annotated[int, Field(ge=0)] | None = None
    exact_quote: Annotated[str, Field(min_length=1, max_length=2000)]


class Triplet(BaseModel):
    """``$defs.triplet`` — [Sujeto] — [Predicado] — [Objeto] con procedencia."""

    model_config = _STRICT

    subject: Entity
    predicate: Predicate
    object: Entity
    provenance: Provenance


class AnalyticalContextLink(BaseModel):
    """``$defs.analytical_context_link`` — vínculo hacia Análisis/RFI (SPEC-02 §2)."""

    model_config = _STRICT

    target_id: str = Field(min_length=1)
    link_category: LinkCategory
    confidence: Annotated[float, Field(ge=0.0, le=1.0)]
    coherence_justification: Annotated[str, Field(min_length=1)]
    exact_quote: Annotated[str, Field(min_length=1)]


class ExtractionResult(BaseModel):
    """Raíz del contrato de salida del Informante IA (SPEC-02 §2)."""

    model_config = _STRICT

    report_metadata: ReportMetadata
    extracted_triplets: list[Triplet] = Field(default_factory=list)
    analytical_context_links: list[AnalyticalContextLink] = Field(default_factory=list)


# =====================================================================
# Validación contra el JSON Schema sellado (SPEC-02 §2/§6)
# =====================================================================
class SchemaValidationError(ValueError):
    """El payload no valida contra ``extraction-schema.json`` (SPEC-02 §6)."""

    def __init__(self, errors: Sequence[str]) -> None:
        self.errors: list[str] = [str(item) for item in errors]
        super().__init__(
            "El payload no valida contra extraction-schema.json: "
            + "; ".join(self.errors)
        )


try:  # pragma: no cover - rama dependiente del entorno
    import jsonschema as _jsonschema

    _HAS_JSONSCHEMA = True
except ImportError:  # pragma: no cover - fallback offline
    _jsonschema = None
    _HAS_JSONSCHEMA = False


@lru_cache(maxsize=1)
def load_contract_schema() -> dict[str, Any]:
    """Carga (y cachea) el JSON Schema sellado del contrato."""
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _build_format_checker() -> Any:
    """``FormatChecker`` de jsonschema con soporte explícito de ``uuid``."""
    checker = _jsonschema.FormatChecker()

    @checker.checks("uuid")
    def _is_uuid(value: object) -> bool:  # noqa: ANN001 - API de jsonschema
        try:
            uuid.UUID(str(value))
        except (ValueError, AttributeError, TypeError):
            return False
        return True

    return checker


_FORMAT_CHECKER = _build_format_checker() if _HAS_JSONSCHEMA else None


# --- Validador mínimo (fallback sin jsonschema) ----------------------
_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


def _matches_type(instance: Any, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(instance, item) for item in expected)
    expected_types = _JSON_TYPES.get(expected)
    if expected_types is None:  # tipo no soportado → no se puede refutar
        return True
    if isinstance(instance, bool) and expected in ("integer", "number"):
        return False
    return isinstance(instance, expected_types)


def _resolve_ref(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/"
    if not ref.startswith(prefix):
        raise SchemaValidationError([f"$ref externo no soportado: {ref}"])
    target: Any = root
    for part in ref[len(prefix):].split("/"):
        target = target[part]
    return target


def _minimal_validate(
    instance: Any, schema: dict[str, Any], root: dict[str, Any], path: str
) -> list[str]:
    """Validador recursivo del subconjunto del contrato (sin jsonschema)."""
    errors: list[str] = []

    if "$ref" in schema:
        return _minimal_validate(instance, _resolve_ref(schema["$ref"], root), root, path)

    expected_type = schema.get("type")
    if expected_type is not None and not _matches_type(instance, expected_type):
        errors.append(
            f"{path}: se esperaba tipo {expected_type}, se obtuvo "
            f"{type(instance).__name__}"
        )
        return errors

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} no pertenece a {schema['enum']}")

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: longitud menor a {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longitud mayor a {schema['maxLength']}")
        if schema.get("format") == "uuid":
            try:
                uuid.UUID(instance)
            except (ValueError, AttributeError, TypeError):
                errors.append(f"{path}: {instance!r} no es un UUID válido")

    if not isinstance(instance, bool) and isinstance(instance, (int, float)):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < mínimo {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > máximo {schema['maximum']}")

    if isinstance(instance, dict):
        for required in schema.get("required", []):
            if required not in instance:
                errors.append(f"{path}: falta la propiedad requerida '{required}'")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in properties:
                errors += _minimal_validate(
                    value, properties[key], root, f"{path}.{key}"
                )
            elif additional is False:
                errors.append(f"{path}: propiedad adicional no permitida '{key}'")
            elif isinstance(additional, dict):
                errors += _minimal_validate(
                    value, additional, root, f"{path}.{key}"
                )

    if isinstance(instance, list) and "items" in schema:
        for position, item in enumerate(instance):
            errors += _minimal_validate(item, schema["items"], root, f"{path}[{position}]")

    return errors


def validate_contract(payload: Mapping[str, Any]) -> None:
    """Valida el payload completo contra ``extraction-schema.json``.

    :raises SchemaValidationError: si no valida al 100%.
    """
    schema = load_contract_schema()
    if _HAS_JSONSCHEMA:
        validator = _jsonschema.Draft202012Validator(
            schema, format_checker=_FORMAT_CHECKER
        )
        errors = sorted(
            validator.iter_errors(payload), key=lambda error: list(error.path)
        )
        if errors:
            raise SchemaValidationError(
                [
                    f"{'/'.join(str(part) for part in error.absolute_path) or '$'}: "
                    f"{error.message}"
                    for error in errors
                ]
            )
        return
    errors = _minimal_validate(dict(payload), schema, schema, "$")
    if errors:
        raise SchemaValidationError(errors)


def _subschema(ref: str) -> dict[str, Any]:
    """Sub-schema ``{"$ref": ref, "$defs": ...}`` para validar un fragmento."""
    schema = load_contract_schema()
    return {"$ref": f"#/$defs/{ref}", "$defs": schema["$defs"]}


def _as_mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return dict(value)


def validate_triplet(triplet: Triplet | Mapping[str, Any]) -> None:
    """Valida una única tripleta contra ``$defs.triplet`` (SPEC-02 §2)."""
    data = _as_mapping(triplet)
    if _HAS_JSONSCHEMA:
        validator = _jsonschema.Draft202012Validator(_subschema("triplet"))
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if errors:
            raise SchemaValidationError(
                [
                    f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
                    for e in errors
                ]
            )
        return
    sub = _subschema("triplet")  # pragma: no cover - rama offline
    errors = _minimal_validate(data, sub, sub, "$")
    if errors:  # pragma: no cover
        raise SchemaValidationError(errors)


def validate_link(link: AnalyticalContextLink | Mapping[str, Any]) -> None:
    """Valida un único vínculo contra ``$defs.analytical_context_link``."""
    data = _as_mapping(link)
    if _HAS_JSONSCHEMA:
        validator = _jsonschema.Draft202012Validator(_subschema("analytical_context_link"))
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if errors:
            raise SchemaValidationError(
                [
                    f"{'/'.join(str(p) for p in e.absolute_path) or '$'}: {e.message}"
                    for e in errors
                ]
            )
        return
    sub = _subschema("analytical_context_link")  # pragma: no cover - rama offline
    errors = _minimal_validate(data, sub, sub, "$")
    if errors:  # pragma: no cover
        raise SchemaValidationError(errors)


def is_valid_contract(payload: Mapping[str, Any]) -> bool:
    """``True`` si ``payload`` valida contra el contrato."""
    try:
        validate_contract(payload)
    except SchemaValidationError:
        return False
    return True


#: Conveniencias de introspección (no forman parte del contrato).
EXTRACTABLE_LABELS: tuple[str, ...] = tuple(label.value for label in EntityLabel)
PREDICATE_TYPES: tuple[str, ...] = tuple(pred.value for pred in PredicateType)
LINK_CATEGORIES: tuple[str, ...] = tuple(cat.value for cat in LinkCategory)
PREDICATE_STATUSES: tuple[str, ...] = tuple(status.value for status in PredicateStatus)

__all__ = [
    "AnalyticalContextLink",
    "Entity",
    "EntityLabel",
    "ExtractionResult",
    "LinkCategory",
    "Predicate",
    "PredicateStatus",
    "PredicateType",
    "Provenance",
    "ReliabilityRating",
    "ReportMetadata",
    "SCHEMA_PATH",
    "SchemaValidationError",
    "SourceType",
    "Triplet",
    "EXTRACTABLE_LABELS",
    "LINK_CATEGORIES",
    "PREDICATE_STATUSES",
    "PREDICATE_TYPES",
    "is_valid_contract",
    "load_contract_schema",
    "validate_contract",
    "validate_link",
    "validate_triplet",
]
