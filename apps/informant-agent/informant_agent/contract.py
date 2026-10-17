"""Carga y validación contra el **contrato sellado** ``extraction-schema.json``.

El contrato vive en ``/contracts`` (zona sellada, SOLO LECTURA). Este módulo lo
localiza de forma robusta (subiendo directorios) y valida con ``jsonschema``
(Draft 2020-12), que es la fuente de verdad (SPEC-02 §2: "el contrato manda").
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

#: Nombre relativo del contrato dentro del repositorio.
CONTRACT_RELATIVE_PATH = Path("contracts") / "extraction-schema.json"

#: Variable de entorno para sobreescribir la ruta del contrato.
CONTRACT_ENV_VAR = "INFORMANT_CONTRACT_SCHEMA"


class ExtractionContractError(ValueError):
    """El payload no valida contra ``extraction-schema.json``."""


def find_repo_root(start: Path | None = None) -> Path:
    """Sube directorios desde ``start`` hasta hallar ``contracts/extraction-schema.json``."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / CONTRACT_RELATIVE_PATH).is_file():
            return candidate
    raise FileNotFoundError(
        "No se encontró el contrato sellado "
        f"'{CONTRACT_RELATIVE_PATH.as_posix()}' subiendo desde {here}. "
        f"Define {CONTRACT_ENV_VAR} con una ruta explícita."
    )


def resolve_schema_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Resuelve la ruta efectiva del contrato (argumento > entorno > repo)."""
    explicit = path if path is not None else os.environ.get(CONTRACT_ENV_VAR)
    if explicit:
        candidate = Path(explicit)
        if not candidate.is_file():
            raise FileNotFoundError(f"Contrato '{candidate}' no existe.")
        return candidate.resolve()
    return (find_repo_root() / CONTRACT_RELATIVE_PATH).resolve()


def load_sealed_schema(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    """Devuelve el JSON Schema sellado (dict)."""
    with resolve_schema_path(path).open("r", encoding="utf-8") as fh:
        return json.load(fh)


@lru_cache(maxsize=4)
def _validator_for(schema_path: str) -> Draft202012Validator:
    return Draft202012Validator(load_sealed_schema(schema_path))


def _validator(path: str | os.PathLike[str] | None = None) -> Draft202012Validator:
    return _validator_for(str(resolve_schema_path(path)))


def validate_extraction(
    payload: Mapping[str, Any], path: str | os.PathLike[str] | None = None
) -> None:
    """Valida un payload de extracción completo contra el contrato sellado.

    :raises ExtractionContractError: si no valida al 100%.
    """
    errors = sorted(_validator(path).iter_errors(payload), key=lambda e: list(e.path))
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
        )
        raise ExtractionContractError(f"Payload no válido contra el contrato: {detail}")


def validate_triplet(
    triplet: Mapping[str, Any], path: str | os.PathLike[str] | None = None
) -> None:
    """Valida una tripleta suelta contra ``$defs.triplet`` (atajo de diagnóstico)."""
    schema = load_sealed_schema(path)
    sub = {"$ref": "#/$defs/triplet", "$defs": schema["$defs"]}
    errors = sorted(
        Draft202012Validator(sub).iter_errors(triplet), key=lambda e: list(e.path)
    )
    if errors:
        detail = "; ".join(
            f"{'/'.join(str(p) for p in e.path) or '<root>'}: {e.message}" for e in errors
        )
        raise ExtractionContractError(f"Tripleta no válida: {detail}")


def is_valid(
    payload: Mapping[str, Any], path: str | os.PathLike[str] | None = None
) -> bool:
    """``True`` si el payload valida contra el contrato sellado."""
    try:
        validate_extraction(payload, path)
    except (ExtractionContractError, ValidationError):
        return False
    return True


__all__ = [
    "CONTRACT_ENV_VAR",
    "CONTRACT_RELATIVE_PATH",
    "ExtractionContractError",
    "find_repo_root",
    "is_valid",
    "load_sealed_schema",
    "resolve_schema_path",
    "validate_extraction",
    "validate_triplet",
]
