"""Aceptación E2E — ciclo simbiótico Analista ↔ Informante IA (SPEC-00 §3).

Recorre el ciclo completo **in-process** (``fastapi.testclient.TestClient``,
sin Neo4j ni Redis) verificando SPEC-00 §6, SPEC-02 §1/§6, SPEC-03 §3–§7 y
SPEC-04 §1/§4:

1. El **Analista** crea un ``Analysis`` (tesis) y un ``IntelRequirement`` (RFI).
2. El **Informante IA** (API Key M2M) consulta ``POST /context/preflight`` y
   recibe contexto + ``preflight_token``.
3. **Anti-ingesta-ciega**: ``POST /ingest/informant`` sin token ⇒ ``409``; con
   token vigente ⇒ ``202``.
4. **Coherence Engine** (real): ``CORROBORATES`` sube ``coherence_score``;
   ``CONTRADICTS`` crea ``CoherenceAlert`` y **no** altera ``conclusion_body``.
5. **Grafo**: ``/graph/nodes``, ``/graph/nodes/{id}`` y ``/graph/neighborhood/{id}``.
6. **Inmutabilidad**: ``POST /graph/nodes/{id}/lock`` + ``PUT`` ⇒ ``409``.
7. **Bandeja de coherencia**: listar y resolver la alerta.
8. **Dossier**: ``/reports/{id}``, ``/reports/{id}/file`` y ``/reports/{id}/chunks``.
9. **GraphRAG**: ``POST /search/rag`` con citas (y guardia de no-evidencia).
10. **RBAC**: ``AI_INFORMANT`` no crea/edita expedientes ni resuelve alertas;
    ``VIEWER`` no bloquea nodos.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from app.core.rbac import Role
from tests.security.conftest import (
    API_KEY,
    NODE_ID,
    REPORT_ID,
    api_key_header,
    auth_header,
)

#: Tesis del analista: única autoridad sobre la verdad (SPEC-00 §4).
CONCLUSION = "Tesis verificada por el analista: Acme Ltd. financia criptografía."
#: Cita exacta que acompaña a los vínculos analíticos (SPEC-02 §2).
LINK_QUOTE = "Acme Ltd. financió la compra de equipo criptográfico en 2024."
CONCLUSION_SENTINEL = CONCLUSION


# =====================================================================
# Helpers de petición
# =====================================================================
def _analysis_payload() -> dict[str, Any]:
    return {
        "code": "AN-E2E-1",
        "title": "Expediente simbiótico E2E",
        "thesis_summary": "Acme Ltd. financia la compra de criptografía.",
        "conclusion_body": CONCLUSION,
    }


def _informant_payload(title: str = "Informe del informante IA") -> dict[str, Any]:
    """Payload mínimo válido contra ``contracts/extraction-schema.json`` (v2.0)."""
    return {
        "report_metadata": {
            "title": title,
            "source_type": "NEWS",
            "source_url_or_origin": "https://example.test/nota",
            "reliability_rating": "B",
        },
        "extracted_triplets": [],
        "analytical_context_links": [],
    }


def _link(analysis_id: str, category: str, confidence: float) -> dict[str, Any]:
    """``analytical_context_link`` del contrato v2.0 (SPEC-02 §2)."""
    return {
        "target_id": analysis_id,
        "link_category": category,
        "confidence": confidence,
        "coherence_justification": "El informe aborda la tesis del expediente.",
        "exact_quote": LINK_QUOTE,
    }


def _ingest_informant(
    client: Any,
    informant_headers: dict[str, str],
    token: Optional[str],
    *,
    title: str = "Informe del informante IA",
) -> Any:
    """``POST /ingest/informant`` con (o sin) ``X-Preflight-Token``."""
    headers = dict(informant_headers)
    if token is not None:
        headers["X-Preflight-Token"] = token
    return client.post(
        "/api/v1/ingest/informant",
        headers=headers,
        files={"file": ("informe.json", b'{"body": "cuerpo del informe OSINT"}', "application/json")},
        data={"payload": json.dumps(_informant_payload(title))},
    )


def _assert_accepted_and_done(
    client: Any,
    response: Any,
    job_registry: Any,
    viewer_headers: dict[str, str],
) -> str:
    """La ingesta responde ``202`` y el pipeline real termina en ``DONE``."""
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    stored = job_registry.get(job_id)
    assert stored is not None, "El job M2M no quedó registrado."
    assert stored["status"] == "DONE", (
        f"El pipeline del Informante IA falló (job {stored}): "
        f"revisa el error registrado."
    )
    # El contrato expone el estado del job (SPEC-02 §5).
    status = client.get(f"/api/v1/ingest/{job_id}", headers=viewer_headers)
    assert status.status_code == 200
    assert status.json()["status"] == "DONE"
    return job_id


# =====================================================================
# Ciclo completo
# =====================================================================
def test_symbiotic_cycle_end_to_end(
    app: Any,
    client: Any,
    repo: Any,
    llm: Any,
    engine: Any,
    job_registry: Any,
    tokens: dict[Any, str],
) -> None:
    analyst = auth_header(tokens[Role.ANALYST])
    viewer = auth_header(tokens[Role.VIEWER])
    informant = api_key_header()

    # -----------------------------------------------------------------
    # 1) El Analista crea el Analysis (tesis) y el IntelRequirement (RFI)
    # -----------------------------------------------------------------
    created = client.post("/api/v1/analyses", headers=analyst, json=_analysis_payload())
    assert created.status_code == 201, created.text
    analysis = created.json()
    analysis_id = analysis["id"]
    assert analysis["conclusion_body"] == CONCLUSION_SENTINEL
    assert analysis["coherence_score"] == 0.0
    assert analysis["status"] == "ACTIVE"

    requirement = client.post(
        f"/api/v1/analyses/{analysis_id}/requirements",
        headers=analyst,
        json={"question": "¿Quién controla Acme Ltd.?", "priority": "HIGH"},
    )
    assert requirement.status_code == 201, requirement.text
    requirement_id = requirement.json()["id"]
    assert requirement.json()["parent_analysis_id"] == analysis_id
    assert requirement.json()["status"] == "OPEN"

    # -----------------------------------------------------------------
    # 2) Informante IA: Pre-Flight (contexto + preflight_token)
    # -----------------------------------------------------------------
    preflight = client.post(
        "/api/v1/context/preflight",
        headers=informant,
        json={"keywords": ["acme", "juan perez"]},
    )
    assert preflight.status_code == 200, preflight.text
    context = preflight.json()
    token = context["preflight_token"]
    assert token
    assert any(item["id"] == analysis_id for item in context["analyses"]), (
        "El Pre-Flight no devolvió el Analysis activo (SPEC-02 §1 Fase 0)."
    )
    assert any(item["id"] == requirement_id for item in context["requirements"]), (
        "El Pre-Flight no devolvió el RFI abierto (SPEC-02 §1 Fase 0)."
    )
    assert any(
        entity.get("canonical_name") == "Juan Pérez"
        for entity in context["canonical_entities"]
    ), "El Pre-Flight no devolvió el diccionario canónico (SPEC-02 §1 Fase 0)."

    # -----------------------------------------------------------------
    # 3) Anti-ingesta-ciega (Constitución Regla 3 · SPEC-03 §5)
    # -----------------------------------------------------------------
    blind = _ingest_informant(client, informant, token=None)
    assert blind.status_code == 409, blind.text
    assert blind.json()["error"]["code"] == "CONFLICT"

    accepted = _ingest_informant(client, informant, token, title="Informe neutral")
    _assert_accepted_and_done(client, accepted, job_registry, viewer)

    # -----------------------------------------------------------------
    # 4) Coherence Engine (real, vía pipeline M2M)
    # -----------------------------------------------------------------
    # 4a) CORROBORATES ⇒ sube coherence_score y conserva la tesis.
    llm.set_payload(links=[_link(analysis_id, "CORROBORATES", 0.7)])
    corroborating = _ingest_informant(client, informant, token, title="Informe que corrobora")
    _assert_accepted_and_done(client, corroborating, job_registry, viewer)

    detail = client.get(f"/api/v1/analyses/{analysis_id}", headers=analyst)
    assert detail.status_code == 200
    assert detail.json()["coherence_score"] == pytest.approx(0.7), (
        "CORROBORATES debe incrementar coherence_score (SPEC-02 §1.6)."
    )
    assert detail.json()["conclusion_body"] == CONCLUSION_SENTINEL
    assert len(repo.corroborations) == 1

    # 4b) CONTRADICTS ⇒ crea CoherenceAlert y NO altera conclusion_body.
    llm.set_payload(links=[_link(analysis_id, "CONTRADICTS", 0.9)])
    contradicting = _ingest_informant(client, informant, token, title="Informe que contradice")
    _assert_accepted_and_done(client, contradicting, job_registry, viewer)

    detail = client.get(f"/api/v1/analyses/{analysis_id}", headers=analyst)
    assert detail.json()["conclusion_body"] == CONCLUSION_SENTINEL, (
        "CONTRADICTS NUNCA debe alterar conclusion_body (SPEC-02 §1.7)."
    )
    assert detail.json()["coherence_score"] == pytest.approx(0.7), (
        "CONTRADICTS no debe subir coherence_score."
    )
    assert repo.contradictions, "No se registró la arista [:CONTRADICTS]."
    assert repo.contradictions[0]["severity"] == "CRITICAL"
    # Regla 7/9: el motor de coherencia jamás invoca la autoridad humana.
    assert repo.update_analysis_calls == [], (
        "El CoherenceEngine invocó update_analysis (viola Reglas 7/9)."
    )

    # -----------------------------------------------------------------
    # 7) Bandeja de coherencia: listar y resolver (ANALYST)
    # -----------------------------------------------------------------
    alerts = client.get("/api/v1/coherence/alerts", headers=analyst)
    assert alerts.status_code == 200, alerts.text
    items = alerts.json()["items"]
    mine = [
        alert
        for alert in items
        if alert.get("analysis_id") == analysis_id
        and alert.get("alert_type") == "CONTRADICTION"
    ]
    assert mine, "La contradicción no generó CoherenceAlert (SPEC-02 §1.7)."
    alert_id = mine[0]["id"]
    assert mine[0]["status"] == "PENDING_ANALYST"

    resolved = client.patch(
        f"/api/v1/coherence/alerts/{alert_id}/resolve",
        headers=analyst,
        json={"status": "RESOLVED", "resolution_note": "Revisada por el analista."},
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["status"] == "RESOLVED"

    # -----------------------------------------------------------------
    # 5) Grafo
    # -----------------------------------------------------------------
    nodes = client.get("/api/v1/graph/nodes", headers=viewer)
    assert nodes.status_code == 200, nodes.text
    assert nodes.json()["items"], "GET /graph/nodes no devolvió nodos."

    node = client.get(f"/api/v1/graph/nodes/{NODE_ID}", headers=viewer)
    assert node.status_code == 200, node.text
    assert node.json()["id"] == NODE_ID
    assert node.json()["label"] == "Person"

    neighborhood = client.get(
        f"/api/v1/graph/neighborhood/{NODE_ID}", headers=viewer
    )
    assert neighborhood.status_code == 200, neighborhood.text
    assert any(item["id"] == NODE_ID for item in neighborhood.json()["nodes"])
    assert "edges" in neighborhood.json()

    # -----------------------------------------------------------------
    # 6) Inmutabilidad analítica (Constitución Regla 2 · SPEC-03 §4)
    # -----------------------------------------------------------------
    locked = client.post(
        f"/api/v1/graph/nodes/{NODE_ID}/lock",
        headers=analyst,
        json={"locked": True},
    )
    assert locked.status_code == 200, locked.text
    assert locked.json()["locked_by_analyst"] is True

    edit_locked = client.put(
        f"/api/v1/graph/nodes/{NODE_ID}",
        headers=analyst,
        json={"canonical_name": "Intento de sobrescritura"},
    )
    assert edit_locked.status_code == 409, edit_locked.text
    assert edit_locked.json()["error"]["code"] == "CONFLICT"

    # -----------------------------------------------------------------
    # 8) Dossier (informe original, metadatos y chunks)
    # -----------------------------------------------------------------
    report = client.get(f"/api/v1/reports/{REPORT_ID}", headers=viewer)
    assert report.status_code == 200, report.text
    assert report.json()["id"] == REPORT_ID

    report_file = client.get(f"/api/v1/reports/{REPORT_ID}/file", headers=viewer)
    assert report_file.status_code == 200, report_file.text
    assert report_file.content.startswith(b"%PDF")

    chunks = client.get(f"/api/v1/reports/{REPORT_ID}/chunks", headers=viewer)
    assert chunks.status_code == 200, chunks.text
    assert chunks.json()["items"]

    # -----------------------------------------------------------------
    # 9) GraphRAG (SPEC-03 §7 / §8: nunca afirma sin citas)
    # -----------------------------------------------------------------
    rag = client.post(
        "/api/v1/search/rag",
        headers=viewer,
        json={"query": "¿Quién controla Acme Ltd.?"},
    )
    assert rag.status_code == 200, rag.text
    body = rag.json()
    assert body["citations"], "GraphRAG debe citar la evidencia usada."
    assert body["citations"][0]["exact_quote"]

    # Guardia de no-evidencia: sin citas ⇒ 200 con ``citations: []``.
    engine.with_citations = False
    rag_empty = client.post(
        "/api/v1/search/rag",
        headers=viewer,
        json={"query": "Consulta sin evidencia"},
    )
    assert rag_empty.status_code == 200
    assert rag_empty.json()["citations"] == []
    assert rag_empty.json()["answer"]

    # -----------------------------------------------------------------
    # 10) RBAC (SPEC-04 §1/§4: cada "No" ⇒ 403)
    # -----------------------------------------------------------------
    # El principal M2M no crea expedientes…
    denied_analysis = client.post(
        "/api/v1/analyses", headers=informant, json=_analysis_payload()
    )
    assert denied_analysis.status_code == 403, denied_analysis.text
    assert denied_analysis.json()["error"]["code"] == "FORBIDDEN"

    # …ni resuelve alertas de coherencia.
    denied_resolve = client.patch(
        f"/api/v1/coherence/alerts/{alert_id}/resolve",
        headers=informant,
        json={"status": "RESOLVED"},
    )
    assert denied_resolve.status_code == 403

    # Un VIEWER no bloquea nodos verificados.
    denied_lock = client.post(
        f"/api/v1/graph/nodes/{NODE_ID}/lock",
        headers=viewer,
        json={"locked": True},
    )
    assert denied_lock.status_code == 403

    # La API Key M2M tampoco puede listar expedientes ni consultar el grafo.
    assert client.get("/api/v1/graph/nodes", headers=informant).status_code == 403

    # Sanidad: el token M2M de laboratorio es el esperado.
    assert informant["X-API-Key"] == API_KEY
