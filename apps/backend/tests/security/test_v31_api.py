"""Aceptación HTTP de las rutas v3.1 (sin PostgreSQL).

Usa el mismo ``app`` y los mismos JWT del arnés de seguridad; el gateway v3.1 se
sustituye por un doble para verificar RBAC, TLP, validación y contrato de
respuesta.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.deps import get_v31_gateway
from app.core.rbac import Role
from tests.security.conftest import auth_header


class ProposalVersionConflict(RuntimeError):
    pass


class FakeV31Gateway:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _record(self, name: str, kwargs: dict[str, Any]) -> None:
        self.calls.append((name, kwargs))

    def find_colocation(self, **kwargs):
        self._record("find_colocation", kwargs)
        return {
            "query": {
                "mission_id": kwargs["mission_id"],
                "center": kwargs["center"],
                "radius_m": kwargs["radius_m"],
                "window": kwargs["window"],
            },
            "clusters": [
                {
                    "cluster_id": "coloc-test",
                    "center": {"lat": 40.4168, "lon": -3.7038},
                    "radius_m": 120.0,
                    "score": 0.91,
                    "disciplines": ["SIGINT", "SOCMINT"],
                    "events": [
                        {
                            "event_id": "11111111-1111-1111-1111-111111111111",
                            "discipline": "SIGINT",
                            "event_type": "RF_EMISSION",
                            "event_time": "2026-10-01T21:14:03Z",
                            "distance_m": 42.0,
                            "confidence": 82,
                            "tlp": "TLP:AMBER",
                            "entity_id": "22222222-2222-2222-2222-222222222222",
                            "observable_id": "33333333-3333-3333-3333-333333333333",
                            "masked_selector": "IMSI:***345",
                            "evidence_ids": [
                                "44444444-4444-4444-4444-444444444444"
                            ],
                        }
                    ],
                    "time_delta_s": 217.0,
                    "evidence_ids": ["44444444-4444-4444-4444-444444444444"],
                }
            ],
            "truncated": False,
            "next_cursor": None,
            "suppressed": 0,
        }

    def get_subgraph(self, **kwargs):
        self._record("get_subgraph", kwargs)
        return {
            "nodes": [
                {
                    "entity_id": "22222222-2222-2222-2222-222222222222",
                    "stix_id": "identity--2222",
                    "stix_type": "identity",
                    "canonical_name": "Entidad de prueba",
                    "primary_discipline": "SOCMINT",
                    "admiralty": "B2",
                    "confidence": 78,
                    "tlp": "TLP:AMBER",
                    "valid_from": "2026-10-01T00:00:00Z",
                    "valid_until": None,
                    "depth": 1,
                    "observables": [],
                    "evidence_ids": [],
                }
            ],
            "edges": [],
            "truncated": False,
            "next_cursor": None,
            "as_of": kwargs["as_of"],
        }

    def detect_alias_evasion(self, **kwargs):
        self._record("detect_alias_evasion", kwargs)
        return {
            "signals": [
                {
                    "signal_id": "alias-test",
                    "signal_type": "RAPID_ALIAS_ROTATION",
                    "entity_id": "22222222-2222-2222-2222-222222222222",
                    "observable_id": "33333333-3333-3333-3333-333333333333",
                    "score": 0.86,
                    "occurrences": 6,
                    "max_gap_hours": 3.5,
                    "first_seen": "2026-09-01T00:00:00Z",
                    "last_seen": "2026-09-01T18:00:00Z",
                    "aliases": ["@a", "@b", "@c"],
                    "platforms": ["telegram"],
                    "evidence_ids": [],
                    "explanation": "Rotación rápida.",
                }
            ],
            "truncated": False,
            "suppressed": 0,
        }

    def list_proposals(self, **kwargs):
        self._record("list_proposals", kwargs)
        return {
            "items": [self._proposal()],
            "truncated": False,
            "next_cursor": None,
        }

    def get_proposal(self, proposal_id, **kwargs):
        self._record("get_proposal", {"proposal_id": proposal_id, **kwargs})
        return self._proposal(proposal_id=proposal_id)

    def approve_proposal(self, proposal_id, **kwargs):
        self._record("approve_proposal", {"proposal_id": proposal_id, **kwargs})
        if kwargs.get("version") != 3:
            raise ProposalVersionConflict("versión desactualizada")
        return self._proposal(
            proposal_id=proposal_id,
            status="ANALYST_VERIFIED",
            version=4,
            reviewed_by=kwargs.get("reviewer_id"),
        )

    def reject_proposal(self, proposal_id, **kwargs):
        self._record("reject_proposal", {"proposal_id": proposal_id, **kwargs})
        return self._proposal(
            proposal_id=proposal_id,
            status="REJECTED",
            version=4,
            reviewed_by=kwargs.get("reviewer_id"),
            decision_reason=kwargs.get("reason"),
        )

    def get_evidence(self, evidence_id, **kwargs):
        self._record("get_evidence", {"evidence_id": evidence_id, **kwargs})
        return {
            "evidence_id": evidence_id,
            "payload_sha256": "a" * 64,
            "tlp": "TLP:AMBER",
            "collector_agent": "HERMES",
            "source_type": "SIGINT",
            "source_uri": "sensor://test",
            "captured_at": "2026-10-01T00:00:00Z",
            "received_at": "2026-10-01T00:00:01Z",
            "payload": {"masked": True},
            "metadata": {},
        }

    def verify_evidence(self, evidence_id, **kwargs):
        self._record("verify_evidence", {"evidence_id": evidence_id, **kwargs})
        return {
            "evidence_id": evidence_id,
            "valid": True,
            "stored_sha256": "a" * 64,
            "computed_sha256": "a" * 64,
            "chain_valid": True,
            "checked_at": "2026-10-01T00:00:00Z",
        }

    @staticmethod
    def _proposal(**overrides: Any) -> dict[str, Any]:
        item = {
            "proposal_id": "66666666-6666-6666-6666-666666666666",
            "proposal_type": "RELATIONSHIP",
            "status": "AGENT_STAGED",
            "mission_id": "77777777-7777-7777-7777-777777777777",
            "agent_type": "HERMES",
            "target_entity_id": None,
            "payload": {"relationship_type": "COMMUNICATES_WITH"},
            "evidence_ids": ["44444444-4444-4444-4444-444444444444"],
            "confidence": 71,
            "version": 3,
            "created_at": "2026-10-01T00:00:00Z",
            "reviewed_by": None,
            "reviewed_at": None,
            "decision_reason": None,
        }
        item.update(overrides)
        return item


@pytest.fixture
def v31_gateway() -> FakeV31Gateway:
    return FakeV31Gateway()


@pytest.fixture(autouse=True)
def _override_v31(app: Any, v31_gateway: FakeV31Gateway) -> None:
    app.dependency_overrides[get_v31_gateway] = lambda: v31_gateway
    yield
    app.dependency_overrides.pop(get_v31_gateway, None)


COLOCATION = {
    "mission_id": "77777777-7777-7777-7777-777777777777",
    "center": {"lat": 40.4168, "lon": -3.7038},
    "radius_m": 500,
    "window": {
        "from": "2026-10-01T00:00:00Z",
        "to": "2026-10-02T00:00:00Z",
    },
    "disciplines": ["SIGINT", "SOCMINT"],
    "min_confidence": 60,
}


def test_colocation_requires_sigint_and_a_social_discipline(client, tokens):
    body = dict(COLOCATION)
    body["disciplines"] = ["SIGINT", "HUMINT"]
    response = client.post(
        "/api/v1/correlation/colocation",
        json=body,
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 422


def test_viewer_cannot_request_above_clearance(client, tokens):
    body = dict(COLOCATION)
    body["max_tlp"] = "TLP:AMBER"
    response = client.post(
        "/api/v1/correlation/colocation",
        json=body,
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "TLP_DENIED"


def test_analyst_colocation_returns_cluster_and_masks_selector(client, tokens, v31_gateway):
    response = client.post(
        "/api/v1/correlation/colocation",
        json={**COLOCATION, "max_tlp": "TLP:AMBER"},
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["clusters"][0]["score"] == 0.91
    masked = payload["clusters"][0]["events"][0]["masked_selector"]
    assert masked == "IMSI:***345"
    assert v31_gateway.calls[0][1]["clearance"] == "TLP:AMBER"


def test_viewer_cannot_request_staged_subgraph(client, tokens):
    response = client.post(
        "/api/v1/graph/subgraph",
        json={
            "mission_id": COLOCATION["mission_id"],
            "root_ids": ["22222222-2222-2222-2222-222222222222"],
            "include_staged": True,
        },
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 403


def test_alias_evasion_is_read_only(client, tokens):
    response = client.post(
        "/api/v1/analytics/alias-evasion",
        json={
            "mission_id": COLOCATION["mission_id"],
            "window": COLOCATION["window"],
        },
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["signals"][0]["signal_type"] == "RAPID_ALIAS_ROTATION"


def test_viewer_cannot_approve_but_analyst_can(client, tokens, v31_gateway):
    proposal_id = "66666666-6666-6666-6666-666666666666"
    decision = {"reason": "Evidencia suficiente", "version": 3}
    denied = client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json=decision,
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert denied.status_code == 403
    ok = client.post(
        f"/api/v1/proposals/{proposal_id}/approve",
        json=decision,
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["status"] == "ANALYST_VERIFIED"
    assert ok.json()["version"] == 4
    assert v31_gateway.calls[-1][1]["reviewer_id"]


def test_stale_proposal_version_is_409_with_specific_code(client, tokens):
    response = client.post(
        "/api/v1/proposals/66666666-6666-6666-6666-666666666666/approve",
        json={"reason": "Evidencia suficiente", "version": 2},
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PROPOSAL_VERSION_CONFLICT"


def test_evidence_verify_is_get_and_requires_analyst(client, tokens):
    evidence_id = "44444444-4444-4444-4444-444444444444"
    denied = client.get(
        f"/api/v1/evidence/{evidence_id}/verify",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert denied.status_code == 403
    ok = client.get(
        f"/api/v1/evidence/{evidence_id}/verify",
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["chain_valid"] is True


def test_evidence_inspector_returns_tlp_scoped_payload(client, tokens):
    evidence_id = "44444444-4444-4444-4444-444444444444"
    response = client.get(
        f"/api/v1/evidence/{evidence_id}",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["evidence_id"] == evidence_id


def test_proposals_list_is_analyst_only(client, tokens, v31_gateway):
    denied = client.get(
        "/api/v1/proposals",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert denied.status_code == 403

    response = client.get(
        "/api/v1/proposals?status=AGENT_STAGED&limit=10",
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["status"] == "AGENT_STAGED"
    assert v31_gateway.calls[-1][1]["status"] == "AGENT_STAGED"
    assert v31_gateway.calls[-1][1]["clearance"] == "TLP:AMBER"


def test_reject_proposal_requires_reason_and_records_reviewer(client, tokens):
    proposal_id = "66666666-6666-6666-6666-666666666666"
    short = client.post(
        f"/api/v1/proposals/{proposal_id}/reject",
        json={"reason": "corto", "version": 3},
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert short.status_code == 422

    response = client.post(
        f"/api/v1/proposals/{proposal_id}/reject",
        json={"reason": "Fuente no independiente", "version": 3},
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "REJECTED"
    assert response.json()["decision_reason"] == "Fuente no independiente"


def test_analyst_subgraph_returns_nodes_and_edges(client, tokens, v31_gateway):
    response = client.post(
        "/api/v1/graph/subgraph",
        json={
            "mission_id": COLOCATION["mission_id"],
            "root_ids": ["22222222-2222-2222-2222-222222222222"],
            "depth": 2,
            "include_staged": True,
            "max_nodes": 50,
        },
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["nodes"][0]["canonical_name"] == "Entidad de prueba"
    assert v31_gateway.calls[-1][1]["include_staged"] is True
    assert v31_gateway.calls[-1][1]["clearance"] == "TLP:AMBER"


def test_invalid_uuid_is_422_not_500(client, tokens):
    response = client.get(
        "/api/v1/proposals/not-a-uuid",
        headers=auth_header(tokens[Role.ANALYST]),
    )
    # El path declara `format: uuid` en OpenAPI y FastAPI valida antes de
    # tocar el repositorio: nunca debe llegar `::uuid` a PostgreSQL ni ser 500.
    assert response.status_code == 422


def test_missing_v31_capability_returns_503(app, client, tokens):
    from app.api.v31_gateway import V31Gateway

    app.dependency_overrides[get_v31_gateway] = lambda: V31Gateway(object())
    response = client.post(
        "/api/v1/correlation/colocation",
        json=COLOCATION,
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"
