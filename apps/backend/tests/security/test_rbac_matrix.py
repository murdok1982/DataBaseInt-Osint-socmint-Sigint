"""Las 28 celdas de la matriz RBAC de SPEC-04 §1 — Verificación Cruzada.

Autorizado por: **SPEC-04 §1** (matriz de 4 actores), **SPEC-04 §4** (criterio de
aceptación: *cada "No" devuelve 403*) y **SPEC-03 §3–§6** (rol mínimo por
endpoint; ``409 CONFLICT`` para nodo bloqueado e ingesta ciega).

La matriz de SPEC-04 §1 tiene **7 capacidades × 4 actores = 28 celdas**. Cada
celda humana se comprueba **dos veces**:

1. como función pura (:func:`app.core.rbac.has_permission`), y
2. contra el endpoint real con ``fastapi.testclient.TestClient``, donde cada
   celda "No" debe devolver **403** (nunca 401) y cada "Sí" un 2xx.

La columna del principal M2M ``AI_INFORMANT`` se comprueba con ``X-API-Key``:
solo ``/context/preflight`` e ``/ingest/informant`` le están permitidos.

Se incluye además la trazabilidad ``openapi.yaml`` ↔ ``operationId`` ↔ actores.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

import pytest

from app.core.rbac import (
    ALLOWED_ROLES_BY_OPERATION,
    ENDPOINT_MATRIX,
    MIN_ROLE_BY_OPERATION,
    MIN_ROLE_BY_PERMISSION,
    SPEC_04_MATRIX,
    Permission,
    Role,
    has_permission,
    role_satisfies,
)
from tests.security.conftest import (
    API_KEY,
    NODE_ID,
    TEST_PASSWORD,
    ANALYSIS_ID,
    api_key_header,
    auth_header,
    login_payload,
)

HUMAN_ROLES = (Role.ADMIN, Role.ANALYST, Role.VIEWER)
ALL_ROLES = (Role.ADMIN, Role.ANALYST, Role.VIEWER, Role.AI_INFORMANT)

#: Payload mínimo válido contra ``contracts/extraction-schema.json`` (v2.0).
INFORMANT_PAYLOAD: dict[str, Any] = {
    "report_metadata": {
        "title": "Informe del informante",
        "source_type": "NEWS",
        "source_url_or_origin": "https://example.test/nota",
        "reliability_rating": "B",
    },
    "extracted_triplets": [],
    "analytical_context_links": [],
}

# =====================================================================
# Las 6 acciones humanas de la matriz y su petición HTTP equivalente
# (LOGIN se comprueba aparte contra /auth/login)
# =====================================================================
ACTION_SPECS: tuple[tuple[Permission, str, str, Optional[dict[str, Any]]], ...] = (
    (Permission.GRAPH_READ, "GET", "/api/v1/graph/nodes", None),
    (
        Permission.ANALYSIS_WRITE,
        "PUT",
        f"/api/v1/graph/nodes/{NODE_ID}",
        {"json": {"canonical_name": "Juan Pérez (actualizado)"}},
    ),
    (
        Permission.GRAPH_LOCK,
        "POST",
        f"/api/v1/graph/nodes/{NODE_ID}/lock",
        {"json": {"locked": True}},
    ),
    (
        Permission.INGEST_OSINT,
        "POST",
        "/api/v1/ingest",
        {
            "files": {
                "file": ("informe.txt", b"Juan Perez controla Acme Ltd.", "text/plain")
            },
            "data": {"title": "Informe de prueba"},
        },
    ),
    (Permission.COHERENCE_RESOLVE, "GET", "/api/v1/coherence/alerts", None),
    (Permission.ADMIN_MANAGE, "DELETE", f"/api/v1/graph/nodes/{NODE_ID}", None),
)

#: Las 28 celdas (7 capacidades × 4 actores) derivadas de SPEC_04_MATRIX.
CELLS: tuple[tuple[Permission, Role, bool], ...] = tuple(
    (permission, role, SPEC_04_MATRIX[permission][role])
    for permission in Permission
    for role in ALL_ROLES
)

#: Las 18 celdas humanas comprobables contra HTTP.
HTTP_CELLS: tuple[tuple[Permission, Role, bool], ...] = tuple(
    (permission, role, SPEC_04_MATRIX[permission][role])
    for permission, *_ in ACTION_SPECS
    for role in HUMAN_ROLES
)

CELL_IDS = [f"{permission.value}-{role.value}" for permission, role, _ in CELLS]
HTTP_CELL_IDS = [
    f"{permission.value}-{role.value}" for permission, role, _ in HTTP_CELLS
]
ACTION_IDS = [permission.value for permission, *_ in ACTION_SPECS]
ACTION_REQUESTS: tuple[tuple[str, str, Optional[dict[str, Any]]], ...] = tuple(
    (method, path, kwargs) for _permission, method, path, kwargs in ACTION_SPECS
)


def test_matrix_has_exactly_28_cells() -> None:
    """SPEC-04 §1: 7 capacidades × 4 actores = 28 celdas."""
    assert len(SPEC_04_MATRIX) == 7
    for cells in SPEC_04_MATRIX.values():
        assert set(cells) == set(ALL_ROLES)
    assert len(CELLS) == 28


def test_matrix_matches_spec_04_table() -> None:
    """Comparación literal con la tabla de SPEC-04 §1."""
    # Orden de columnas: ADMIN, ANALYST, AI_INFORMANT, VIEWER.
    expected = {
        Permission.LOGIN: (True, True, False, True),
        Permission.GRAPH_READ: (True, True, False, True),
        Permission.ANALYSIS_WRITE: (True, True, False, False),
        Permission.GRAPH_LOCK: (True, True, False, False),
        Permission.INGEST_OSINT: (True, True, True, False),
        Permission.COHERENCE_RESOLVE: (True, True, False, False),
        Permission.ADMIN_MANAGE: (True, False, False, False),
    }
    columns = (Role.ADMIN, Role.ANALYST, Role.AI_INFORMANT, Role.VIEWER)
    for permission, values in expected.items():
        assert SPEC_04_MATRIX[permission] == dict(zip(columns, values))

    denied = {
        (permission, role)
        for permission, cells in SPEC_04_MATRIX.items()
        for role, allowed in cells.items()
        if not allowed
    }
    assert denied == {
        (Permission.LOGIN, Role.AI_INFORMANT),
        (Permission.GRAPH_READ, Role.AI_INFORMANT),
        (Permission.ANALYSIS_WRITE, Role.AI_INFORMANT),
        (Permission.ANALYSIS_WRITE, Role.VIEWER),
        (Permission.GRAPH_LOCK, Role.AI_INFORMANT),
        (Permission.GRAPH_LOCK, Role.VIEWER),
        (Permission.INGEST_OSINT, Role.VIEWER),
        (Permission.COHERENCE_RESOLVE, Role.AI_INFORMANT),
        (Permission.COHERENCE_RESOLVE, Role.VIEWER),
        (Permission.ADMIN_MANAGE, Role.ANALYST),
        (Permission.ADMIN_MANAGE, Role.AI_INFORMANT),
        (Permission.ADMIN_MANAGE, Role.VIEWER),
    }


@pytest.mark.parametrize(("permission", "role", "expected"), CELLS, ids=CELL_IDS)
def test_matrix_cell_pure_permissions(
    permission: Permission, role: Role, expected: bool
) -> None:
    """Celda de matriz como función pura (sin HTTP)."""
    allowed = has_permission(role, permission)
    assert isinstance(allowed, bool)
    assert allowed == expected
    # Coherencia con el rol mínimo jerárquico usado por ``require_role`` (solo
    # cuando la fila es expresable como un único rol humano).
    min_role = MIN_ROLE_BY_PERMISSION[permission]
    if min_role is not None and role in HUMAN_ROLES:
        assert role_satisfies(role, min_role) == expected


@pytest.mark.parametrize(
    ("permission", "role", "expected"), HTTP_CELLS, ids=HTTP_CELL_IDS
)
def test_matrix_cell_http(
    client: Any,
    tokens: dict[Role, str],
    permission: Permission,
    role: Role,
    expected: bool,
) -> None:
    """Celda humana contra el endpoint real: "Sí" ⇒ 2xx, "No" ⇒ 403."""
    _permission, method, path, kwargs = next(
        spec for spec in ACTION_SPECS if spec[0] is permission
    )
    response = getattr(client, method.lower())(
        path, headers=auth_header(tokens[role]), **(kwargs or {})
    )
    if expected:
        assert 200 <= response.status_code < 300, (
            f"{method} {path} con rol {role.value} debería permitirse "
            f"(SPEC-04 §1) pero devolvió {response.status_code}"
        )
    else:
        assert response.status_code == 403, (
            f"{method} {path} con rol {role.value} debería devolver 403 "
            f"(SPEC-04 §4) pero devolvió {response.status_code}"
        )
        assert response.json()["error"]["code"] == "FORBIDDEN"


# =====================================================================
# LOGIN (fila de la matriz) contra /auth/login
# =====================================================================
@pytest.fixture
def login_client(app: Any, hashed_users: Any) -> Any:
    """Cliente con operadores de hash Argon2id real (ADMIN/ANALYST/VIEWER)."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_user_store

    app.dependency_overrides[get_user_store] = lambda: hashed_users
    return TestClient(app)


@pytest.mark.parametrize(
    ("role", "username"),
    [(Role.ADMIN, "admin"), (Role.ANALYST, "analyst"), (Role.VIEWER, "viewer")],
    ids=["ADMIN", "ANALYST", "VIEWER"],
)
def test_login_allowed_for_human_roles(
    login_client: Any, role: Role, username: str
) -> None:
    """Los tres roles humanos pueden iniciar sesión (``LOGIN`` = "Sí")."""
    assert SPEC_04_MATRIX[Permission.LOGIN][role] is True
    response = login_client.post("/api/v1/auth/login", json=login_payload(username))
    assert response.status_code == 200


def test_login_is_not_available_to_the_m2m_principal(login_client: Any) -> None:
    """``LOGIN`` = "No" para ``AI_INFORMANT``: no es un ``User`` (SPEC-04 §1)."""
    assert SPEC_04_MATRIX[Permission.LOGIN][Role.AI_INFORMANT] is False
    response = login_client.post(
        "/api/v1/auth/login",
        json={"username": "informant-agent", "password": API_KEY},
    )
    assert response.status_code == 401


# =====================================================================
# Columna M2M: AI_INFORMANT (X-API-Key M2M, SPEC-04 §5.2)
# =====================================================================
def test_informant_can_call_preflight(client: Any) -> None:
    """``postContextPreflight`` permite al principal M2M (SELLO v2.0.0)."""
    response = client.post(
        "/api/v1/context/preflight",
        headers=api_key_header(),
        json={"keywords": ["acme", "juan perez"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["preflight_token"]
    assert body["analyses"]
    assert body["requirements"]


def test_informant_can_ingest_with_valid_preflight_token(
    app: Any, client: Any, preflight: Any, job_registry: Any
) -> None:
    """``createInformantIngestJob`` (M2M) acepta con token vigente ⇒ 202."""
    token = preflight.issue_token(["acme"])
    response = client.post(
        "/api/v1/ingest/informant",
        headers={**api_key_header(), "X-Preflight-Token": token},
        files={
            "file": ("informe.json", b'{"ok": true}', "application/json"),
        },
        data={"payload": json.dumps(INFORMANT_PAYLOAD)},
    )
    assert response.status_code == 202
    job = response.json()
    assert job["job_id"]
    stored = job_registry.get(job["job_id"])
    assert stored["status"] == "DONE"


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        ("GET", "/api/v1/graph/nodes", None),
        ("POST", "/api/v1/analyses", {"json": {
            "code": "AN-9", "title": "T", "thesis_summary": "S"}}),
        ("GET", "/api/v1/coherence/alerts", None),
        ("DELETE", f"/api/v1/graph/nodes/{NODE_ID}", None),
    ],
    ids=["GRAPH_READ", "ANALYSIS_WRITE", "COHERENCE_RESOLVE", "ADMIN_MANAGE"],
)
def test_informant_is_denied_on_human_endpoints(
    client: Any, method: str, path: str, kwargs: Optional[dict[str, Any]]
) -> None:
    """La API Key M2M no alcanza ningún endpoint humano ⇒ 403 (no 401)."""
    response = getattr(client, method.lower())(
        path, headers=api_key_header(), **(kwargs or {})
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


def test_api_key_does_not_authenticate_a_human_session(client: Any) -> None:
    """La API Key M2M no abre una sesión humana (``/auth/me`` es JWT) ⇒ 401."""
    response = client.get("/api/v1/auth/me", headers=api_key_header())
    assert response.status_code == 401


# =====================================================================
# Ingesta ciega prohibida (Constitución Regla 3 · SPEC-03 §5)
# =====================================================================
def test_informant_without_preflight_token_is_409(client: Any) -> None:
    """``POST /ingest/informant`` sin ``X-Preflight-Token`` ⇒ 409 CONFLICT."""
    response = client.post(
        "/api/v1/ingest/informant",
        headers=api_key_header(),
        files={"file": ("informe.json", b'{"ok": true}', "application/json")},
        data={"payload": json.dumps(INFORMANT_PAYLOAD)},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_informant_with_invalid_preflight_token_is_409(client: Any) -> None:
    """Un token de pre-vuelo falsificado ⇒ 409 (nunca 202)."""
    response = client.post(
        "/api/v1/ingest/informant",
        headers={**api_key_header(), "X-Preflight-Token": "falso.inexistente"},
        files={"file": ("informe.json", b'{"ok": true}', "application/json")},
        data={"payload": json.dumps(INFORMANT_PAYLOAD)},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_informant_without_api_key_is_401(client: Any, preflight: Any) -> None:
    """Sin API Key M2M ⇒ 401 (falta de credenciales, no 403)."""
    token = preflight.issue_token(["acme"])
    response = client.post(
        "/api/v1/ingest/informant",
        headers={"X-Preflight-Token": token},
        files={"file": ("informe.json", b'{"ok": true}', "application/json")},
        data={"payload": json.dumps(INFORMANT_PAYLOAD)},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_informant_route_rejects_human_jwt(
    client: Any, tokens: dict[Role, str], preflight: Any
) -> None:
    """``AI_INFORMANT`` es exclusivo del M2M: un JWT humano ⇒ 403."""
    token = preflight.issue_token(["acme"])
    response = client.post(
        "/api/v1/ingest/informant",
        headers={**auth_header(tokens[Role.ANALYST]), "X-Preflight-Token": token},
        files={"file": ("informe.json", b'{"ok": true}', "application/json")},
        data={"payload": json.dumps(INFORMANT_PAYLOAD)},
    )
    assert response.status_code == 403


def test_informant_invalid_payload_is_409(client: Any, preflight: Any) -> None:
    """Payload que no valida contra extraction-schema.json ⇒ 409 (SPEC-02 §6)."""
    token = preflight.issue_token(["acme"])
    response = client.post(
        "/api/v1/ingest/informant",
        headers={**api_key_header(), "X-Preflight-Token": token},
        files={"file": ("informe.json", b'{"ok": true}', "application/json")},
        data={"payload": json.dumps({"report_metadata": {}})},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


# =====================================================================
# Inmutabilidad analítica: lockGraphNode y nodo bloqueado (SPEC-03 §4)
# =====================================================================
def test_viewer_cannot_lock_nodes(client: Any, tokens: dict[Role, str]) -> None:
    """``lockGraphNode`` es "No" para VIEWER ⇒ 403."""
    response = client.post(
        f"/api/v1/graph/nodes/{NODE_ID}/lock",
        headers=auth_header(tokens[Role.VIEWER]),
        json={"locked": True},
    )
    assert response.status_code == 403


def test_analyst_can_lock_node(client: Any, tokens: dict[Role, str]) -> None:
    """``lockGraphNode`` (ANALYST+) marca ``locked_by_analyst`` (Regla 2)."""
    response = client.post(
        f"/api/v1/graph/nodes/{NODE_ID}/lock",
        headers=auth_header(tokens[Role.ANALYST]),
        json={"locked": True},
    )
    assert response.status_code == 200
    assert response.json()["locked_by_analyst"] is True


def test_editing_a_locked_node_is_409(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Editar un nodo bloqueado ⇒ 409 CONFLICT (``NodeLockedError``)."""
    lock = client.post(
        f"/api/v1/graph/nodes/{NODE_ID}/lock",
        headers=auth_header(tokens[Role.ANALYST]),
        json={"locked": True},
    )
    assert lock.status_code == 200
    response = client.put(
        f"/api/v1/graph/nodes/{NODE_ID}",
        headers=auth_header(tokens[Role.ANALYST]),
        json={"canonical_name": "Intento de edición"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


# =====================================================================
# Sin credenciales: 401 (no 403)
# =====================================================================
@pytest.mark.parametrize(
    ("method", "path", "kwargs"), ACTION_REQUESTS, ids=ACTION_IDS
)
def test_unauthenticated_returns_401_not_403(
    client: Any, method: str, path: str, kwargs: Optional[dict]
) -> None:
    """Sin credenciales: 401 (no 403) en las acciones protegidas."""
    response = getattr(client, method.lower())(path, **(kwargs or {}))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_insufficient_role_is_403_even_with_valid_token(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Un token válido con rol insuficiente da 403 y **no** 401 (SPEC-04 §4)."""
    response = client.delete(
        f"/api/v1/graph/nodes/{NODE_ID}", headers=auth_header(tokens[Role.ANALYST])
    )
    assert response.status_code == 403
    body = response.json()
    assert body == {
        "error": {
            "code": "FORBIDDEN",
            "message": "Rol insuficiente: se requiere ADMIN (SPEC-04 §1).",
        }
    }


# =====================================================================
# Trazabilidad contrato ↔ operación ↔ actores (Constitución, Regla 2)
# =====================================================================
def _contract_operation_ids() -> set[str]:
    """``operationId`` declarados en ``contracts/openapi.yaml`` (solo lectura)."""
    contract = Path(__file__).resolve().parents[4] / "contracts" / "openapi.yaml"
    return set(
        re.findall(r"operationId:\s*([A-Za-z0-9_]+)", contract.read_text(encoding="utf-8"))
    )


def test_every_contract_operation_is_implemented_and_has_allowed_roles(
    app: Any,
) -> None:
    """Todo ``operationId`` del contrato existe en la app y tiene actores."""
    contract_ids = _contract_operation_ids()
    assert contract_ids, "No se pudieron leer los operationId del contrato"
    assert set(ALLOWED_ROLES_BY_OPERATION) == contract_ids
    assert set(MIN_ROLE_BY_OPERATION) == contract_ids

    schema = app.openapi()
    implemented: set[str] = set()
    for path_item in schema["paths"].values():
        for method, operation in path_item.items():
            if method.lower() in {"get", "post", "put", "delete", "patch"}:
                implemented.add(operation["operationId"])

    # El contrato declara el WebSocket como GET /ingest/{job_id}/ws (101); en
    # FastAPI es una ruta WebSocket y no se publica en el esquema OpenAPI.
    assert implemented == contract_ids - {"ingestProgressWs"}


def test_endpoint_matrix_is_consistent_with_contract() -> None:
    """``ENDPOINT_MATRIX`` cubre todos los endpoints y respeta la matriz."""
    matrix_ids = {operation_id for _m, _p, operation_id, _perm in ENDPOINT_MATRIX}
    assert matrix_ids == set(MIN_ROLE_BY_OPERATION)
    assert len(ENDPOINT_MATRIX) == len(_contract_operation_ids())

    for _method, _path, operation_id, permission in ENDPOINT_MATRIX:
        allowed = ALLOWED_ROLES_BY_OPERATION[operation_id]
        if allowed is None:
            # ``authLogin`` es público (``security: []``) pese a cubrir la fila
            # ``LOGIN``; ``getInfo`` no tiene fila en la matriz.
            assert operation_id in {"authLogin", "getInfo"}
            continue
        min_role = MIN_ROLE_BY_OPERATION[operation_id]
        if permission is not None and min_role is not None:
            expected_min = MIN_ROLE_BY_PERMISSION[permission]
            if expected_min is not None:
                assert min_role == expected_min
            for role in HUMAN_ROLES:
                assert has_permission(role, permission) == role_satisfies(
                    role, min_role
                )


def test_public_endpoints_are_only_login_and_health(app: Any) -> None:
    """Solo ``authLogin`` y ``getInfo`` son públicos (``security: []``)."""
    schema = app.openapi()
    public = {
        operation["operationId"]
        for path_item in schema["paths"].values()
        for method, operation in path_item.items()
        if method.lower() in {"get", "post", "put", "delete", "patch"}
        and not operation.get("security")
    }
    assert public == {"authLogin", "getInfo"}
