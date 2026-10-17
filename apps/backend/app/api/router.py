"""Router agregado de la API (zona Agente 3).

Autorizado por: **SPEC-03 §1** (base ``/api/v1``) y ``contracts/openapi.yaml``
(SELLADO v2.0.0). ``app.main`` lo monta con ``prefix="/api/v1"``.

Cada ``operationId`` del contrato está implementado exactamente una vez; la
trazabilidad completa endpoint ↔ ``operationId`` ↔ rol mínimo está en
:data:`app.core.rbac.ENDPOINT_MATRIX` y se verifica en
``tests/security/test_rbac_matrix.py``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    analyses,
    auth,
    coherence,
    context,
    graph,
    ingest,
    reports,
    search,
    system,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(graph.router)
api_router.include_router(context.router)
api_router.include_router(ingest.router)
api_router.include_router(reports.router)
api_router.include_router(analyses.router)
api_router.include_router(coherence.router)
api_router.include_router(search.router)
api_router.include_router(system.router)

__all__ = ["api_router"]
