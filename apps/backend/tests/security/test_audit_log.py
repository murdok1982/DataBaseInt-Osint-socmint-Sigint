"""Bitácora de auditoría de accesos — Verificación Cruzada de SPEC-06 §1.

Autorizado por: **SPEC-06 §1.2** (campos del evento ``http_request``), **§1.3**
(``X-Request-ID``), **§1.4** (evento ``auth`` sin usuario en claro) y **§1.5**
(datos que jamás se registran).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import pytest

from app.api.deps import get_user_store
from app.core.audit import AUDIT_LOGGER_NAME, get_audit_logger, username_fingerprint
from app.core.rbac import Role
from tests.security.conftest import (
    ANALYSIS_ID,
    API_KEY,
    ANALYST_ID,
    TEST_PASSWORD,
    api_key_header,
    auth_header,
    login_payload,
)

HTTP_FIELDS = {
    "event",
    "ts",
    "request_id",
    "actor_id",
    "actor_role",
    "method",
    "route",
    "resource_id",
    "status",
    "duration_ms",
    "client_ip",
}


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())

    def events(self, kind: str) -> list[dict[str, Any]]:
        parsed = [json.loads(line) for line in self.lines]
        return [event for event in parsed if event["event"] == kind]


@pytest.fixture
def audit() -> Iterator[_Collector]:
    logger = get_audit_logger()
    collector = _Collector()
    logger.addHandler(collector)
    try:
        yield collector
    finally:
        logger.removeHandler(collector)


def test_audit_logger_is_isolated_json_on_stdout() -> None:
    logger = logging.getLogger(AUDIT_LOGGER_NAME)
    get_audit_logger()
    assert logger.propagate is False
    assert any(getattr(h, "_osint_audit", False) for h in logger.handlers)


def test_each_request_emits_one_event_with_spec_fields(
    client: Any, tokens: dict[Role, str], audit: _Collector
) -> None:
    response = client.get(
        f"/api/v1/analyses/{ANALYSIS_ID}?secreto=no-registrar",
        headers=auth_header(tokens[Role.ANALYST]),
    )
    assert response.status_code == 200
    events = audit.events("http_request")
    assert len(events) == 1
    event = events[0]
    assert set(event) == HTTP_FIELDS
    assert event["actor_id"] == ANALYST_ID
    assert event["actor_role"] == "ANALYST"
    assert event["method"] == "GET"
    assert event["route"] == "/api/v1/analyses/{id}"
    assert event["resource_id"] == ANALYSIS_ID
    assert event["status"] == 200
    assert event["request_id"] == response.headers["X-Request-ID"]


def test_m2m_actor_is_recorded(client: Any, audit: _Collector) -> None:
    client.post(
        "/api/v1/context/preflight",
        headers=api_key_header(),
        json={"keywords": ["acme"]},
    )
    event = audit.events("http_request")[0]
    assert event["actor_role"] == "AI_INFORMANT"


def test_anonymous_and_denied_requests_are_recorded(
    client: Any, audit: _Collector
) -> None:
    client.get("/api/v1/graph/nodes")
    client.get("/api/v1/graph/nodes", headers=api_key_header())
    first, second = audit.events("http_request")
    assert first["status"] == 401 and first["actor_id"] is None
    assert second["status"] == 403 and second["actor_role"] == "AI_INFORMANT"


def test_no_credentials_query_or_body_reach_the_log(
    client: Any, tokens: dict[Role, str], audit: _Collector, app: Any, hashed_users: Any
) -> None:
    token = tokens[Role.ANALYST]
    client.get(
        "/api/v1/graph/nodes?q=dato-reservado",
        headers={**auth_header(token), "Cookie": "osint_refresh=cookie-secreta"},
    )
    client.get("/api/v1/graph/nodes", headers=api_key_header())
    client.post(
        "/api/v1/ingest/informant",
        headers={**api_key_header(), "X-Preflight-Token": "token-preflight-secreto"},
        files={"file": ("informe.json", b"{}", "application/json")},
        data={"payload": "{}"},
    )
    app.dependency_overrides[get_user_store] = lambda: hashed_users
    client.post("/api/v1/auth/login", json=login_payload("admin"))
    client.post("/api/v1/auth/login", json={"username": "admin", "password": "mal"})

    blob = "\n".join(audit.lines)
    for forbidden in (
        token,
        API_KEY,
        "token-preflight-secreto",
        "cookie-secreta",
        "dato-reservado",
        TEST_PASSWORD,
        "admin\"",
    ):
        assert forbidden not in blob, forbidden


def test_client_request_id_is_reused_only_when_safe(
    client: Any, tokens: dict[Role, str]
) -> None:
    headers = auth_header(tokens[Role.VIEWER])
    good = client.get(
        "/api/v1/graph/nodes", headers={**headers, "X-Request-ID": "corr-1234-abcd"}
    )
    assert good.headers["X-Request-ID"] == "corr-1234-abcd"
    bad = client.get(
        "/api/v1/graph/nodes",
        headers={**headers, "X-Request-ID": "<script>alert(1)</script>"},
    )
    assert bad.headers["X-Request-ID"] != "<script>alert(1)</script>"
    assert len(bad.headers["X-Request-ID"]) == 36


def test_login_emits_auth_events_without_plain_username(
    app: Any, hashed_users: Any, audit: _Collector
) -> None:
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_user_store] = lambda: hashed_users
    client = TestClient(app)
    client.post("/api/v1/auth/login", json=login_payload("analyst"))
    client.post("/api/v1/auth/login", json={"username": "analyst", "password": "x"})
    success, failure = audit.events("auth")
    assert success["outcome"] == "SUCCESS"
    assert success["actor_id"] == ANALYST_ID and success["actor_role"] == "ANALYST"
    assert failure["outcome"] == "FAILURE"
    assert failure["username_sha256"] == username_fingerprint("ANALYST")
    assert "analyst" not in json.dumps(failure)


def test_lockout_emits_locked_event(
    app: Any, hashed_users: Any, audit: _Collector
) -> None:
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_user_store] = lambda: hashed_users
    client = TestClient(app)
    for _ in range(6):
        client.post("/api/v1/auth/login", json={"username": "viewer", "password": "x"})
    outcomes = [event["outcome"] for event in audit.events("auth")]
    assert outcomes == ["FAILURE"] * 5 + ["LOCKED"]


def test_routes_outside_api_are_not_audited(client: Any, audit: _Collector) -> None:
    client.get("/docs")
    assert audit.events("http_request") == []
