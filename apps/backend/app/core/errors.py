"""Envoltura de errores de la API OSINT Nexus.

Autorizado por: **SPEC-03 §1** — *Errores:*
``{ "error": { "code": String, "message": String } }`` y
``contracts/openapi.yaml`` (componente ``Error``).

Este módulo es la única fuente de los códigos de error del backend, de modo que
rutas, RBAC y manejadores globales devuelvan exactamente el mismo sobre.
"""

from __future__ import annotations

from typing import Any, Optional

# --- Códigos de error (no inventar otros sin spec) ---------------------
CODE_UNAUTHORIZED = "UNAUTHORIZED"
CODE_FORBIDDEN = "FORBIDDEN"
CODE_NOT_FOUND = "NOT_FOUND"
CODE_CONFLICT = "CONFLICT"
CODE_VALIDATION_ERROR = "VALIDATION_ERROR"
CODE_SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
CODE_INTERNAL_ERROR = "INTERNAL_ERROR"
#: Subida de informe por encima de ``MAX_UPLOAD_MB`` (SPEC-02 §1.1).
CODE_PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
#: Extensión o contenido de informe no admitidos (SPEC-02 §1.1).
CODE_UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"

#: Código por defecto para cada status HTTP usado por el contrato.
STATUS_TO_CODE: dict[int, str] = {
    400: "BAD_REQUEST",
    401: CODE_UNAUTHORIZED,
    403: CODE_FORBIDDEN,
    404: CODE_NOT_FOUND,
    409: CODE_CONFLICT,
    413: CODE_PAYLOAD_TOO_LARGE,
    415: CODE_UNSUPPORTED_MEDIA_TYPE,
    422: CODE_VALIDATION_ERROR,
    500: CODE_INTERNAL_ERROR,
    503: CODE_SERVICE_UNAVAILABLE,
}


def error_payload(code: str, message: str) -> dict[str, dict[str, str]]:
    """Construye el sobre de error del contrato (``Error`` schema)."""
    return {"error": {"code": code, "message": message}}


class ServiceUnavailable(RuntimeError):
    """Dependencia aún no implementada por otro agente (SPEC-03 / prompt A3).

    Se traduce a ``503`` con ``{"error": {"code": "SERVICE_UNAVAILABLE"}}``.
    Nunca se eleva en tiempo de import: solo al atender una petición, para no
    romper el arranque de la aplicación.
    """

    code = CODE_SERVICE_UNAVAILABLE


class ConflictError(RuntimeError):
    """Conflicto de dominio (p. ej. nodo bloqueado o ingesta sin Pre-Flight).

    Se traduce a ``409`` con ``{"error": {"code": "CONFLICT"}}``
    (``contracts/openapi.yaml`` → response ``Conflict``; SPEC-03 §5/§6).
    """

    code = CODE_CONFLICT

    def __init__(self, message: str, *, code: str = CODE_CONFLICT) -> None:
        super().__init__(message)
        self.code = code


def detail_from_code(code: str, message: str) -> dict[str, Any]:
    """``detail`` de :class:`fastapi.HTTPException` ya normalizado al contrato."""
    return error_payload(code, message)


def code_for_status(status_code: int, fallback: Optional[str] = None) -> str:
    """Código textual para un status HTTP según la tabla del contrato."""
    return STATUS_TO_CODE.get(status_code, fallback or "HTTP_ERROR")


__all__ = [
    "CODE_UNAUTHORIZED",
    "CODE_FORBIDDEN",
    "CODE_NOT_FOUND",
    "CODE_CONFLICT",
    "CODE_VALIDATION_ERROR",
    "CODE_SERVICE_UNAVAILABLE",
    "CODE_INTERNAL_ERROR",
    "STATUS_TO_CODE",
    "error_payload",
    "detail_from_code",
    "code_for_status",
    "ServiceUnavailable",
    "ConflictError",
]
