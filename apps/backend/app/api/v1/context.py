"""Rutas de ``/context`` — Pre-Flight Context Check (Fase 0).

Autorizado por: **SPEC-02 §1 Fase 0** (análisis activos + RFIs + diccionario
canónico), **SPEC-03 §5** (``POST /context/preflight``, ``AI_INFORMANT`` ||
``ANALYST``) y ``contracts/openapi.yaml`` SELLADO v2.0.0.

| operationId            | Método | Ruta                    | Actores                    |
| :--------------------- | :----- | :---------------------- | :------------------------- |
| ``postContextPreflight``| POST  | ``/context/preflight``  | ``ANALYST``/``ADMIN``/``AI_INFORMANT`` |

La ruta delega en :class:`app.services.preflight.PreflightService` (Agente 2) y
devuelve el contexto junto con el ``preflight_token`` **efímero** que
``POST /ingest/informant`` exige después (Constitución, Regla 3).
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_preflight_service
from app.core.rbac import Role, require_roles
from app.core.users import Principal

router = APIRouter(prefix="/context", tags=["context"])

_PREFLIGHT_ACTORS = frozenset({Role.ANALYST, Role.AI_INFORMANT})


class PreflightRequest(BaseModel):
    """``PreflightRequest`` del contrato (``keywords`` obligatorias)."""

    keywords: list[str] = Field(min_length=1)
    entity_hints: Optional[list[str]] = None


class PreflightAnalysis(BaseModel):
    """Elemento de ``PreflightResponse.analyses`` (contrato)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    code: Optional[str] = None
    thesis_summary: Optional[str] = None


class PreflightRequirement(BaseModel):
    """Elemento de ``PreflightResponse.requirements`` (contrato)."""

    model_config = ConfigDict(extra="ignore")

    id: str
    question: Optional[str] = None
    priority: Optional[str] = None


class PreflightCanonicalEntity(BaseModel):
    """Elemento de ``PreflightResponse.canonical_entities`` (contrato)."""

    model_config = ConfigDict(extra="ignore")

    id: Optional[str] = None
    label: Optional[str] = None
    canonical_name: Optional[str] = None


class PreflightResponse(BaseModel):
    """``PreflightResponse`` del contrato."""

    preflight_token: str
    analyses: list[PreflightAnalysis] = Field(default_factory=list)
    requirements: list[PreflightRequirement] = Field(default_factory=list)
    canonical_entities: list[PreflightCanonicalEntity] = Field(default_factory=list)


# =====================================================================
# POST /context/preflight — postContextPreflight (ANALYST | AI_INFORMANT)
# =====================================================================
@router.post(
    "/preflight",
    operation_id="postContextPreflight",
    response_model=PreflightResponse,
    summary="Pre-Flight Context Check (Fase 0) (AI_INFORMANT | ANALYST)",
    responses={401: {"description": "No autenticado"}},
)
async def post_context_preflight(
    payload: PreflightRequest,
    principal: Principal = Depends(require_roles(_PREFLIGHT_ACTORS)),
    service: Any = Depends(get_preflight_service),
) -> PreflightResponse:
    """Devuelve el contexto de la Fase 0 + ``preflight_token`` (SPEC-03 §5)."""
    result = service.response(
        payload.keywords, entity_hints=payload.entity_hints
    )
    return PreflightResponse(**result)


__all__ = [
    "router",
    "PreflightRequest",
    "PreflightResponse",
    "PreflightAnalysis",
    "PreflightRequirement",
    "PreflightCanonicalEntity",
]
