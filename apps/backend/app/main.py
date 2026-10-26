"""Aplicación FastAPI de OSINT Nexus (zona Agente 3).

Autorizado por: **SPEC-03 §1** (base ``/api/v1``, ``Bearer`` JWT / ``X-API-Key``,
envoltura de errores), **SPEC-03 §5-§6** (409 ``CONFLICT`` para ingesta ciega y
nodo bloqueado), **SPEC-04** (RBAC/seguridad), **SPEC-06 §1** (bitácora de
auditoría) y ``contracts/openapi.yaml`` SELLADO v2.2.0.

Responsabilidades
-----------------
- Montar el router de la API bajo ``/api/v1`` (exactamente los endpoints del
  contrato: nada más, nada menos).
- Exponer ``GET /api/v1/info`` — operación ``getInfo`` del contrato (health
  **público**, ``security: []``); ver ``app/api/v1/system.py``.
- Normalizar **todos** los errores al sobre del contrato:
  ``{"error": {"code": String, "message": String}}``.
- Traducir a **409** las excepciones de dominio del contrato ``Conflict``:
  ingesta ciega (:class:`~app.services.preflight.PreflightRequiredError`) y nodo
  bloqueado (:class:`~app.db.repository.NodeLockedError`) — SPEC-03 §5/§6.
- Cerrar el driver de Neo4j al apagar (la base de datos jamás se expone:
  Constitución, Regla 4).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.deps import close_graph_driver, close_v31_pool
from app.api.router import api_router
from app.core.audit import AuditMiddleware
from app.core.metrics import PrometheusMiddleware, metrics_app
from app.core.errors import (
    CODE_CONFLICT,
    CODE_SERVICE_UNAVAILABLE,
    ConflictError,
    ServiceUnavailable,
    code_for_status,
    error_payload,
)
from app.services.preflight import PreflightRequiredError

API_TITLE = "OSINT Nexus API"
API_VERSION = "3.1.0"
API_DESCRIPTION = (
    "API de inteligencia OSINT (grafo de conocimiento + GraphRAG + correlación "
    "multi-INT). Contrato SELLADO v3.1.0 derivado de SPEC-01/02/03/04 y SPEC-06."
)
API_PREFIX = "/api/v1"
#: Fuera de ``/api/v1``: solo accesible por la red interna (Prometheus).
METRICS_PATH = "/metrics"

#: ``NodeLockedError`` del Agente 1 (inmutabilidad analítica, Constitución
#: Regla 2). ``app.db.repository`` es stdlib-only al importar, pero se resuelve
#: de forma perezosa para no acoplar el arranque a la zona del Agente 1.
try:  # pragma: no cover - la clase existe en el repo del Agente 1
    from app.db.repository import NodeLockedError
except Exception:  # pragma: no cover - repositorio mínimo
    NodeLockedError = None  # type: ignore[assignment]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Arranque/apagado: no se toca la base de datos hasta la primera consulta."""
    yield
    close_graph_driver()
    close_v31_pool()


def _envelope(exc: StarletteHTTPException) -> dict[str, Any]:
    """Convierte cualquier ``detail`` en el sobre de error del contrato."""
    detail = exc.detail
    if isinstance(detail, dict):
        nested = detail.get("error")
        if isinstance(nested, dict) and "code" in nested:
            return detail
        return error_payload(
            str(detail.get("code") or code_for_status(exc.status_code)),
            str(detail.get("message") or ""),
        )
    return error_payload(code_for_status(exc.status_code), str(detail))


def create_app() -> FastAPI:
    """Crea la app FastAPI (factoría; facilita los tests con ``TestClient``)."""
    application = FastAPI(
        title=API_TITLE,
        version=API_VERSION,
        description=API_DESCRIPTION,
        lifespan=lifespan,
    )

    # --- API del contrato (SPEC-03 §1: base /api/v1) ---------------
    # Incluye ``GET /api/v1/info`` (operationId ``getInfo``, público) vía
    # ``app.api.v1.system``; no hay rutas de salud fuera de ``/api/v1``.
    application.include_router(api_router, prefix=API_PREFIX)

    # --- Bitácora de auditoría + X-Request-ID (SPEC-06 §1) ----------
    application.add_middleware(AuditMiddleware, prefix=API_PREFIX)

    # --- Métricas Prometheus (red interna; Nginx no enruta /metrics) -
    application.add_middleware(PrometheusMiddleware)
    application.mount(METRICS_PATH, metrics_app())

    # --- Errores con el sobre del contrato -------------------------
    @application.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(exc),
            headers=getattr(exc, "headers", None),
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ()))
        message = f"Petición inválida en '{location}': {first.get('msg', 'error')}"
        return JSONResponse(
            status_code=422,
            content=error_payload(code_for_status(422), message),
        )

    @application.exception_handler(ServiceUnavailable)
    async def service_unavailable_handler(
        request: Request, exc: ServiceUnavailable
    ) -> JSONResponse:
        """Dependencia de otro agente aún no implementada ⇒ 503, nunca 500."""
        return JSONResponse(
            status_code=503,
            content=error_payload(CODE_SERVICE_UNAVAILABLE, str(exc)),
        )

    # --- 409 CONFLICT (SPEC-03 §5/§6; contrato ``Conflict``) ---------
    async def conflict_handler(request: Request, exc: Exception) -> JSONResponse:
        """Nodo bloqueado o ingesta ciega ⇒ ``409 CONFLICT`` (nunca 500).

        SPEC-03 §5 fija el código del sobre a ``CONFLICT`` tanto para la ingesta
        ciega (``PreflightRequiredError``) como para el nodo bloqueado
        (``NodeLockedError`` / ``ConflictError``).
        """
        return JSONResponse(
            status_code=409,
            content=error_payload(CODE_CONFLICT, str(exc)),
        )

    application.add_exception_handler(ConflictError, conflict_handler)
    application.add_exception_handler(PreflightRequiredError, conflict_handler)
    if NodeLockedError is not None:  # pragma: no branch - siempre presente
        application.add_exception_handler(NodeLockedError, conflict_handler)

    return application


app = create_app()


__all__ = ["app", "create_app", "API_PREFIX", "API_VERSION"]
