"""Pruebas del harness Hermes v3.1 sin base de datos."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from hermes_harness.gateway import ToolGateway
from hermes_harness.idempotency import (
    IdempotencyKeyMismatch,
    idempotency_key_for_ingest,
    idempotency_key_for_relationship,
    idempotency_key_for_sigint,
)
from hermes_harness.models import (
    IngestMultiIntObservable,
    ProposeRelationship,
    RecordSigintTelemetry,
    ToolEnvelope,
)
from hermes_harness.normalization import (
    InvalidSelector,
    normalize_selector,
    selector_fingerprint,
)
from hermes_harness.opsec import OpsecContext, evaluate_opsec


NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
EVIDENCE = str(uuid4())
SHA = "a" * 64


def _ingest_values(*, key: str = "0" * 64, normalized: str | None = None) -> dict[str, Any]:
    return {
        "tool_call_id": str(uuid4()),
        "agent_id": "HERMES",
        "run_id": str(uuid4()),
        "mission_id": str(uuid4()),
        "pir_id": str(uuid4()),
        "discipline": "SOCMINT",
        "observable_type": "USERNAME",
        "value": "@JuanPerez",
        "normalized_value": normalized,
        "observed_at": NOW.isoformat(),
        "source": {
            "platform": "telegram",
            "uri": "https://t.me/juanperez",
            "source_type": "SOCMINT",
            "legal_basis": "OSINT público; Ley 1/2020",
        },
        "evidence": {
            "evidence_id": EVIDENCE,
            "payload_sha256": SHA,
            "captured_at": NOW.isoformat(),
            "tlp": "TLP:AMBER",
        },
        "tlp": "TLP:AMBER",
        "admiralty": "B2",
        "idempotency_key": key,
    }


def _ingest_model() -> IngestMultiIntObservable:
    values = _ingest_values()
    model = IngestMultiIntObservable.model_validate(values)
    model.normalized_value = normalize_selector(model.observable_type, model.value)
    model.idempotency_key = idempotency_key_for_ingest(model)
    return model


class FakeBackend:
    def __init__(self, *, allow_opsec: bool = True) -> None:
        self.allow_opsec = allow_opsec
        self.calls: list[tuple[str, Any]] = []
        self.audit: list[dict[str, Any]] = []
        self.opsec_events: list[dict[str, Any]] = []

    def search_existing_entities(self, payload):
        self.calls.append(("search", payload))
        return {"status": "OK", "data": {"candidates": []}, "candidates": []}

    def ingest_multi_int_observable(self, payload):
        self.calls.append(("ingest", payload))
        return {
            "status": "STAGED",
            "proposal_id": str(uuid4()),
            "data": {"normalized_value": payload.normalized_value},
        }

    def record_sigint_telemetry(self, payload):
        self.calls.append(("sigint", payload))
        return {"status": "STAGED", "proposal_id": str(uuid4())}

    def propose_relationship(self, payload):
        self.calls.append(("relationship", payload))
        return {"status": "STAGED", "proposal_id": str(uuid4())}

    def opsec_context(self, payload):
        return OpsecContext(
            sockpuppet_active=True,
            mission_active=True,
            interaction_authorized=self.allow_opsec,
            legal_basis="Autorización judicial 2026/42",
            target_is_human=True,
        )

    def record_opsec_event(self, payload):
        self.opsec_events.append(payload)

    def record_tool_call(self, **kwargs):
        self.audit.append(kwargs)


def _envelope(tool_name: str, arguments: dict[str, Any], **context: Any) -> ToolEnvelope:
    return ToolEnvelope(
        tool_call_id=context.get("tool_call_id", uuid4()),
        agent_id=context.get("agent_id", "HERMES"),
        run_id=context.get("run_id", uuid4()),
        mission_id=context.get("mission_id", uuid4()),
        tool_name=tool_name,
        arguments=arguments,
    )


# ---------------------------------------------------------------------------
# Normalización / claves
# ---------------------------------------------------------------------------
def test_normalization_matches_contract_examples():
    assert normalize_selector("USERNAME", "  @JuanPérez ") == "juanpérez"
    assert normalize_selector("PHONE_E164", "+34 600-123-456") == "+34600123456"
    assert normalize_selector("URL", "HTTPS://Example.COM:443/a?b=1#x") == (
        "https://example.com/a?b=1"
    )
    assert normalize_selector("IPV6", "2001:0db8::1") == "2001:db8::1"
    with pytest.raises(InvalidSelector):
        normalize_selector("PHONE_E164", "600123456")
    assert selector_fingerprint("USERNAME", "@A") == selector_fingerprint(
        "USERNAME", "a"
    )


def test_idempotency_is_stable_and_detects_tampering():
    model = _ingest_model()
    assert model.idempotency_key == idempotency_key_for_ingest(model)
    changed = model.model_copy(
        update={"value": "@otro", "normalized_value": "otro"}
    )
    with pytest.raises(IdempotencyKeyMismatch):
        from hermes_harness.idempotency import verify_key

        verify_key(changed, changed.idempotency_key)


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------
def test_gateway_rejects_unknown_tool_before_backend():
    backend = FakeBackend()
    result = ToolGateway(backend).call(
        ToolEnvelope(
            tool_call_id=uuid4(),
            agent_id="HERMES",
            run_id=uuid4(),
            mission_id=uuid4(),
            tool_name="shell",
            arguments={},
        )
    )
    assert result.status == "ERROR"
    assert result.error and result.error.code == "TOOL_NOT_ALLOWED"
    assert backend.calls == []


def test_gateway_rejects_envelope_fields_in_arguments():
    backend = FakeBackend()
    result = ToolGateway(backend).call(
        _envelope("search_existing_entities", {"query": {"name": "x"}, "run_id": "x"})
    )
    assert result.error and result.error.code == "SCHEMA_INVALID"
    assert backend.calls == []


def test_valid_ingest_is_staged_and_never_writes_directly():
    backend = FakeBackend()
    model = _ingest_model()
    arguments = {
        k: v
        for k, v in model.model_dump(mode="json").items()
        if k not in {"tool_call_id", "agent_id", "run_id", "mission_id"}
    }
    result = ToolGateway(backend).call(
        _envelope(
            "ingest_multi_int_observable",
            arguments,
            tool_call_id=model.tool_call_id,
            agent_id=model.agent_id,
            run_id=model.run_id,
            mission_id=model.mission_id,
        )
    )
    assert result.status == "STAGED"
    assert result.proposal_id
    assert backend.calls[0][0] == "ingest"
    assert backend.calls[0][1].normalized_value == "juanperez"
    assert backend.audit and backend.audit[0]["arguments_sha256"]


def test_bad_idempotency_key_is_rejected():
    backend = FakeBackend()
    args = {
        k: v
        for k, v in _ingest_model().model_dump(mode="json").items()
        if k not in {"tool_call_id", "agent_id", "run_id", "mission_id"}
    }
    args["idempotency_key"] = "b" * 64
    model = _ingest_model()
    result = ToolGateway(backend).call(
        _envelope(
            "ingest_multi_int_observable",
            args,
            agent_id=model.agent_id,
            run_id=model.run_id,
            mission_id=model.mission_id,
        )
    )
    assert result.status == "ERROR"
    assert result.error and result.error.code == "IDEMPOTENCY_KEY_MISMATCH"
    assert backend.calls == []


def test_opsec_aborts_without_context_and_records_event():
    backend = FakeBackend()
    backend.opsec_context = None  # type: ignore[assignment]
    result = ToolGateway(backend).call(
        _envelope(
            "check_opsec",
            {
                "sockpuppet_id": str(uuid4()),
                "interaction_id": str(uuid4()),
                "action": "SEND_MESSAGE",
            },
        )
    )
    assert result.status == "ABORTED"
    assert result.error and result.error.code == "OPSEC_VIOLATION"
    assert backend.calls == []


def test_opsec_allows_only_with_authorized_context():
    backend = FakeBackend(allow_opsec=True)
    result = ToolGateway(backend).call(
        _envelope(
            "check_opsec",
            {
                "sockpuppet_id": str(uuid4()),
                "interaction_id": str(uuid4()),
                "action": "SEND_MESSAGE",
            },
        )
    )
    assert result.status == "OK"
    assert result.data["decision"] == "ALLOW"
    assert backend.opsec_events


def test_pure_opsec_is_fail_closed():
    assert not evaluate_opsec("SEND_MESSAGE", OpsecContext()).allowed
    assert not evaluate_opsec(
        "PROFILE_VIEW",
        OpsecContext(
            sockpuppet_active=True,
            mission_active=True,
            interaction_authorized=False,
            legal_basis="x" * 10,
        ),
    ).allowed
    assert evaluate_opsec(
        "SEND_MESSAGE",
        OpsecContext(
            sockpuppet_active=True,
            mission_active=True,
            interaction_authorized=True,
            legal_basis="Orden judicial 2026/42",
        ),
    ).allowed


def test_sigint_and_relationship_keys():
    sig = RecordSigintTelemetry(
        tool_call_id=uuid4(),
        agent_id="ARGOS",
        run_id=uuid4(),
        mission_id=uuid4(),
        pir_id=uuid4(),
        event_type="RF_EMISSION",
        selector={"observable_type": "IMSI", "value": "123456789012345"},
        observed_at=NOW,
        evidence={
            "evidence_id": EVIDENCE,
            "payload_sha256": SHA,
            "captured_at": NOW,
            "tlp": "TLP:AMBER",
        },
        admiralty="B2",
        tlp="TLP:AMBER",
        idempotency_key="0" * 64,
    )
    sig.idempotency_key = idempotency_key_for_sigint(sig)
    assert len(sig.idempotency_key) == 64

    rel = ProposeRelationship(
        tool_call_id=uuid4(),
        agent_id="ARACNE",
        run_id=uuid4(),
        mission_id=uuid4(),
        source_entity_id=uuid4(),
        target_entity_id=uuid4(),
        relationship_type="COMMUNICATES_WITH",
        discipline="SOCMINT",
        valid_from=NOW,
        admiralty="C3",
        confidence_hint=0.6,
        evidence_ids=[UUID(EVIDENCE)],
        tlp="TLP:AMBER",
        idempotency_key="0" * 64,
    )
    rel.idempotency_key = idempotency_key_for_relationship(rel)
    assert len(rel.idempotency_key) == 64
