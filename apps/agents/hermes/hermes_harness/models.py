"""Contratos Pydantic v2 del harness Hermes (SPEC-02 §3–§8).

Zona: Agente 5 — ``apps/agents/**``.

Estos modelos son la frontera entre el LLM y el gateway. El LLM **nunca** toca
SQL ni credenciales: sólo puede emitir uno de estos envelopes; el gateway valida
``extra="forbid"`` antes de ejecutar cualquier tool.

Reglas que no se relajan:
* ``search_existing_entities`` es read-only;
* las tools de ingesta exigen ``evidence`` y ``idempotency_key``;
* SIGINT impone ``discipline=SIGINT`` y selector de sensor;
* ``check_opsec`` es obligatorio antes de una interacción HUMINT.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Admiralty = Annotated[str, StringConstraints(pattern=r"^[A-F][1-6]$")]
Tlp = Literal["TLP:RED", "TLP:AMBER+STRICT", "TLP:AMBER", "TLP:GREEN", "TLP:CLEAR"]
Discipline = Literal[
    "OSINT", "SOCMINT", "SIGINT", "HUMINT", "TECHINT", "FININT", "GEOINT", "MIXED"
]
AgentId = Literal["HERMES", "ARACNE", "ARGOS"]
ObservableType = Literal[
    "ACCOUNT_ID", "USERNAME", "DISPLAY_NAME", "PHONE_E164", "EMAIL",
    "WALLET", "DOMAIN", "IPV4", "IPV6", "URL", "MAC_ADDRESS",
    "IMSI", "IMEI", "ICCID", "MSISDN", "DEVICE_FINGERPRINT",
    "TELEGRAM_ID", "X_USER_ID", "DISCORD_ID", "SIGNAL_ID",
    "COORDINATE", "OTHER",
]


class StrictModel(BaseModel):
    """Base de todos los envelopes: no admite campos no declarados."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class GeoPoint(StrictModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0)


class EvidenceRef(StrictModel):
    evidence_id: UUID
    payload_sha256: Sha256
    captured_at: datetime
    tlp: Tlp


class SourceRef(StrictModel):
    platform: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    uri: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    source_type: Literal[
        "OSINT", "SOCMINT", "SIGINT", "HUMINT", "MANUAL", "PUBLIC_REGISTRY", "LEGACY"
    ]
    collection_method: Annotated[str | None, StringConstraints(max_length=256)] = None
    legal_basis: Annotated[str, StringConstraints(min_length=8, max_length=1024)]


class AliasInput(StrictModel):
    alias_type: Literal[
        "HANDLE", "DISPLAY_NAME", "PHONE", "EMAIL", "WALLET", "URL",
        "ACCOUNT_ID", "DEVICE_ID", "OTHER",
    ]
    value: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    platform: Annotated[str | None, StringConstraints(max_length=128)] = None
    observed_from: datetime
    observed_until: datetime | None = None

    @field_validator("observed_until")
    @classmethod
    def _window(cls, value: datetime | None, info: Any) -> datetime | None:
        start = info.data.get("observed_from")
        if value is not None and start is not None and value <= start:
            raise ValueError("observed_until debe ser > observed_from")
        return value


class SelectorQuery(StrictModel):
    observable_type: ObservableType
    value: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class EntityQuery(StrictModel):
    selector: SelectorQuery | None = None
    name: Annotated[str | None, StringConstraints(min_length=1, max_length=512)] = None
    text: Annotated[str | None, StringConstraints(min_length=2, max_length=4096)] = None

    @model_validator(mode="after")
    def _one_query(self) -> "EntityQuery":
        supplied = sum(v is not None for v in (self.selector, self.name, self.text))
        if supplied != 1:
            raise ValueError("use exactamente uno de selector, name o text")
        return self


class SearchExistingEntities(StrictModel):
    tool_call_id: UUID
    agent_id: AgentId
    run_id: UUID
    mission_id: UUID
    query: EntityQuery
    disciplines: list[Discipline] = Field(default_factory=list)
    min_score: float = Field(default=0.35, ge=0, le=1)
    limit: int = Field(default=10, ge=1, le=50)
    as_of: datetime | None = None


class IngestMultiIntObservable(StrictModel):
    tool_call_id: UUID
    agent_id: AgentId
    run_id: UUID
    mission_id: UUID
    pir_id: UUID
    discipline: Discipline
    observable_type: ObservableType
    value: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    normalized_value: Annotated[str | None, StringConstraints(max_length=4096)] = None
    observed_at: datetime
    source: SourceRef
    evidence: EvidenceRef
    tlp: Tlp
    admiralty: Admiralty
    confidence_hint: float | None = Field(default=None, ge=0, le=1)
    aliases: list[AliasInput] = Field(default_factory=list, max_length=100)
    attributes: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Sha256


class SigintSelector(StrictModel):
    observable_type: Literal[
        "IMSI", "IMEI", "ICCID", "MSISDN", "MAC_ADDRESS",
        "DEVICE_FINGERPRINT", "COORDINATE", "OTHER",
    ]
    value: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class RecordSigintTelemetry(StrictModel):
    tool_call_id: UUID
    agent_id: AgentId
    run_id: UUID
    mission_id: UUID
    pir_id: UUID
    event_type: Literal[
        "RF_EMISSION", "IMSI_ATTACH", "MAC_OBSERVATION", "CALL", "SMS"
    ]
    selector: SigintSelector
    frequency_hz: float | None = Field(default=None, ge=0)
    bandwidth_hz: float | None = Field(default=None, ge=0)
    modulation: Annotated[str | None, StringConstraints(max_length=64)] = None
    duration_ms: int | None = Field(default=None, ge=0)
    location: GeoPoint | None = None
    observed_at: datetime
    device_fingerprint: Annotated[str | None, StringConstraints(max_length=512)] = None
    evidence: EvidenceRef
    admiralty: Admiralty
    tlp: Tlp
    attributes: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Sha256


class ProposeRelationship(StrictModel):
    tool_call_id: UUID
    agent_id: AgentId
    run_id: UUID
    mission_id: UUID
    source_entity_id: UUID
    target_entity_id: UUID
    relationship_type: Literal[
        "CONTROLS_ACCOUNT", "COMMUNICATES_WITH", "FINANCED_BY", "LOCATED_AT",
        "ALIAS_OF", "AFFILIATED_WITH", "MEMBER_OF", "OWNS", "USES",
        "TARGETS", "PARTICIPATED_IN", "OBSERVED_AT", "MENTIONS", "RELATED_TO",
    ]
    discipline: Discipline
    valid_from: datetime
    valid_until: datetime | None = None
    event_id: UUID | None = None
    admiralty: Admiralty
    confidence_hint: float = Field(ge=0, le=1)
    evidence_ids: list[UUID] = Field(min_length=1, max_length=50)
    tlp: Tlp
    properties: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: Sha256

    @field_validator("valid_until")
    @classmethod
    def _window(cls, value: datetime | None, info: Any) -> datetime | None:
        start = info.data.get("valid_from")
        if value is not None and start is not None and value <= start:
            raise ValueError("valid_until debe ser > valid_from")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("evidence_ids no puede contener duplicados")
        return value


class CheckOpsec(StrictModel):
    tool_call_id: UUID
    agent_id: Literal["HERMES"]
    run_id: UUID
    mission_id: UUID
    sockpuppet_id: UUID
    interaction_id: UUID
    action: Literal["SEND_MESSAGE", "JOIN_CHANNEL", "PROFILE_VIEW", "CALL", "OTHER"]


#: Mapa nombre de tool → modelo. Es la allowlist de ejecución del gateway.
TOOL_MODELS: dict[str, type[StrictModel]] = {
    "search_existing_entities": SearchExistingEntities,
    "ingest_multi_int_observable": IngestMultiIntObservable,
    "record_sigint_telemetry": RecordSigintTelemetry,
    "propose_relationship": ProposeRelationship,
    "check_opsec": CheckOpsec,
}


class ToolEnvelope(StrictModel):
    """Envelope genérico que el LLM puede emitir antes de validar argumentos.

    ``tool_name`` es ``str`` a propósito: un nombre fuera de la allowlist debe
    producir ``TOOL_NOT_ALLOWED`` (403), no un error de esquema genérico. El JSON
    Schema del contrato sigue restringiendo el enum para el cliente.
    """

    tool_call_id: UUID
    agent_id: AgentId
    run_id: UUID
    mission_id: UUID
    tool_name: str
    arguments: dict[str, Any]
    requested_at: datetime | None = None


class ToolError(StrictModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateResult(StrictModel):
    entity_id: UUID
    canonical_name: str = ""
    score: float = Field(ge=0, le=1)
    reason: Literal["EXACT_SELECTOR", "ALIAS_HISTORY", "TRIGRAM", "EMBEDDING", "HUMAN"]


ToolStatus = Literal[
    "OK", "STAGED", "DUPLICATE", "NEEDS_REVIEW", "REJECTED", "ABORTED", "ERROR"
]


class ToolResult(StrictModel):
    tool_call_id: UUID
    status: ToolStatus
    data: dict[str, Any] = Field(default_factory=dict)
    proposal_id: UUID | None = None
    evidence_id: UUID | None = None
    entity_id: UUID | None = None
    candidates: list[CandidateResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: ToolError | None = None


__all__ = [
    "AgentId",
    "AliasInput",
    "CandidateResult",
    "CheckOpsec",
    "Discipline",
    "EntityQuery",
    "EvidenceRef",
    "GeoPoint",
    "IngestMultiIntObservable",
    "ObservableType",
    "ProposeRelationship",
    "RecordSigintTelemetry",
    "SearchExistingEntities",
    "SelectorQuery",
    "Sha256",
    "SigintSelector",
    "SourceRef",
    "StrictModel",
    "Tlp",
    "ToolEnvelope",
    "ToolError",
    "TOOL_MODELS",
    "ToolResult",
    "ToolStatus",
]
