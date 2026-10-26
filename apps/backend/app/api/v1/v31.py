"""Rutas v3.1 — correlación, subgrafo, evasión, propuestas y evidencia.

Zona: Agente 3 — ``apps/backend/app/api/**``.

Autorizado por las specs v3.1 aprobadas por el usuario:
``SPEC-02 §11.5`` (propuestas), ``SPEC-03 §3–§5`` (consultas) y
``SPEC-04 §4`` (inspector de evidencia). Los ``operationId`` coinciden
exactamente con ``contracts/openapi.yaml`` y con la matriz RBAC.

Reglas de seguridad que se aplican aquí, no en el cliente:

* el TLP solicitado nunca supera el clearance del rol;
* ``include_staged`` sólo lo puede pedir ANALYST/ADMIN;
* una propuesta se muta con ``version`` obligatoria (optimistic locking);
* una aprobación **no** escribe el grafo: sólo autoriza; ``apply`` lo ejecuta
  el worker de ATENEA fuera de esta API.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.deps import get_v31_gateway
from app.api.v31_gateway import V31Gateway
from app.core.errors import CODE_NOT_FOUND, detail_from_code
from app.core.rbac import HUMAN_ROLES, Role, require_role
from app.core.users import Principal
from app.services.correlation import can_read

router = APIRouter()

#: Clearance máximo por rol humano. No se eleva sin una spec de seguridad.
ROLE_CLEARANCE: dict[Role, str] = {
    Role.VIEWER: "TLP:GREEN",
    Role.ANALYST: "TLP:AMBER",
    Role.ADMIN: "TLP:RED",
}


class Discipline(str, Enum):
    OSINT = "OSINT"
    SOCMINT = "SOCMINT"
    SIGINT = "SIGINT"
    HUMINT = "HUMINT"
    TECHINT = "TECHINT"
    FININT = "FININT"
    GEOINT = "GEOINT"
    MIXED = "MIXED"


class TLP(str, Enum):
    CLEAR = "TLP:CLEAR"
    GREEN = "TLP:GREEN"
    AMBER = "TLP:AMBER"
    AMBER_STRICT = "TLP:AMBER+STRICT"
    RED = "TLP:RED"


class Direction(str, Enum):
    OUT = "OUT"
    IN = "IN"
    BOTH = "BOTH"


class ProposalStatus(str, Enum):
    AGENT_STAGED = "AGENT_STAGED"
    ANALYST_VERIFIED = "ANALYST_VERIFIED"
    APPLIED = "APPLIED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ProposalType(str, Enum):
    OBSERVABLE = "OBSERVABLE"
    RELATIONSHIP = "RELATIONSHIP"
    EVENT = "EVENT"
    ENTITY_MERGE = "ENTITY_MERGE"
    ALIAS = "ALIAS"


class GeoPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class TimeWindow(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: datetime = Field(alias="from")
    to: datetime

    @model_validator(mode="after")
    def _ordered(self) -> "TimeWindow":
        if self.to <= self.from_:
            raise ValueError("window.to debe ser posterior a window.from.")
        return self

    @property
    def seconds(self) -> float:
        return (self.to - self.from_).total_seconds()


class ColocationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_id: UUID
    center: GeoPoint
    radius_m: float = Field(default=500, ge=10, le=5000)
    window: TimeWindow
    disciplines: list[Discipline] = Field(min_length=2)
    min_confidence: int = Field(default=60, ge=0, le=100)
    max_tlp: Optional[TLP] = None
    max_time_delta_s: int = Field(default=900, ge=0, le=86400)
    limit: int = Field(default=200, ge=1, le=500)

    @model_validator(mode="after")
    def _disciplines_and_window(self) -> "ColocationRequest":
        keys = {d.value for d in self.disciplines}
        if "SIGINT" not in keys:
            raise ValueError("disciplines debe incluir SIGINT.")
        if not keys & {"SOCMINT", "OSINT"}:
            raise ValueError("disciplines debe incluir SOCMINT u OSINT.")
        if self.window.seconds > 30 * 24 * 3600:
            raise ValueError("La ventana de colocalización no puede exceder 30 días.")
        return self


class ColocationEventOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    event_id: str
    discipline: Discipline
    event_type: str
    event_time: datetime
    distance_m: Optional[float] = None
    confidence: int = Field(ge=0, le=100)
    tlp: TLP
    entity_id: Optional[str] = None
    observable_id: Optional[str] = None
    masked_selector: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)


class ColocationClusterOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    cluster_id: str
    center: GeoPoint
    radius_m: float
    score: float = Field(ge=0, le=1)
    disciplines: list[Discipline] = Field(default_factory=list)
    events: list[ColocationEventOut] = Field(default_factory=list)
    time_delta_s: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)


class ColocationResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    query: dict[str, Any] = Field(default_factory=dict)
    clusters: list[ColocationClusterOut] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: Optional[str] = None
    suppressed: int = 0


class SubgraphRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_id: UUID
    root_ids: list[UUID] = Field(min_length=1, max_length=20)
    depth: int = Field(default=2, ge=1, le=5)
    direction: Direction = Direction.BOTH
    disciplines: list[Discipline] = Field(default_factory=list)
    admiralty_min: Optional[str] = None
    admiralty_max: Optional[str] = None
    min_confidence: int = Field(default=0, ge=0, le=100)
    max_tlp: Optional[TLP] = None
    as_of: Optional[datetime] = None
    include_staged: bool = False
    max_nodes: int = Field(default=300, ge=1, le=500)
    max_edges: int = Field(default=1200, ge=1, le=2000)
    cursor: Optional[str] = None

    @field_validator("admiralty_min", "admiralty_max")
    @classmethod
    def _admiralty_code(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        text = value.strip().upper()
        if len(text) != 2 or text[0] not in "ABCDEF" or text[1] not in "123456":
            raise ValueError("Código de Almirantazgo inválido (ej. B2).")
        return text


class SubgraphNodeOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    entity_id: str
    stix_id: Optional[str] = None
    stix_type: Optional[str] = None
    canonical_name: str
    primary_discipline: Optional[Discipline] = None
    admiralty: Optional[str] = None
    confidence: int = Field(default=0, ge=0, le=100)
    tlp: Optional[TLP] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    depth: int = 0
    observables: list[dict[str, Any]] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)


class SubgraphEdgeOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: str
    discipline: Optional[Discipline] = None
    confidence: int = Field(default=0, ge=0, le=100)
    admiralty: Optional[str] = None
    tlp: Optional[TLP] = None
    valid_from: Optional[datetime] = None
    valid_until: Optional[datetime] = None
    event_id: Optional[str] = None
    evidence_ids: list[str] = Field(default_factory=list)


class SubgraphResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    nodes: list[SubgraphNodeOut] = Field(default_factory=list)
    edges: list[SubgraphEdgeOut] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: Optional[str] = None
    as_of: Optional[datetime] = None


class AliasEvasionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mission_id: UUID
    entity_id: Optional[UUID] = None
    window: TimeWindow
    min_aliases: int = Field(default=3, ge=2, le=50)
    min_platforms: int = Field(default=2, ge=1, le=20)
    max_gap_hours: float = Field(default=48, gt=0, le=720)
    similarity_threshold: float = Field(default=0.55, ge=0, le=1)
    max_tlp: Optional[TLP] = None
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def _window_limit(self) -> "AliasEvasionRequest":
        if self.window.seconds > 90 * 24 * 3600:
            raise ValueError("La ventana de alias no puede exceder 90 días.")
        return self


class AliasSignalOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    signal_id: str
    signal_type: str
    entity_id: Optional[str] = None
    observable_id: Optional[str] = None
    score: float = Field(ge=0, le=1)
    occurrences: int = 0
    max_gap_hours: Optional[float] = None
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    aliases: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    explanation: str = ""


class AliasEvasionResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    signals: list[AliasSignalOut] = Field(default_factory=list)
    truncated: bool = False
    suppressed: int = 0


class ProposalOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    proposal_id: str
    proposal_type: ProposalType
    status: ProposalStatus
    mission_id: Optional[str] = None
    agent_type: Optional[str] = None
    target_entity_id: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    confidence: int = Field(default=0, ge=0, le=100)
    version: int = 1
    created_at: Optional[datetime] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    decision_reason: Optional[str] = None


class ProposalListResponse(BaseModel):
    items: list[ProposalOut] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: Optional[str] = None


class ProposalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=8, max_length=2000)
    version: int = Field(ge=1)


class EvidenceOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_id: str
    payload_sha256: str
    tlp: TLP
    collector_agent: Optional[str] = None
    source_type: Optional[str] = None
    source_uri: Optional[str] = None
    captured_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceVerifyResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    evidence_id: str
    valid: bool
    stored_sha256: Optional[str] = None
    computed_sha256: Optional[str] = None
    chain_valid: bool = False
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def clearance_for(principal: Principal) -> str:
    """Clearance máximo del rol; fail-closed para roles desconocidos."""
    return ROLE_CLEARANCE.get(principal.role, "TLP:CLEAR")


def _enforce_tlp(principal: Principal, requested: Optional[TLP]) -> str:
    clearance = clearance_for(principal)
    if requested is not None and not can_read(clearance, requested.value):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail_from_code(
                "TLP_DENIED",
                f"TLP solicitado {requested.value} excede el clearance {clearance}.",
            ),
        )
    return clearance


def _not_found(resource: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail_from_code(CODE_NOT_FOUND, f"{resource} no encontrado: {identifier}"),
    )


def _conflict(code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail_from_code(code, message),
    )


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if value is not None else {}


# ---------------------------------------------------------------------------
# Consulta 1 — colocalización
# ---------------------------------------------------------------------------
@router.post(
    "/correlation/colocation",
    operation_id="findSigintSocmintColocation",
    response_model=ColocationResponse,
    tags=["correlation"],
    summary="Colocalización SIGINT-SOCMINT/OSINT (VIEWER+)",
    responses={403: {"description": "TLP/rol insuficiente"}},
)
async def find_sigint_socmint_colocation(
    payload: ColocationRequest,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> ColocationResponse:
    """Ejecuta la consulta PostGIS; nunca escribe en el grafo."""
    clearance = _enforce_tlp(user, payload.max_tlp)
    result = gateway.find_colocation(
        mission_id=str(payload.mission_id),
        center={"lat": payload.center.lat, "lon": payload.center.lon},
        radius_m=payload.radius_m,
        window={
            "from": payload.window.from_.isoformat(),
            "to": payload.window.to.isoformat(),
        },
        disciplines=[d.value for d in payload.disciplines],
        min_confidence=payload.min_confidence,
        max_tlp=(payload.max_tlp.value if payload.max_tlp else clearance),
        max_time_delta_s=payload.max_time_delta_s,
        limit=payload.limit,
        clearance=clearance,
        viewer_role=user.role.value,
    )
    return ColocationResponse(**_as_dict(result))


# ---------------------------------------------------------------------------
# Consulta 2 — subgrafo multicapa
# ---------------------------------------------------------------------------
@router.post(
    "/graph/subgraph",
    operation_id="getMultiLayerSubgraph",
    response_model=SubgraphResponse,
    tags=["graph"],
    summary="Subgrafo multicapa con as_of (VIEWER+)",
    responses={403: {"description": "TLP/rol insuficiente"}},
)
async def get_multi_layer_subgraph(
    payload: SubgraphRequest,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> SubgraphResponse:
    """Expande raíces con filtros de disciplina, Almirantazgo, TLP y fecha."""
    if payload.include_staged and user.role is Role.VIEWER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=detail_from_code(
                "FORBIDDEN", "include_staged requiere rol ANALYST o ADMIN."
            ),
        )
    clearance = _enforce_tlp(user, payload.max_tlp)
    result = gateway.get_subgraph(
        mission_id=str(payload.mission_id),
        root_ids=[str(r) for r in payload.root_ids],
        depth=payload.depth,
        direction=payload.direction.value,
        disciplines=[d.value for d in payload.disciplines],
        admiralty_min=payload.admiralty_min,
        admiralty_max=payload.admiralty_max,
        min_confidence=payload.min_confidence,
        max_tlp=(payload.max_tlp.value if payload.max_tlp else clearance),
        as_of=(payload.as_of or datetime.now(timezone.utc)).isoformat(),
        include_staged=payload.include_staged,
        max_nodes=payload.max_nodes,
        max_edges=payload.max_edges,
        cursor=payload.cursor,
        clearance=clearance,
    )
    return SubgraphResponse(**_as_dict(result))


# ---------------------------------------------------------------------------
# Consulta 3 — evasión de alias
# ---------------------------------------------------------------------------
@router.post(
    "/analytics/alias-evasion",
    operation_id="detectAliasEvasion",
    response_model=AliasEvasionResponse,
    tags=["analytics"],
    summary="Señales de rotación/colisión de alias (VIEWER+)",
    responses={403: {"description": "TLP/rol insuficiente"}},
)
async def detect_alias_evasion(
    payload: AliasEvasionRequest,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> AliasEvasionResponse:
    """Devuelve señales explicables; no crea ``ALIAS_OF`` automáticamente."""
    clearance = _enforce_tlp(user, payload.max_tlp)
    result = gateway.detect_alias_evasion(
        mission_id=str(payload.mission_id),
        entity_id=(str(payload.entity_id) if payload.entity_id else None),
        window={
            "from": payload.window.from_.isoformat(),
            "to": payload.window.to.isoformat(),
        },
        min_aliases=payload.min_aliases,
        min_platforms=payload.min_platforms,
        max_gap_hours=payload.max_gap_hours,
        similarity_threshold=payload.similarity_threshold,
        max_tlp=(payload.max_tlp.value if payload.max_tlp else clearance),
        limit=payload.limit,
        clearance=clearance,
    )
    return AliasEvasionResponse(**_as_dict(result))


# ---------------------------------------------------------------------------
# Propuestas (SPEC-02 §11.5)
# ---------------------------------------------------------------------------
@router.get(
    "/proposals",
    operation_id="listProposals",
    response_model=ProposalListResponse,
    tags=["proposals"],
    summary="Lista propuestas de agente (ANALYST+)",
)
async def list_proposals(
    status_filter: Optional[ProposalStatus] = Query(default=None, alias="status"),
    mission_id: Optional[UUID] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    cursor: Optional[str] = Query(default=None),
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> ProposalListResponse:
    result = gateway.list_proposals(
        status=(status_filter.value if status_filter else None),
        mission_id=(str(mission_id) if mission_id else None),
        limit=limit,
        cursor=cursor,
        clearance=clearance_for(user),
        reviewer_role=user.role.value,
    )
    return ProposalListResponse(**_as_dict(result))


@router.get(
    "/proposals/{proposal_id}",
    operation_id="getProposal",
    response_model=ProposalOut,
    tags=["proposals"],
    summary="Propuesta + evidencia + diff (ANALYST+)",
)
async def get_proposal(
    proposal_id: UUID,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> ProposalOut:
    result = gateway.get_proposal(
        str(proposal_id), clearance=clearance_for(user), reviewer_role=user.role.value
    )
    if result is None:
        raise _not_found("Propuesta", str(proposal_id))
    return ProposalOut(**result)


@router.post(
    "/proposals/{proposal_id}/approve",
    operation_id="approveProposal",
    response_model=ProposalOut,
    tags=["proposals"],
    summary="Autoriza una propuesta (ANALYST+; no escribe el grafo)",
)
async def approve_proposal(
    proposal_id: UUID,
    payload: ProposalDecision,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> ProposalOut:
    try:
        result = gateway.approve_proposal(
            str(proposal_id),
            reviewer_id=user.id,
            reason=payload.reason,
            version=payload.version,
            clearance=clearance_for(user),
        )
    except Exception as exc:
        if type(exc).__name__ == "ProposalVersionConflict":
            raise _conflict("PROPOSAL_VERSION_CONFLICT", str(exc)) from exc
        raise
    if result is None:
        raise _not_found("Propuesta", str(proposal_id))
    return ProposalOut(**result)


@router.post(
    "/proposals/{proposal_id}/reject",
    operation_id="rejectProposal",
    response_model=ProposalOut,
    tags=["proposals"],
    summary="Rechaza una propuesta con motivo (ANALYST+)",
)
async def reject_proposal(
    proposal_id: UUID,
    payload: ProposalDecision,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> ProposalOut:
    try:
        result = gateway.reject_proposal(
            str(proposal_id),
            reviewer_id=user.id,
            reason=payload.reason,
            version=payload.version,
            clearance=clearance_for(user),
        )
    except Exception as exc:
        if type(exc).__name__ == "ProposalVersionConflict":
            raise _conflict("PROPOSAL_VERSION_CONFLICT", str(exc)) from exc
        raise
    if result is None:
        raise _not_found("Propuesta", str(proposal_id))
    return ProposalOut(**result)


# ---------------------------------------------------------------------------
# Inspector de evidencia (SPEC-04)
# ---------------------------------------------------------------------------
@router.get(
    "/evidence/{evidence_id}",
    operation_id="getEvidence",
    response_model=EvidenceOut,
    tags=["evidence"],
    summary="Metadatos y payload de evidencia (VIEWER+, TLP aplicado)",
)
async def get_evidence(
    evidence_id: UUID,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> EvidenceOut:
    result = gateway.get_evidence(
        str(evidence_id), clearance=clearance_for(user), viewer_role=user.role.value
    )
    if result is None:
        raise _not_found("Evidencia", str(evidence_id))
    return EvidenceOut(**result)


@router.get(
    "/evidence/{evidence_id}/verify",
    operation_id="verifyEvidence",
    response_model=EvidenceVerifyResponse,
    tags=["evidence"],
    summary="Verifica SHA-256 y cadena de custodia (ANALYST+)",
)
async def verify_evidence(
    evidence_id: UUID,
    user: Principal = Depends(require_role(Role.ANALYST)),
    gateway: V31Gateway = Depends(get_v31_gateway),
) -> EvidenceVerifyResponse:
    result = gateway.verify_evidence(
        str(evidence_id), clearance=clearance_for(user), reviewer_role=user.role.value
    )
    if result is None:
        raise _not_found("Evidencia", str(evidence_id))
    return EvidenceVerifyResponse(**result)


__all__ = [
    "router",
    "ROLE_CLEARANCE",
    "AliasEvasionRequest",
    "AliasEvasionResponse",
    "ColocationRequest",
    "ColocationResponse",
    "EvidenceOut",
    "EvidenceVerifyResponse",
    "ProposalDecision",
    "ProposalListResponse",
    "ProposalOut",
    "SubgraphRequest",
    "SubgraphResponse",
    "clearance_for",
]


