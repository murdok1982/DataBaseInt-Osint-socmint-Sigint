"""Cálculo y verificación de ``idempotency_key`` (SPEC-02 §9)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from .models import (
    IngestMultiIntObservable,
    ProposeRelationship,
    RecordSigintTelemetry,
)


class IdempotencyKeyMismatch(ValueError):
    """La clave enviada no coincide con el canonical recalculado."""


def _dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace(
        "+00:00", "Z"
    )


def canonical_json_bytes(value: Any) -> bytes:
    """JSON canónico estable (claves ordenadas, UTF-8, sin espacios)."""

    def default(item: Any) -> Any:
        if isinstance(item, datetime):
            return _dt(item)
        if hasattr(item, "value"):
            return item.value
        return str(item)

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=default,
    ).encode("utf-8")


def sha256_hex(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def idempotency_key_for_ingest(model: IngestMultiIntObservable) -> str:
    normalized = model.normalized_value
    canonical = {
        "agent": model.agent_id,
        "mission_id": str(model.mission_id),
        "pir_id": str(model.pir_id),
        "discipline": model.discipline,
        "observable_type": model.observable_type,
        "normalized_value": normalized,
        "observed_at": _dt(model.observed_at),
        "source_uri": model.source.uri,
        "evidence_sha256": model.evidence.payload_sha256,
    }
    return sha256_hex(canonical_json_bytes(canonical))


def idempotency_key_for_sigint(model: RecordSigintTelemetry) -> str:
    canonical = {
        "agent": model.agent_id,
        "mission_id": str(model.mission_id),
        "pir_id": str(model.pir_id),
        "event_type": model.event_type,
        "selector_type": model.selector.observable_type,
        "selector_value": model.selector.value,
        "observed_at": _dt(model.observed_at),
        "evidence_sha256": model.evidence.payload_sha256,
    }
    return sha256_hex(canonical_json_bytes(canonical))


def idempotency_key_for_relationship(model: ProposeRelationship) -> str:
    canonical = {
        "agent": model.agent_id,
        "mission_id": str(model.mission_id),
        "source_entity_id": str(model.source_entity_id),
        "target_entity_id": str(model.target_entity_id),
        "relationship_type": model.relationship_type,
        "valid_from": _dt(model.valid_from),
        "event_id": str(model.event_id) if model.event_id else None,
        "evidence_ids": sorted(str(item) for item in model.evidence_ids),
    }
    return sha256_hex(canonical_json_bytes(canonical))


def expected_key(model: Any) -> str:
    if isinstance(model, IngestMultiIntObservable):
        return idempotency_key_for_ingest(model)
    if isinstance(model, RecordSigintTelemetry):
        return idempotency_key_for_sigint(model)
    if isinstance(model, ProposeRelationship):
        return idempotency_key_for_relationship(model)
    raise TypeError(f"Modelo sin idempotencia definida: {type(model).__name__}")


def verify_key(model: Any, supplied: str | None = None) -> str:
    """Recalcula y compara; devuelve la clave esperada."""
    value = supplied if supplied is not None else getattr(model, "idempotency_key", None)
    computed = expected_key(model)
    if value != computed:
        raise IdempotencyKeyMismatch(
            "idempotency_key no coincide con el canonical de SPEC-02 §9."
        )
    return computed


def arguments_sha256(arguments: Mapping[str, Any]) -> str:
    """Hash de argumentos para ``audit.hermes_tool_calls`` (sin selectores en claro)."""
    return sha256_hex(canonical_json_bytes(dict(arguments)))


__all__ = [
    "IdempotencyKeyMismatch",
    "arguments_sha256",
    "canonical_json_bytes",
    "expected_key",
    "idempotency_key_for_ingest",
    "idempotency_key_for_relationship",
    "idempotency_key_for_sigint",
    "sha256_hex",
    "verify_key",
]
