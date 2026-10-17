"""Rutas de ``/analyses`` — expedientes analíticos y RFIs.

Autorizado por: **SPEC-03 §6** (expedientes y requerimientos), **SPEC-04 §1**
(``Analysis``/``IntelRequirement``: ``ANALYST``+) y ``contracts/openapi.yaml``
SELLADO v2.1.0.

| operationId              | Método | Ruta                            | Rol mínimo |
| :----------------------- | :----- | :------------------------------ | :--------- |
| ``listAnalyses``         | GET    | ``/analyses``                   | VIEWER     |
| ``createAnalysis``       | POST   | ``/analyses``                   | ANALYST    |
| ``getAnalysis``          | GET    | ``/analyses/{id}``              | VIEWER     |
| ``updateAnalysis``       | PUT    | ``/analyses/{id}``              | ANALYST    |
| ``listAnalysisRequirements``| GET | ``/analyses/{id}/requirements`` | VIEWER     |
| ``createIntelRequirement``| POST  | ``/analyses/{id}/requirements`` | ANALYST    |

Solo un analista humano crea/edita la tesis; el motor de coherencia **nunca**
toca ``conclusion_body`` (Reglas 7/9). La persistencia se delega en el
repositorio del Agente 1 vía :class:`app.api.graph_gateway.GraphGateway`.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_graph_gateway
from app.api.graph_gateway import GraphGateway
from app.core.errors import CODE_NOT_FOUND, detail_from_code
from app.core.rbac import Role, require_role
from app.core.users import Principal

router = APIRouter(prefix="/analyses", tags=["analyses"])


class AnalysisStatus(str, Enum):
    """``Analysis.status`` del contrato."""

    ACTIVE = "ACTIVE"
    UNDER_REVIEW = "UNDER_REVIEW"
    CLOSED = "CLOSED"


class RequirementPriority(str, Enum):
    """``IntelRequirement.priority`` del contrato."""

    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class RequirementStatus(str, Enum):
    """``IntelRequirement.status`` del contrato."""

    OPEN = "OPEN"
    ANSWERED = "ANSWERED"


class Analysis(BaseModel):
    """``Analysis`` del contrato."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: Optional[str] = None
    title: Optional[str] = None
    thesis_summary: Optional[str] = None
    conclusion_body: Optional[str] = None
    status: Optional[AnalysisStatus] = None
    author_analyst_id: Optional[str] = None
    coherence_score: Optional[float] = Field(default=None, ge=0, le=1)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AnalysisCreate(BaseModel):
    """``AnalysisCreate`` del contrato."""

    code: str = Field(min_length=1)
    title: str = Field(min_length=1)
    thesis_summary: str = Field(min_length=1)
    conclusion_body: Optional[str] = None


class AnalysisUpdate(BaseModel):
    """``AnalysisUpdate`` del contrato (solo campos editables)."""

    title: Optional[str] = None
    thesis_summary: Optional[str] = None
    conclusion_body: Optional[str] = None
    status: Optional[AnalysisStatus] = None


class IntelRequirement(BaseModel):
    """``IntelRequirement`` del contrato."""

    model_config = ConfigDict(extra="ignore")

    id: str
    question: Optional[str] = None
    priority: Optional[RequirementPriority] = None
    status: Optional[RequirementStatus] = None
    parent_analysis_id: Optional[str] = None
    target_entity_id: Optional[str] = None


class IntelRequirementCreate(BaseModel):
    """``IntelRequirementCreate`` del contrato."""

    question: str = Field(min_length=1)
    priority: RequirementPriority
    target_entity_id: Optional[str] = None


class AnalysisList(BaseModel):
    """Envoltura ``{items: [Analysis]}`` de ``listAnalyses`` (contrato §6)."""

    items: list[Analysis] = Field(default_factory=list)


class IntelRequirementList(BaseModel):
    """Envoltura ``{items: [IntelRequirement]}`` de ``listAnalysisRequirements``."""

    items: list[IntelRequirement] = Field(default_factory=list)


def _not_found(resource: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail_from_code(
            CODE_NOT_FOUND, f"{resource} no encontrado: {identifier}"
        ),
    )


# =====================================================================
# GET /analyses — listAnalyses (VIEWER+)
# =====================================================================
@router.get(
    "",
    operation_id="listAnalyses",
    response_model=AnalysisList,
    summary="Lista expedientes analíticos, filtrable por estado (VIEWER+)",
    responses={
        403: {"description": "Rol insuficiente (RBAC SPEC-04)"},
    },
)
async def list_analyses(
    status: Optional[AnalysisStatus] = Query(
        default=None, description="Filtra por estado del expediente."
    ),
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> AnalysisList:
    """Lista los ``Analysis`` visibles; ``?status`` acota por estado (SPEC-03 §6)."""
    payload = gateway.list_analyses(status.value if status is not None else None)
    return AnalysisList(items=[Analysis(**item) for item in payload["items"]])


# =====================================================================
# POST /analyses — createAnalysis (ANALYST+)
# =====================================================================
@router.post(
    "",
    operation_id="createAnalysis",
    response_model=Analysis,
    status_code=status.HTTP_201_CREATED,
    summary="Crea un expediente analítico (ANALYST+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def create_analysis(
    payload: AnalysisCreate,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> Analysis:
    """Crea un ``Analysis`` con el analista autenticado como autor."""
    result = gateway.create_analysis(
        title=payload.title,
        thesis_summary=payload.thesis_summary,
        code=payload.code,
        author_analyst_id=user.id,
        conclusion_body=payload.conclusion_body,
    )
    return Analysis(**result)


# =====================================================================
# GET /analyses/{id} — getAnalysis (VIEWER+)
# =====================================================================
@router.get(
    "/{id}",
    operation_id="getAnalysis",
    response_model=Analysis,
    summary="Ficha del expediente analítico (VIEWER+)",
    responses={404: {"description": "No encontrado"}},
)
async def get_analysis(
    id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> Analysis:
    """Ficha completa del expediente por ``id``."""
    result = gateway.get_analysis(id)
    if result is None:
        raise _not_found("Expediente", id)
    return Analysis(**result)


# =====================================================================
# PUT /analyses/{id} — updateAnalysis (ANALYST+)
# =====================================================================
@router.put(
    "/{id}",
    operation_id="updateAnalysis",
    response_model=Analysis,
    summary="Edita tesis/conclusión/estado (ANALYST+; única autoridad humana)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def update_analysis(
    id: str,
    payload: AnalysisUpdate,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> Analysis:
    """Edita la tesis, conclusión o estado de un expediente."""
    changes = payload.model_dump(exclude_none=True, mode="json")
    result = gateway.update_analysis(id, changes)
    if result is None:
        raise _not_found("Expediente", id)
    return Analysis(**result)


# =====================================================================
# POST /analyses/{id}/requirements — createIntelRequirement (ANALYST+)
# =====================================================================
@router.post(
    "/{id}/requirements",
    operation_id="createIntelRequirement",
    response_model=IntelRequirement,
    status_code=status.HTTP_201_CREATED,
    summary="Crea un requerimiento de inteligencia (RFI) (ANALYST+)",
    responses={
        403: {"description": "Rol insuficiente (RBAC SPEC-04)"},
        404: {"description": "Expediente no encontrado (SPEC-03 §6 v2.0.1)"},
    },
)
async def create_intel_requirement(
    id: str,
    payload: IntelRequirementCreate,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> IntelRequirement:
    """Crea un ``IntelRequirement`` vinculado al expediente ``id``.

    Integridad referencial (SPEC-03 §6 v2.0.1): el ``Analysis`` ``{id}`` debe
    existir; si no, responde **404** (nunca 500 por un ``MATCH`` fallido). La
    comprobación se delega en el gateway/repositorio ya disponible.
    """
    if gateway.get_analysis(id) is None:
        raise _not_found("Expediente", id)
    result = gateway.create_requirement(
        question=payload.question,
        priority=payload.priority.value,
        parent_analysis_id=id,
        target_entity_id=payload.target_entity_id,
    )
    return IntelRequirement(**result)


# =====================================================================
# GET /analyses/{id}/requirements — listAnalysisRequirements (VIEWER+)
# =====================================================================
@router.get(
    "/{id}/requirements",
    operation_id="listAnalysisRequirements",
    response_model=IntelRequirementList,
    summary="Lista los RFI del expediente (VIEWER+)",
    responses={
        403: {"description": "Rol insuficiente (RBAC SPEC-04)"},
        404: {"description": "Expediente no encontrado (SPEC-03 §6)"},
    },
)
async def list_analysis_requirements(
    id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> IntelRequirementList:
    """RFI vinculados al expediente ``id``; 404 si el expediente no existe."""
    if gateway.get_analysis(id) is None:
        raise _not_found("Expediente", id)
    payload = gateway.list_requirements(id)
    return IntelRequirementList(
        items=[IntelRequirement(**item) for item in payload["items"]]
    )


__all__ = [
    "router",
    "Analysis",
    "AnalysisCreate",
    "AnalysisUpdate",
    "AnalysisStatus",
    "IntelRequirement",
    "IntelRequirementCreate",
    "RequirementPriority",
    "RequirementStatus",
]
