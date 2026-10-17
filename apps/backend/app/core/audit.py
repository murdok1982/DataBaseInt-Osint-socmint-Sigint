"""Bitácora de auditoría de accesos y correlación ``X-Request-ID``.

Autorizado por: **SPEC-06 §1** (un evento JSON ``http_request`` por petición a
``/api/v1``, eventos ``auth`` del login y lista de datos prohibidos) y
**SPEC-04 §2.1** (regla de IP de cliente tras el proxy de confianza).

Solo se registran los campos enumerados en SPEC-06 §1.2. Nunca se leen el
cuerpo, el *query string*, las cookies ni las cabeceras de credenciales.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.config import get_settings

if TYPE_CHECKING:  # pragma: no cover - solo tipado
    from app.core.users import Principal

AUDIT_LOGGER_NAME = "osint.audit"
REQUEST_ID_HEADER = "X-Request-ID"
#: Formato aceptado para un ``X-Request-ID`` aportado por el cliente (SPEC-06 §1.3).
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")
#: Parámetros de ruta que identifican el recurso accedido (SPEC-06 §1.2).
_RESOURCE_PARAMS = ("id", "job_id", "alert_id")


class _JsonLineFormatter(logging.Formatter):
    """El mensaje ya es JSON: se emite tal cual, una línea por evento."""

    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


def get_audit_logger() -> logging.Logger:
    """Logger ``osint.audit`` con salida JSON a ``stdout`` (SPEC-06 §4)."""
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    if not any(getattr(h, "_osint_audit", False) for h in logger.handlers):
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_JsonLineFormatter())
        handler._osint_audit = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def _emit(event: dict[str, Any]) -> None:
    get_audit_logger().info(json.dumps(event, ensure_ascii=False, sort_keys=True))


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="milliseconds")


def resolve_request_id(raw: Optional[str]) -> str:
    """Reutiliza un ``X-Request-ID`` seguro del cliente o genera un UUID4."""
    if raw and _SAFE_REQUEST_ID.match(raw):
        return raw
    return str(uuid.uuid4())


def client_ip(request: Request) -> str:
    """IP del cliente; ``X-Real-IP`` solo si se confía en el proxy (SPEC-04 §2.1)."""
    if get_settings().trust_proxy_headers:
        forwarded = (request.headers.get("x-real-ip") or "").strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else "unknown"


def username_fingerprint(username: str) -> str:
    """SHA-256 truncado (16 hex) del usuario en minúsculas (SPEC-06 §1.4)."""
    digest = hashlib.sha256((username or "").strip().lower().encode("utf-8"))
    return digest.hexdigest()[:16]


def remember_principal(request: Request, principal: "Principal") -> None:
    """Anota el actor autenticado para el evento de auditoría de la petición."""
    request.state.audit_principal = principal


def audit_auth_event(
    request: Request,
    outcome: str,
    *,
    principal: Optional["Principal"] = None,
    username: Optional[str] = None,
) -> None:
    """Evento ``auth`` del login (``SUCCESS``/``FAILURE``/``LOCKED``)."""
    event: dict[str, Any] = {
        "event": "auth",
        "ts": _utc_now(),
        "request_id": getattr(request.state, "request_id", None),
        "outcome": outcome,
        "client_ip": client_ip(request),
    }
    if principal is not None:
        event["actor_id"] = principal.id
        event["actor_role"] = principal.role.value
    else:
        event["username_sha256"] = username_fingerprint(username or "")
    _emit(event)


class AuditMiddleware(BaseHTTPMiddleware):
    """Un evento ``http_request`` por petición a ``/api/v1`` + ``X-Request-ID``."""

    def __init__(self, app: Any, prefix: str = "/api/v1") -> None:
        super().__init__(app)
        self.prefix = prefix

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = resolve_request_id(request.headers.get(REQUEST_ID_HEADER))
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            if request.url.path.startswith(self.prefix):
                self._log(request, request_id, status_code, started)

    @staticmethod
    def _log(request: Request, request_id: str, status_code: int, started: float) -> None:
        route = request.scope.get("route")
        template = getattr(route, "path", None) or "<sin ruta>"
        params = request.scope.get("path_params") or {}
        resource_id = next(
            (str(params[name]) for name in _RESOURCE_PARAMS if name in params), None
        )
        principal = getattr(request.state, "audit_principal", None)
        _emit(
            {
                "event": "http_request",
                "ts": _utc_now(),
                "request_id": request_id,
                "actor_id": getattr(principal, "id", None),
                "actor_role": getattr(getattr(principal, "role", None), "value", None),
                "method": request.method,
                "route": template,
                "resource_id": resource_id,
                "status": status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "client_ip": client_ip(request),
            }
        )


__all__ = [
    "AUDIT_LOGGER_NAME",
    "REQUEST_ID_HEADER",
    "AuditMiddleware",
    "audit_auth_event",
    "client_ip",
    "get_audit_logger",
    "remember_principal",
    "resolve_request_id",
    "username_fingerprint",
]
