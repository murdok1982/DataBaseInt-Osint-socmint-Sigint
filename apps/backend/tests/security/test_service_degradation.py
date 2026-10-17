"""Ingesta y degradación controlada: 503 en lugar de romper el arranque.

Autorizado por: **SPEC-03 §1** (envoltura de errores), **SPEC-02 §5/§6**
(estado/progreso del job, subida no bloqueante) y el prompt del Agente 3 (*si el
módulo no está, responde 503 con* ``{"error": {"code": "SERVICE_UNAVAILABLE"}}``).

Se verifica que:

- el arranque no depende de Neo4j, de Redis ni de los servicios del Agente 2;
- un servicio ausente o un repositorio sin la capacidad solicitada ⇒ **503**;
- el RBAC prevalece (403) aunque el servicio esté caído;
- la ingesta acepta (202) y refleja el progreso del pipeline;
- el WebSocket de progreso respeta el RBAC y degrada de forma controlada.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from starlette.websockets import WebSocketDisconnect

from app.api.deps import (
    authorize_websocket,
    get_graph_gateway,
    get_graphrag_engine,
    get_nlp_extractor,
)
from app.api.graph_gateway import GraphGateway
from app.core.documents import extract_pages
from app.core.errors import ServiceUnavailable
from app.core.jobs import STATUS_DONE, STATUS_FAILED
from app.core.rbac import Role
from app.core.security import create_access_token
from tests.security.conftest import (
    FakeGraphRagEngine,
    REPORT_ID,
    VIEWER_ID,
    auth_header,
)

#: Informe de prueba en texto plano (SPEC-02 §1.1 admite TXT/Markdown/JSON).
TEXT_REPORT = b"Juan Perez controla Acme Ltd. desde 2019."


class _NoCapabilities:
    """Repositorio sin ninguna capacidad (el Agente 1 aún no las expone)."""


def _upload_report(client: Any, token: str, filename: str = "informe.txt") -> Any:
    return client.post(
        "/api/v1/ingest",
        headers=auth_header(token),
        files={"file": (filename, TEXT_REPORT, "text/plain")},
        data={"title": "Informe de prueba"},
    )


# =====================================================================
# Degradación del grafo
# =====================================================================
def test_gateway_raises_service_unavailable_without_capability() -> None:
    """``GraphGateway`` eleva 503 si el repositorio no expone la capacidad."""
    gateway = GraphGateway(_NoCapabilities())
    with pytest.raises(ServiceUnavailable):
        gateway.list_nodes(
            label=None,
            q=None,
            from_=None,
            to=None,
            min_confidence=None,
            limit=50,
            cursor=None,
        )


def test_graph_endpoint_returns_503_when_repository_lacks_read_methods(
    app: Any, client: Any, tokens: dict[Role, str]
) -> None:
    """Aun con rol válido, un repositorio incompleto ⇒ 503 (no 500)."""
    app.dependency_overrides[get_graph_gateway] = lambda: GraphGateway(_NoCapabilities())
    response = client.get(
        "/api/v1/graph/nodes", headers=auth_header(tokens[Role.VIEWER])
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_rbac_still_wins_over_service_availability(
    app: Any, client: Any, tokens: dict[Role, str]
) -> None:
    """La celda "No" sigue dando 403 aunque el servicio esté caído."""
    app.dependency_overrides[get_graph_gateway] = lambda: GraphGateway(_NoCapabilities())
    response = client.put(
        "/api/v1/graph/nodes/00000000-0000-0000-0000-000000000000",
        headers=auth_header(tokens[Role.VIEWER]),
        json={"canonical_name": "X"},
    )
    assert response.status_code == 403


# =====================================================================
# Degradación de los servicios del Agente 2
# =====================================================================
def test_ingest_returns_503_without_composed_extractor(
    app: Any, client: Any, tokens: dict[Role, str]
) -> None:
    """Sin extractor compuesto (SPEC-02 §5) la ingesta responde 503."""
    app.dependency_overrides.pop(get_nlp_extractor, None)
    response = _upload_report(client, tokens[Role.ANALYST])
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_search_returns_503_without_composed_engine(
    app: Any, client: Any, tokens: dict[Role, str]
) -> None:
    """Sin motor GraphRAG compuesto (SPEC-03 §5) la búsqueda responde 503."""
    app.dependency_overrides.pop(get_graphrag_engine, None)
    response = client.post(
        "/api/v1/search/rag",
        headers=auth_header(tokens[Role.VIEWER]),
        json={"query": "¿Quién controla la sociedad?"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


def test_graphrag_answer_without_citations_returns_no_evidence_200(
    app: Any, client: Any, tokens: dict[Role, str]
) -> None:
    """SPEC-03 §6 / B7: sin citas se responde 200 con ``citations: []``."""
    app.dependency_overrides[get_graphrag_engine] = (
        lambda: FakeGraphRagEngine(with_citations=False)
    )
    response = client.post(
        "/api/v1/search/rag",
        headers=auth_header(tokens[Role.VIEWER]),
        json={"query": "¿Quién controla la sociedad?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["citations"] == []
    assert body["answer"]  # respuesta explícita de "sin evidencia"


def test_graphrag_answer_with_citations_is_returned(
    client: Any, tokens: dict[Role, str]
) -> None:
    """Respuesta GraphRAG válida: ``answer`` + ``citations`` (SPEC-03 §5)."""
    response = client.post(
        "/api/v1/search/rag",
        headers=auth_header(tokens[Role.VIEWER]),
        json={"query": "¿Quién controla la sociedad?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert body["citations"][0]["report_id"] == REPORT_ID
    assert body["citations"][0]["exact_quote"]


# =====================================================================
# Flujo de ingesta (SPEC-02 §5/§6)
# =====================================================================
def test_ingest_accepts_and_completes_the_job(
    client: Any, tokens: dict[Role, str], job_registry: Any
) -> None:
    """202 + job ``DONE`` con ``pending_review`` del pipeline (SPEC-02 §6)."""
    response = _upload_report(client, tokens[Role.ANALYST])
    assert response.status_code == 202
    job = response.json()
    assert job["job_id"]
    assert job["status"] == "QUEUED"  # respuesta inmediata, sin bloquear

    stored = job_registry.get(job["job_id"])
    assert stored["status"] == STATUS_DONE
    assert stored["progress"] == 1.0

    status_response = client.get(
        f"/api/v1/ingest/{job['job_id']}", headers=auth_header(tokens[Role.VIEWER])
    )
    assert status_response.status_code == 200
    assert status_response.json()["status"] == STATUS_DONE
    assert status_response.json()["pending_review"] == 1


def test_ingest_stores_the_raw_report(
    client: Any, tokens: dict[Role, str], storage_dir: Path
) -> None:
    """El informe íntegro queda en ``STORAGE_RAW_DIR`` (SPEC-02 §1.2)."""
    job = _upload_report(client, tokens[Role.ANALYST]).json()
    stored_files = list(storage_dir.glob(f"{job['job_id']}*"))
    assert len(stored_files) == 1
    assert stored_files[0].read_bytes() == TEXT_REPORT


def test_ingest_failure_is_reflected_as_failed(
    client: Any,
    tokens: dict[Role, str],
    fake_extractor: Any,
    job_registry: Any,
) -> None:
    """Un fallo del pipeline no rompe la API: el job queda ``FAILED``."""
    fake_extractor.fail = True
    job = _upload_report(client, tokens[Role.ANALYST]).json()
    assert job_registry.get(job["job_id"])["status"] == STATUS_FAILED
    status_response = client.get(
        f"/api/v1/ingest/{job['job_id']}", headers=auth_header(tokens[Role.VIEWER])
    )
    assert status_response.status_code == 200


def test_ingest_job_not_found_returns_404(client: Any, tokens: dict[Role, str]) -> None:
    """Job inexistente ⇒ 404 con el sobre del contrato."""
    response = client.get(
        "/api/v1/ingest/no-existe", headers=auth_header(tokens[Role.VIEWER])
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_extract_pages_reads_text_and_reports_broken_pdf() -> None:
    """Extracción de páginas: texto OK; PDF ilegible ⇒ 503, nunca 500."""
    assert extract_pages(b"pagina uno\fpagina dos", "informe.txt") == [
        "pagina uno",
        "pagina dos",
    ]
    with pytest.raises(ServiceUnavailable):
        extract_pages(b"%PDF-1.4 contenido corrupto", "informe.pdf")


def test_health_endpoint_is_public(app: Any) -> None:
    """``GET /api/v1/info`` (``getInfo``) no requiere token (health del despliegue)."""
    from fastapi.testclient import TestClient

    response = TestClient(app).get("/api/v1/info")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "environment" in body
    assert body["version"]


# =====================================================================
# WebSocket de progreso (ingestProgressWs) — RBAC y degradación
# =====================================================================
def test_websocket_requires_valid_token(client: Any) -> None:
    """Sin token ⇒ cierre 4401 (no se acepta el socket)."""
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with client.websocket_connect("/api/v1/ingest/cualquiera/ws"):
            pass
    assert excinfo.value.code == 4401


def test_websocket_unknown_job_closes_4404(client: Any, tokens: dict[Role, str]) -> None:
    """Job desconocido ⇒ cierre 4404 tras autenticar (SPEC-02 §5)."""
    with client.websocket_connect(
        f"/api/v1/ingest/no-existe/ws?token={tokens[Role.VIEWER]}"
    ) as websocket:
        with pytest.raises(WebSocketDisconnect) as excinfo:
            websocket.receive_json()
    assert excinfo.value.code == 4404


def test_websocket_viewer_receives_progress(
    client: Any, tokens: dict[Role, str]
) -> None:
    """VIEWER autenticado recibe el progreso del job (SPEC-03 §4)."""
    job = _upload_report(client, tokens[Role.ANALYST]).json()
    with client.websocket_connect(
        f"/api/v1/ingest/{job['job_id']}/ws?token={tokens[Role.VIEWER]}"
    ) as websocket:
        event = websocket.receive_json()
    assert event["job_id"] == job["job_id"]
    assert event["status"] == STATUS_DONE
    assert event["progress"] == 1.0


def test_websocket_authorization_is_403_equivalent_when_role_is_insufficient(
    users: Any,
) -> None:
    """Rol insuficiente ⇒ 403 en el handshake (equivalente WS del RBAC)."""
    import asyncio

    token = create_access_token(VIEWER_ID, Role.VIEWER.value)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(authorize_websocket(token, Role.ADMIN, users))
    assert excinfo.value.status_code == 403
