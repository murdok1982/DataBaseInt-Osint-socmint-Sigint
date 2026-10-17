"""Listados de expedientes y RFI (contrato v2.1.0) — Verificación Cruzada.

Autorizado por: **SPEC-03 §6** — ``GET /api/v1/analyses`` (``listAnalyses``) y
``GET /api/v1/analyses/{id}/requirements`` (``listAnalysisRequirements``), rol
mínimo ``VIEWER``; **SPEC-04 §4** — ``AI_INFORMANT`` no puede llamar a
``/analyses/*`` (403).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.rbac import Role
from tests.security.conftest import (
    ANALYSIS_ID,
    REQUIREMENT_ID,
    api_key_header,
    auth_header,
)

UNKNOWN_ANALYSIS_ID = "99999999-9999-9999-9999-999999999999"
HUMAN_ROLES = (Role.ADMIN, Role.ANALYST, Role.VIEWER)


@pytest.mark.parametrize("role", HUMAN_ROLES, ids=[r.value for r in HUMAN_ROLES])
def test_list_analyses_is_allowed_for_every_human_role(
    client: Any, tokens: dict[Role, str], role: Role
) -> None:
    """``listAnalyses`` (VIEWER+) devuelve ``{items: [Analysis]}``."""
    response = client.get("/api/v1/analyses", headers=auth_header(tokens[role]))
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == [ANALYSIS_ID]


def test_list_analyses_filters_by_status(
    client: Any, tokens: dict[Role, str]
) -> None:
    """``?status`` acota por estado del expediente (SPEC-03 §6)."""
    headers = auth_header(tokens[Role.VIEWER])
    active = client.get("/api/v1/analyses?status=ACTIVE", headers=headers)
    closed = client.get("/api/v1/analyses?status=CLOSED", headers=headers)
    assert active.status_code == closed.status_code == 200
    assert len(active.json()["items"]) == 1
    assert closed.json()["items"] == []


def test_list_analyses_rejects_unknown_status(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Un estado fuera del enum del contrato ⇒ 422 (validación en servidor)."""
    response = client.get(
        "/api/v1/analyses?status=INVENTADO",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 422


@pytest.mark.parametrize("role", HUMAN_ROLES, ids=[r.value for r in HUMAN_ROLES])
def test_list_requirements_is_allowed_for_every_human_role(
    client: Any, tokens: dict[Role, str], role: Role
) -> None:
    """``listAnalysisRequirements`` (VIEWER+) devuelve los RFI del expediente."""
    response = client.get(
        f"/api/v1/analyses/{ANALYSIS_ID}/requirements",
        headers=auth_header(tokens[role]),
    )
    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert [item["id"] for item in items] == [REQUIREMENT_ID]
    assert items[0]["parent_analysis_id"] == ANALYSIS_ID


def test_list_requirements_includes_a_newly_created_rfi(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Un RFI creado por el analista aparece en el listado del expediente."""
    created = client.post(
        f"/api/v1/analyses/{ANALYSIS_ID}/requirements",
        headers=auth_header(tokens[Role.ANALYST]),
        json={"question": "¿Dónde opera Acme Ltd.?", "priority": "LOW"},
    )
    assert created.status_code == 201
    response = client.get(
        f"/api/v1/analyses/{ANALYSIS_ID}/requirements",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    ids = {item["id"] for item in response.json()["items"]}
    assert {REQUIREMENT_ID, created.json()["id"]} <= ids


def test_list_requirements_returns_404_for_unknown_analysis(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Expediente inexistente ⇒ 404 ``NOT_FOUND`` (nunca 200 vacío ni 500)."""
    response = client.get(
        f"/api/v1/analyses/{UNKNOWN_ANALYSIS_ID}/requirements",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/analyses", f"/api/v1/analyses/{ANALYSIS_ID}/requirements"],
    ids=["listAnalyses", "listAnalysisRequirements"],
)
def test_informant_cannot_list_analyses(client: Any, path: str) -> None:
    """``AI_INFORMANT`` no accede a ``/analyses/*`` ⇒ 403 (SPEC-04 §4)."""
    response = client.get(path, headers=api_key_header())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize(
    "path",
    ["/api/v1/analyses", f"/api/v1/analyses/{ANALYSIS_ID}/requirements"],
    ids=["listAnalyses", "listAnalysisRequirements"],
)
def test_listings_without_credentials_are_401(client: Any, path: str) -> None:
    """Sin credenciales ⇒ 401 (no 403)."""
    response = client.get(path)
    assert response.status_code == 401
