"""Rutas troncales operativas: ya **no** devuelven 503 artificial.

Autorizado por: **SPEC-03 §3–§5** (grafo, informes, ingesta, GraphRAG) y
**SPEC-02 §5/§6** (ingesta no bloqueante).

Antes de cerrar el composition root (SPEC-GAP-3.2/3.3/3.6/3.7), ``/graph/*``,
``/reports/*``, ``POST /ingest`` y ``POST /search/rag`` respondían **503** aunque
el repositorio/el motor estuvieran disponibles, porque faltaba cablear las
dependencias. Este módulo fija el **comportamiento de aceptación**: con los
dobles inyectados, cada ruta troncal responde con su código de éxito.

El 503 queda reservado para una dependencia **realmente** ausente (sin grafo):
se verifica en ``test_service_degradation.py``.
"""

from __future__ import annotations

from typing import Any

from app.api.deps import get_graphrag_engine
from app.core.rbac import Role
from tests.security.conftest import (
    ANALYSIS_ID,
    NODE_ID,
    REPORT_ID,
    auth_header,
)


def test_graph_nodes_route_is_operational(
    client: Any, tokens: dict[Role, str]
) -> None:
    """``listGraphNodes`` (con ``q``) responde 200 y devuelve items."""
    response = client.get(
        "/api/v1/graph/nodes?q=Juan&limit=10",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"]
    assert body["items"][0]["label"] == "Person"


def test_graph_edges_route_is_operational(client: Any, tokens: dict[Role, str]) -> None:
    """``listGraphEdges`` responde 200 con la procedencia completa (``page_number``)."""
    response = client.get(
        "/api/v1/graph/edges", headers=auth_header(tokens[Role.VIEWER])
    )
    assert response.status_code == 200
    edge = response.json()["items"][0]
    assert edge["page_number"] == 1
    assert edge["exact_quote"]


def test_graph_neighborhood_route_is_operational(
    client: Any, tokens: dict[Role, str]
) -> None:
    """``getGraphNeighborhood`` responde 200 con el subgrafo."""
    response = client.get(
        f"/api/v1/graph/neighborhood/{NODE_ID}?depth=1",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 200
    assert response.json()["nodes"]


def test_reports_route_is_operational(client: Any, tokens: dict[Role, str]) -> None:
    """``getReport`` responde 200 con los metadatos del informe."""
    response = client.get(
        f"/api/v1/reports/{REPORT_ID}", headers=auth_header(tokens[Role.VIEWER])
    )
    assert response.status_code == 200
    assert response.json()["id"] == REPORT_ID


def test_search_rag_route_is_operational(client: Any, tokens: dict[Role, str]) -> None:
    """``searchRag`` responde 200 con ``answer`` y ``citations`` (SPEC-03 §5)."""
    response = client.post(
        "/api/v1/search/rag",
        headers=auth_header(tokens[Role.VIEWER]),
        json={"query": "¿Quién controla la sociedad?"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    assert body["citations"]


def test_search_rag_accepts_analysis_only_citations(
    app: Any, client: Any, tokens: dict[Role, str]
) -> None:
    """Las citas de análisis humano (``analysis_id``/``code``) no se rechazan.

    El contrato v2.0.0 solo exige ``exact_quote`` por cita; ``report_id`` y
    ``page_number`` son opcionales (antes el esquema los exigía y rechazaba las
    citas del GraphRAG basadas en ``Analysis``).
    """

    class _AnalysisCitationEngine:
        def answer(self, query: str) -> dict[str, Any]:
            return {
                "answer": "Tesis corroborada por el análisis del expediente.",
                "subgraph": {"nodes": [], "edges": []},
                "citations": [
                    {
                        "analysis_id": ANALYSIS_ID,
                        "code": "AN-0001",
                        "exact_quote": "Juan Pérez controla Acme Ltd.",
                    }
                ],
            }

    app.dependency_overrides[get_graphrag_engine] = lambda: _AnalysisCitationEngine()
    response = client.post(
        "/api/v1/search/rag",
        headers=auth_header(tokens[Role.VIEWER]),
        json={"query": "¿Qué dice el expediente?"},
    )
    assert response.status_code == 200
    citation = response.json()["citations"][0]
    assert citation["analysis_id"] == ANALYSIS_ID
    assert citation["exact_quote"]


def test_ingest_route_is_operational(client: Any, tokens: dict[Role, str]) -> None:
    """``createIngestJob`` responde 202 (subida no bloqueante, SPEC-02 §6)."""
    response = client.post(
        "/api/v1/ingest",
        headers=auth_header(tokens[Role.ANALYST]),
        files={"file": ("informe.txt", b"Juan Perez controla Acme Ltd.", "text/plain")},
        data={"title": "Informe de prueba"},
    )
    assert response.status_code == 202
    assert response.json()["status"] == "QUEUED"


def test_info_route_is_operational(client: Any) -> None:
    """``getInfo`` responde 200 (health público del contrato v1.1.0)."""
    response = client.get("/api/v1/info")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
