"""Rutas de ``/coherence`` — bandeja de alertas Humano-IA.

Autorizado por: **SPEC-01 §3.2/§5.4** (alertas de coherencia), **SPEC-03 §6**
(``/coherence/alerts``, ``ANALYST``+) y ``contracts/openapi.yaml`` SELLADO
v2.0.0.

| operationId              | Método | Ruta                                     | Rol mínimo |
| :----------------------- | :----- | :--------------------------------------- | :--------- |
| ``listCoherenceAlerts``  | GET    | ``/coherence/alerts``                    | ANALYST    |
| ``resolveCoherenceAlert``| PATCH  | ``/coherence/alerts/{alert_id}/resolve`` | ANALYST    |

La **resolución** de una alerta es una decisión humana (SPEC-04 §1): el motor de
coherencia solo crea alertas; jamás las descarta ni altera la tesis.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_graph_gateway
from app.api.graph_gateway import GraphGateway
from app.core.errors import CODE_NOT_FOUND, detail_from_code
from app.core.rbac import Role, require_role
from app.core.users import Principal

router = APIRouter(prefix="/coherence", tags=["coherence"])


class AlertType(str, Enum):
    """``CoherenceAlert.alert_type`` del contrato."""

    CONTRADICTION = "CONTRADICTION"
    HIGH_VALUE_CORROBORATION = "HIGH_VALUE_CORROBORATION"
    NEW_ALIAS_MERGE = "NEW_ALIAS_MERGE"


class AlertStatus(str, Enum):
    """``CoherenceAlert.status`` del contrato."""

    PENDING_ANALYST = "PENDING_ANALYST"
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class ResolveStatus(str, Enum):
    """Estados admitidos al resolver/descartar (``CoherenceAlertResolve``)."""

    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class CoherenceAlert(BaseModel):
    """``CoherenceAlert`` del contrato."""

    model_config = ConfigDict(extra="ignore")

    id: str
    alert_type: Optional[AlertType] = None
    status: Optional[AlertStatus] = None
    description: Optional[str] = None
    analysis_id: Optional[str] = None
    entity_id: Optional[str] = None
    created_at: Optional[datetime] = None


class CoherenceAlertResolve(BaseModel):
    """``CoherenceAlertResolve`` del contrato."""

    status: ResolveStatus
    resolution_note: Optional[str] = None


class AlertListResponse(BaseModel):
    """Respuesta de ``listCoherenceAlerts``."""

    items: list[CoherenceAlert] = Field(default_factory=list)


def _not_found(alert_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail_from_code(
            CODE_NOT_FOUND, f"Alerta de coherencia no encontrada: {alert_id}"
        ),
    )


# =====================================================================
# GET /coherence/alerts — listCoherenceAlerts (ANALYST+)
# =====================================================================
@router.get(
    "/alerts",
    operation_id="listCoherenceAlerts",
    response_model=AlertListResponse,
    summary="Bandeja de coherencia (ANALYST+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def list_coherence_alerts(
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: GraphGateway = Depends(get_graph_gateway),
    status_filter: Optional[AlertStatus] = Query(default=None, alias="status"),
) -> AlertListResponse:
    """Lista/filtra las alertas de coherencia (SPEC-01 §5.4)."""
    payload = gateway.list_alerts(
        status=status_filter.value if status_filter else None
    )
    return AlertListResponse(**payload)


# =====================================================================
# PATCH /coherence/alerts/{alert_id}/resolve — resolveCoherenceAlert (ANALYST+)
# =====================================================================
@router.patch(
    "/alerts/{alert_id}/resolve",
    operation_id="resolveCoherenceAlert",
    response_model=CoherenceAlert,
    summary="Resuelve o descarta una alerta (ANALYST+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def resolve_coherence_alert(
    alert_id: str,
    payload: CoherenceAlertResolve,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> CoherenceAlert:
    """Resuelve o descarta una alerta (decisión humana, SPEC-04 §1)."""
    result = gateway.resolve_alert(
        alert_id, payload.status.value, payload.resolution_note
    )
    if result is None:
        raise _not_found(alert_id)
    return CoherenceAlert(**result)


__all__ = [
    "router",
    "CoherenceAlert",
    "CoherenceAlertResolve",
    "AlertListResponse",
    "AlertType",
    "AlertStatus",
    "ResolveStatus",
]
