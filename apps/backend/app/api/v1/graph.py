"""Rutas de ``/graph`` (tag ``graph`` del contrato).

Autorizado por: **SPEC-03 §3** (rutas del grafo) y **SPEC-04 §1** (matriz RBAC).

| operationId            | Método | Ruta                         | Rol mínimo |
| :--------------------- | :----- | :--------------------------- | :--------- |
| ``listGraphNodes``     | GET    | ``/graph/nodes``             | VIEWER     |
| ``getGraphNode``       | GET    | ``/graph/nodes/{id}``        | VIEWER     |
| ``updateGraphNode``    | PUT    | ``/graph/nodes/{id}``        | ANALYST    |
| ``lockGraphNode``      | POST   | ``/graph/nodes/{id}/lock``   | ANALYST    |
| ``deleteGraphNode``    | DELETE | ``/graph/nodes/{id}``        | ADMIN      |
| ``listGraphEdges``     | GET    | ``/graph/edges``             | VIEWER     |
| ``getGraphNeighborhood``| GET   | ``/graph/neighborhood/{id}`` | VIEWER     |

La dependencia de rol se declara **antes** que la del gateway para que una celda
"No" de la matriz se resuelva con **403** sin tocar la base de datos.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from app.api.deps import get_graph_gateway
from app.api.graph_gateway import GraphGateway
from app.core.errors import CODE_NOT_FOUND, detail_from_code
from app.core.rbac import Role, require_role
from app.core.users import Principal

router = APIRouter(prefix="/graph", tags=["graph"])


# =====================================================================
# Esquemas del contrato (openapi.yaml)
# =====================================================================
class EntityLabel(str, Enum):
    """``EntityLabel`` del contrato (SPEC-01 §1, v2.0.0)."""

    PERSON = "Person"
    ORGANIZATION = "Organization"
    CYBER_ASSET = "CyberAsset"
    LOCATION = "Location"
    EVENT = "Event"
    REPORT = "Report"
    ANALYSIS = "Analysis"
    INTEL_REQUIREMENT = "IntelRequirement"
    COHERENCE_ALERT = "CoherenceAlert"


class RelationshipType(str, Enum):
    """``GraphEdge.type`` del contrato (16 relaciones, v2.0.0)."""

    CONTROLS = "CONTROLS"
    FINANCED = "FINANCED"
    USES_ASSET = "USES_ASSET"
    LOCATED_AT = "LOCATED_AT"
    ASSOCIATED_WITH = "ASSOCIATED_WITH"
    PARTICIPATED_IN = "PARTICIPATED_IN"
    MENTIONED_IN = "MENTIONED_IN"
    INVESTIGATES = "INVESTIGATES"
    HAS_REQUIREMENT = "HAS_REQUIREMENT"
    CITES_ANALYSIS = "CITES_ANALYSIS"
    CORROBORATES = "CORROBORATES"
    CONTRADICTS = "CONTRADICTS"
    EXTENDS = "EXTENDS"
    ANSWERS_REQUIREMENT = "ANSWERS_REQUIREMENT"
    RAISED_FOR = "RAISED_FOR"
    ABOUT = "ABOUT"


class GraphNode(BaseModel):
    """``GraphNode`` del contrato."""

    id: str
    label: EntityLabel
    canonical_name: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    risk_score: Optional[float] = Field(default=None, ge=0, le=1)
    locked_by_analyst: bool = False
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphNodeUpdate(BaseModel):
    """``GraphNodeUpdate`` del contrato (solo campos editables/fusionables)."""

    canonical_name: Optional[str] = None
    aliases: Optional[list[str]] = None
    merge_with: Optional[list[UUID]] = None
    attributes: Optional[dict[str, Any]] = None


class GraphEdge(BaseModel):
    """``GraphEdge`` del contrato (SELLADO v2.0.0).

    Se replica el ``required`` del contrato *al pie de la letra* — incluida la
    trazabilidad (``source_report_id``, ``exact_quote``, ``page_number``) que la
    Constitución (Regla 3) exige en toda arista. Una arista sin procedencia es un
    dato inexistente: si el repositorio devuelve una arista incompleta, el fallo
    es **ruidoso** (Regla 8) en lugar de devolver un dato sin evidencia.
    """

    id: str
    from_: str = Field(alias="from")
    to: str
    type: RelationshipType
    confidence: float = Field(ge=0, le=1)
    source_report_id: str
    exact_quote: str
    page_number: int = Field(ge=1)
    locked_by_analyst: bool = False
    first_seen: datetime
    last_seen: datetime
    #: No forma parte del ``required`` del contrato, pero se propaga si existe.
    text_evidence_span: Optional[str] = None

    model_config = {"populate_by_name": True}


class Subgraph(BaseModel):
    """``Subgraph`` del contrato."""

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)


class NodeListResponse(BaseModel):
    """Respuesta de ``listGraphNodes``."""

    items: list[GraphNode] = Field(default_factory=list)
    next_cursor: Optional[str] = None


class EdgeListResponse(BaseModel):
    """Respuesta de ``listGraphEdges``."""

    items: list[GraphEdge] = Field(default_factory=list)


def _not_found(resource: str, identifier: str) -> HTTPException:
    """``404`` con el sobre de error del contrato."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail_from_code(
            CODE_NOT_FOUND, f"{resource} no encontrado: {identifier}"
        ),
    )


# =====================================================================
# GET /graph/nodes — listGraphNodes (VIEWER+)
# =====================================================================
@router.get(
    "/nodes",
    operation_id="listGraphNodes",
    response_model=NodeListResponse,
    summary="Lista/filtra nodos  (VIEWER+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def list_graph_nodes(
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
    label: Optional[EntityLabel] = Query(default=None),
    q: Optional[str] = Query(
        default=None, max_length=200, description="Búsqueda full-text por nombre/alias"
    ),
    date_from: Optional[datetime] = Query(default=None, alias="from"),
    date_to: Optional[datetime] = Query(default=None, alias="to"),
    min_confidence: Optional[float] = Query(default=None, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[str] = Query(default=None),
) -> NodeListResponse:
    """Lista/filtra nodos (``label``, ``q``, ``from``, ``to``, ``min_confidence``)."""
    payload = gateway.list_nodes(
        label=label.value if label else None,
        q=q,
        from_=date_from.isoformat() if date_from else None,
        to=date_to.isoformat() if date_to else None,
        min_confidence=min_confidence,
        limit=limit,
        cursor=cursor,
    )
    return NodeListResponse(**payload)


# =====================================================================
# GET /graph/nodes/{id} — getGraphNode (VIEWER+)
# =====================================================================
@router.get(
    "/nodes/{id}",
    operation_id="getGraphNode",
    response_model=GraphNode,
    summary="Ficha completa de nodo (VIEWER+)",
    responses={404: {"description": "No encontrado"}},
)
async def get_graph_node(
    id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> GraphNode:
    """Ficha completa de un nodo por ``id``."""
    result = gateway.get_node(id)
    if result is None:
        raise _not_found("Nodo", id)
    return GraphNode(**result)


# =====================================================================
# PUT /graph/nodes/{id} — updateGraphNode (ANALYST+)
# =====================================================================
@router.put(
    "/nodes/{id}",
    operation_id="updateGraphNode",
    response_model=GraphNode,
    summary="Edita/fusiona nodo (ANALYST+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def update_graph_node(
    id: str,
    payload: GraphNodeUpdate,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> GraphNode:
    """Edita o fusiona un nodo existente (``merge_with`` → Entity Resolution)."""
    changes = payload.model_dump(exclude_none=True)
    result = gateway.update_node(id, changes)
    if result is None:
        raise _not_found("Nodo", id)
    return GraphNode(**result)


# =====================================================================
# POST /graph/nodes/{id}/lock — lockGraphNode (ANALYST+)
# =====================================================================
class NodeLockRequest(BaseModel):
    """Cuerpo de ``lockGraphNode`` (``locked`` con default ``true``)."""

    locked: bool = True


@router.post(
    "/nodes/{id}/lock",
    operation_id="lockGraphNode",
    response_model=GraphNode,
    summary="Bloquea un nodo verificado por el analista (ANALYST+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def lock_graph_node(
    id: str,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: GraphGateway = Depends(get_graph_gateway),
    payload: Optional[NodeLockRequest] = None,
) -> GraphNode:
    """Marca ``locked_by_analyst`` (Constitución Regla 2; ``ANALYST``+)."""
    locked = True if payload is None else bool(payload.locked)
    result = gateway.lock_node(id, locked)
    if result is None:
        raise _not_found("Nodo", id)
    return GraphNode(**result)


# =====================================================================
# DELETE /graph/nodes/{id} — deleteGraphNode (ADMIN)
# =====================================================================
@router.delete(
    "/nodes/{id}",
    operation_id="deleteGraphNode",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borra nodo (ADMIN)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def delete_graph_node(
    id: str,
    user: Principal = Depends(require_role(Role.ADMIN)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> Response:
    """Borra un nodo y sus relaciones (solo ADMIN, SPEC-04 §1)."""
    gateway.delete_node(id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =====================================================================
# GET /graph/edges — listGraphEdges (VIEWER+)
# =====================================================================
@router.get(
    "/edges",
    operation_id="listGraphEdges",
    response_model=EdgeListResponse,
    summary="Lista/filtra aristas (VIEWER+)",
)
async def list_graph_edges(
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
    min_confidence: Optional[float] = Query(default=None, ge=0, le=1),
    limit: int = Query(default=50, ge=1, le=500),
) -> EdgeListResponse:
    """Lista/filtra aristas (toda arista conserva su evidencia, Regla 3)."""
    payload = gateway.list_edges(min_confidence=min_confidence, limit=limit)
    return EdgeListResponse(**payload)


# =====================================================================
# GET /graph/neighborhood/{id} — getGraphNeighborhood (VIEWER+)
# =====================================================================
@router.get(
    "/neighborhood/{id}",
    operation_id="getGraphNeighborhood",
    response_model=Subgraph,
    summary="Vecindad a N saltos (VIEWER+)",
)
async def get_graph_neighborhood(
    id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
    depth: int = Query(default=1, ge=1, le=3),
) -> Subgraph:
    """Vecindad a ``depth`` saltos (máximo 3, SPEC-03 §6)."""
    payload = gateway.neighborhood(id, depth)
    return Subgraph(**payload)


__all__ = [
    "router",
    "EntityLabel",
    "RelationshipType",
    "GraphNode",
    "GraphNodeUpdate",
    "GraphEdge",
    "Subgraph",
    "NodeListResponse",
    "EdgeListResponse",
    "NodeLockRequest",
]
