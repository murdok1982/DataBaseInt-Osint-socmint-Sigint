"""Integridad referencial al crear un RFI (SPEC-03 §6 v2.0.1).

Autorizado por: **SPEC-03 §6** — ``POST /api/v1/analyses/{id}/requirements``
exige que el ``Analysis`` ``{id}`` exista; si no, responde **404** (nunca un
``500`` por un ``MATCH`` fallido del repositorio).

Se verifica el comportamiento de aceptación con el gateway doble inyectado
(``app.dependency_overrides[get_graph_gateway]``): el caso inexistente devuelve
404 y el caso válido sigue devolviendo 201 con el ``parent_analysis_id``.
"""

from __future__ import annotations

from typing import Any

from app.core.rbac import Role
from tests.security.conftest import (
    ANALYSIS_ID,
    auth_header,
)

#: ``Analysis`` inexistente en el repositorio doble.
UNKNOWN_ANALYSIS_ID = "99999999-9999-9999-9999-999999999999"


def test_create_requirement_returns_404_for_unknown_analysis(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Un ``Analysis`` inexistente ⇒ 404 ``NOT_FOUND`` (nunca 500)."""
    response = client.post(
        f"/api/v1/analyses/{UNKNOWN_ANALYSIS_ID}/requirements",
        headers=auth_header(tokens[Role.ANALYST]),
        json={"question": "¿Quién controla Acme Ltd.?", "priority": "HIGH"},
    )
    assert response.status_code == 404, response.text
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_create_requirement_returns_201_for_existing_analysis(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Un ``Analysis`` existente sigue creando el RFI con 201."""
    response = client.post(
        f"/api/v1/analyses/{ANALYSIS_ID}/requirements",
        headers=auth_header(tokens[Role.ANALYST]),
        json={"question": "¿Quién controla Acme Ltd.?", "priority": "HIGH"},
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["id"]
    assert body["parent_analysis_id"] == ANALYSIS_ID
    assert body["status"] == "OPEN"
