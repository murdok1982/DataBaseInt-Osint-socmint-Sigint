"""Rutas de sistema (tag ``system`` del contrato).

Autorizado por: ``contracts/openapi.yaml`` (SELLADO v2.0.0), operación
``getInfo`` — *«Salud y metadatos del servicio (público)»*.

| operationId | Método | Ruta           | Rol mínimo |
| :---------- | :----- | :------------- | :--------- |
| ``getInfo`` | GET    | ``/info``      | público    |

Sustituye al antiguo ``GET /api/info`` (fuera de contrato): ahora el *health*
vive dentro de ``/api/v1`` y es trazable como cualquier otra operación
(``security: []`` en el contrato). Solo expone metadatos **no sensibles**.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import get_settings

router = APIRouter(tags=["system"])


class InfoResponse(BaseModel):
    """``getInfo`` — salud y metadatos del servicio (contrato: ``status``/``version``)."""

    status: str
    version: str
    name: Optional[str] = None
    environment: Optional[str] = None


# =====================================================================
# GET /info — getInfo (público)
# =====================================================================
@router.get(
    "/info",
    operation_id="getInfo",
    response_model=InfoResponse,
    summary="Salud y metadatos del servicio (público)",
    openapi_extra={"security": []},
)
async def get_info(request: Request) -> InfoResponse:
    """``GET /api/v1/info`` — health público; solo metadatos no sensibles."""
    settings = get_settings()
    return InfoResponse(
        status="ok",
        version=request.app.version,
        name=request.app.title,
        environment=settings.environment,
    )


__all__ = ["router", "InfoResponse"]
