"""Tests de contrato — ontología y repositorio v2.0 (SPEC-01 §1–§7).

Verificación Cruzada (Constitución, Regla 5/6). Sin base de datos real: los
métodos de lectura/escritura se ejercitan con un **driver grabador** en memoria
que registra el Cypher y los parámetros enviados (nunca concatenados).
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from app.db.repository import (
    ALLOWED_LABELS,
    ALLOWED_RELATION_TYPES,
    COHERENCE_RELATION_TYPES,
    FULLTEXT_INDEX,
    OSINT_RELATION_TYPES,
    REQUIRED_RELATION_PROPS,
    GraphRepository,
    NodeLockedError,
)

# --- Valores esperados, extraídos literalmente de SPEC-01 §1–§3 (v2.0) ---
EXPECTED_LABELS = {
    "Person",
    "Organization",
    "CyberAsset",
    "Location",
    "Event",
    "Report",
    "Analysis",
    "IntelRequirement",
    "CoherenceAlert",
}

EXPECTED_OSINT_RELATIONS = {
    "CONTROLS",
    "FINANCED",
    "USES_ASSET",
    "LOCATED_AT",
    "ASSOCIATED_WITH",
    "PARTICIPATED_IN",
    "MENTIONED_IN",
}

EXPECTED_COHERENCE_RELATIONS = {
    "INVESTIGATES",
    "HAS_REQUIREMENT",
    "CITES_ANALYSIS",
    "CORROBORATES",
    "CONTRADICTS",
    "EXTENDS",
    "ANSWERS_REQUIREMENT",
    "RAISED_FOR",
    "ABOUT",
}

EXPECTED_RELATION_TYPES = EXPECTED_OSINT_RELATIONS | EXPECTED_COHERENCE_RELATIONS

#: Arista OSINT con TODA la procedencia obligatoria de SPEC-01 §3.1 (v2.0).
#: ``locked_by_analyst`` se omite a propósito: el repositorio lo pone a ``false``.
VALID_EDGE_PROPS = {
    "confidence": 0.9,
    "source_report_id": "report-1",
    "exact_quote": "cita textual",
    "text_evidence_span": "cita textual",
    "page_number": 3,
    "first_seen": "2026-01-01T00:00:00",
    "last_seen": "2026-02-01T00:00:00",
}


# =====================================================================
# Driver grabador (sin Neo4j)
# =====================================================================
class _FakeResult(list):
    """Resultado iterable con ``single()``/``consume()`` (API de neo4j)."""

    def single(self) -> Optional[dict[str, Any]]:
        return self[0] if self else None

    def consume(self) -> "_FakeResult":
        return self


class _RecordingTx:
    def __init__(self, driver: "RecordingDriver") -> None:
        self._driver = driver

    def run(self, cypher: str, **params: Any) -> _FakeResult:
        self._driver.calls.append((cypher, params))
        rows = self._driver._lookup(cypher)
        return _FakeResult(rows)


class _RecordingSession:
    def __init__(self, driver: "RecordingDriver") -> None:
        self._driver = driver

    def execute_read(self, fn: Any) -> Any:
        return fn(_RecordingTx(self._driver))

    def execute_write(self, fn: Any) -> Any:
        return fn(_RecordingTx(self._driver))

    def close(self) -> None:  # pragma: no cover - trivial
        return None


class RecordingDriver:
    """Driver falso que graba ``(cypher, params)`` y devuelve filas cannáticas.

    ``responses`` es una lista de ``(marcador, filas)``; la primera cuyo marcador
    aparezca en el Cypher ejecutado aporta las filas devueltas.
    """

    def __init__(
        self, responses: Optional[list[tuple[str, list[dict[str, Any]]]]] = None
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.responses = list(responses or [])

    def _lookup(self, cypher: str) -> list[dict[str, Any]]:
        for marker, rows in self.responses:
            if marker in cypher:
                return rows
        return []

    def session(self, database: Optional[str] = None) -> _RecordingSession:
        return _RecordingSession(self)

    # -- helpers de test ----------------------------------------------
    @property
    def last_cypher(self) -> str:
        return self.calls[-1][0]

    @property
    def last_params(self) -> dict[str, Any]:
        return self.calls[-1][1]

    def cyphers(self) -> list[str]:
        return [cypher for cypher, _ in self.calls]


# ---------------------------------------------------------------------
# Permit-list (SPEC-01 §1 · §3)
# ---------------------------------------------------------------------
def test_allowed_labels_are_exactly_the_nine_v2():
    assert len(ALLOWED_LABELS) == 9
    assert set(ALLOWED_LABELS) == EXPECTED_LABELS


def test_device_and_financial_account_are_removed():
    assert "Device" not in ALLOWED_LABELS
    assert "FinancialAccount" not in ALLOWED_LABELS
    assert "OWNS_DEVICE" not in ALLOWED_RELATION_TYPES
    assert "HOLDS_ACCOUNT" not in ALLOWED_RELATION_TYPES


def test_osint_relations_are_exactly_seven():
    assert len(OSINT_RELATION_TYPES) == 7
    assert set(OSINT_RELATION_TYPES) == EXPECTED_OSINT_RELATIONS


def test_coherence_relations_are_exactly_nine():
    assert len(COHERENCE_RELATION_TYPES) == 9
    assert set(COHERENCE_RELATION_TYPES) == EXPECTED_COHERENCE_RELATIONS


def test_allowed_relation_types_are_exactly_sixteen():
    assert len(ALLOWED_RELATION_TYPES) == 16
    assert set(ALLOWED_RELATION_TYPES) == EXPECTED_RELATION_TYPES


def test_required_relation_props_match_v2_contract():
    """SPEC-01 §3.1 v2.0: procedencia doble + ``locked_by_analyst``."""
    assert set(REQUIRED_RELATION_PROPS) == {
        "confidence",
        "source_report_id",
        "exact_quote",
        "text_evidence_span",
        "page_number",
        "first_seen",
        "last_seen",
        "locked_by_analyst",
    }
    assert "evidence_quote" not in REQUIRED_RELATION_PROPS


# ---------------------------------------------------------------------
# Validaciones del repositorio (SPEC-01 §3 · §4)
# ---------------------------------------------------------------------
def test_upsert_relationship_rejects_type_outside_spec():
    repo = GraphRepository(driver=None)
    with pytest.raises(ValueError):
        repo.upsert_relationship("a", "b", "HACKS", VALID_EDGE_PROPS)


def test_upsert_relationship_rejects_removed_type():
    repo = GraphRepository(driver=None)
    with pytest.raises(ValueError):
        repo.upsert_relationship("a", "b", "OWNS_DEVICE", VALID_EDGE_PROPS)


def test_upsert_relationship_rejects_missing_mandatory_props():
    repo = GraphRepository(driver=None)
    with pytest.raises(ValueError):
        repo.upsert_relationship("a", "b", "CONTROLS", {"confidence": 0.9})


def test_upsert_relationship_rejects_missing_page_number():
    repo = GraphRepository(driver=None)
    without_page = {k: v for k, v in VALID_EDGE_PROPS.items() if k != "page_number"}
    with pytest.raises(ValueError):
        repo.upsert_relationship("a", "b", "CONTROLS", without_page)


def test_upsert_relationship_rejects_missing_exact_quote():
    repo = GraphRepository(driver=None)
    without_quote = {k: v for k, v in VALID_EDGE_PROPS.items() if k != "exact_quote"}
    with pytest.raises(ValueError):
        repo.upsert_relationship("a", "b", "CONTROLS", without_quote)


def test_upsert_relationship_rejects_confidence_out_of_range():
    repo = GraphRepository(driver=None)
    bad = dict(VALID_EDGE_PROPS, confidence=1.5)
    with pytest.raises(ValueError):
        repo.upsert_relationship("a", "b", "CONTROLS", bad)


def test_upsert_entity_rejects_label_outside_spec():
    repo = GraphRepository(driver=None)
    with pytest.raises(ValueError):
        repo.upsert_entity("Alien", "x1", {})


def test_upsert_entity_rejects_removed_device_label():
    repo = GraphRepository(driver=None)
    with pytest.raises(ValueError):
        repo.upsert_entity("Device", "x1", {})


def test_find_by_unique_key_rejects_non_unique_key():
    repo = GraphRepository(driver=None)
    with pytest.raises(ValueError):
        repo.find_by_unique_key("Person", "canonical_name", "Juan")


# ---------------------------------------------------------------------
# Las plantillas Cypher cumplen el contrato (SPEC-01 §3 · §4 · Regla 2)
# ---------------------------------------------------------------------
def test_upsert_relationship_cypher_includes_mandatory_props():
    cypher = GraphRepository.UPSERT_RELATIONSHIP_TEMPLATE
    for prop in REQUIRED_RELATION_PROPS:
        assert f"${prop}" in cypher
        assert f"r.{prop}" in cypher
    assert "r.exact_quote" in cypher
    assert "r.text_evidence_span" in cypher
    assert "r.locked_by_analyst" in cypher


def test_upsert_relationship_cypher_is_parameterized():
    cypher = GraphRepository.UPSERT_RELATIONSHIP_TEMPLATE
    assert "$from_id" in cypher and "$to_id" in cypher
    assert "$confidence" in cypher


def test_upsert_entity_cypher_merges_by_id_and_merges_aliases():
    cypher = GraphRepository.UPSERT_ENTITY_TEMPLATE
    assert "MERGE (n:" in cypher
    assert "$id" in cypher
    assert "aliases" in cypher
    # No se hace MERGE sobre la propiedad mutable `aliases` (SPEC-01 §6).
    assert "MERGE (n:Person {aliases" not in cypher


def test_upsert_entity_cypher_protects_locked_nodes():
    """Regla 2: un nodo bloqueado no se sobrescribe (contrato §5.1)."""
    cypher = GraphRepository.UPSERT_ENTITY_TEMPLATE
    assert "n.locked_by_analyst" in cypher
    assert "WHEN n.locked_by_analyst THEN" in cypher


def test_find_by_unique_key_template_is_parameterized():
    cypher = GraphRepository.FIND_BY_UNIQUE_KEY_TEMPLATE
    assert "$value" in cypher


def test_fulltext_index_name_matches_contract():
    assert FULLTEXT_INDEX == "entity_names_ft"
    assert FULLTEXT_INDEX in GraphRepository.FULLTEXT_SEARCH_CYPHER
    assert FULLTEXT_INDEX in GraphRepository.LIST_NODES_FULLTEXT_CYPHER
    assert FULLTEXT_INDEX in GraphRepository.SEARCH_NODES_CYPHER


def test_intelligence_queries_use_the_three_spec_templates():
    # SPEC-01 §5.1 / §5.2 / §5.3
    assert "CONTROLS|FINANCED" in GraphRepository.PATHS_OF_INFLUENCE_TEMPLATE
    assert "ASSOCIATED_WITH" in GraphRepository.CRITICAL_BROKERS_TEMPLATE
    assert "USES_ASSET" in GraphRepository.ASSETS_WITH_EVIDENCE_CYPHER
    assert "$min_conf" in GraphRepository.PATHS_OF_INFLUENCE_TEMPLATE
    assert "$asset_value" in GraphRepository.ASSETS_WITH_EVIDENCE_CYPHER


# ---------------------------------------------------------------------
# DDL de constraints (SPEC-01 §6 · contrato SELLADO v2.0.0)
# ---------------------------------------------------------------------
def test_constraint_statements_cover_all_labels_and_fulltext():
    statements = GraphRepository.constraint_statements()
    text = "\n".join(statements)
    assert statements, "El contrato debe aportar sentencias DDL."
    for label in ALLOWED_LABELS:
        assert f":{label})" in text, f"Falta constraint/índice para {label}"
    assert FULLTEXT_INDEX in text
    assert "user_username_unique" in text  # nodo User (contrato v2.0)
    assert "apikey_key_hash_unique" in text  # M2M (contrato v2.0)
    assert "Device" not in text
    assert "FinancialAccount" not in text
    assert all("IF NOT EXISTS" in stmt for stmt in statements)


def test_ensure_constraints_requires_driver():
    repo = GraphRepository(driver=None)
    with pytest.raises(RuntimeError):
        repo.ensure_constraints()


# =====================================================================
# Escritura: procedencia + ``locked_by_analyst`` (SPEC-01 §3.1 v2.0)
# =====================================================================
def test_upsert_relationship_persists_provenance_and_lock_default():
    driver = RecordingDriver(
        [("MERGE (a)-[r:CONTROLS]->(b)", [{"r": {"confidence": 0.9, "page_number": 3}}])]
    )
    repo = GraphRepository(driver)
    repo.upsert_relationship("a", "b", "CONTROLS", VALID_EDGE_PROPS)

    assert "$page_number" in driver.last_cypher
    assert "$exact_quote" in driver.last_cypher
    assert "$locked_by_analyst" in driver.last_cypher
    for prop in REQUIRED_RELATION_PROPS:
        assert prop in driver.last_params
    assert driver.last_params["page_number"] == 3
    assert driver.last_params["source_report_id"] == "report-1"
    assert driver.last_params["exact_quote"] == "cita textual"
    # Por defecto, una arista nueva NO está bloqueada (SPEC-01 §3.1).
    assert driver.last_params["locked_by_analyst"] is False


def test_upsert_relationship_coherence_type_uses_generic_template():
    driver = RecordingDriver(
        [("MERGE (a)-[r:CORROBORATES]->(b)", [{"r": {"confidence": 0.8}}])]
    )
    repo = GraphRepository(driver)
    repo.upsert_relationship(
        "rep1", "an1", "CORROBORATES", {"confidence": 0.8, "justification": "x"}
    )
    assert "$props" in driver.last_cypher
    assert driver.last_params["props"]["confidence"] == 0.8


# =====================================================================
# Inmutabilidad analítica (SPEC-01 §1/§4 · Constitución Regla 2)
# =====================================================================
def test_is_node_locked_reads_flag():
    driver = RecordingDriver(
        [("RETURN coalesce(n.locked_by_analyst", [{"locked": True}])]
    )
    repo = GraphRepository(driver)
    assert repo.is_node_locked("p1") is True


def test_update_node_rejects_locked_node():
    driver = RecordingDriver(
        [("RETURN coalesce(n.locked_by_analyst", [{"locked": True}])]
    )
    repo = GraphRepository(driver)
    with pytest.raises(NodeLockedError):
        repo.update_node("p1", {"canonical_name": "Otro"})
    # No se ejecutó ninguna escritura de actualización.
    assert all("SET n.canonical_name" not in c for c in driver.cyphers())


def test_delete_node_rejects_locked_node():
    driver = RecordingDriver(
        [("RETURN coalesce(n.locked_by_analyst", [{"locked": True}])]
    )
    repo = GraphRepository(driver)
    with pytest.raises(NodeLockedError):
        repo.delete_node("p1")
    assert all("DETACH DELETE" not in c for c in driver.cyphers())


def test_delete_node_proceeds_when_unlocked():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    repo.delete_node("p1")
    assert "DETACH DELETE" in driver.last_cypher
    assert driver.last_params["node_id"] == "p1"


def test_update_node_cypher_guards_lock():
    cypher = GraphRepository.UPDATE_NODE_CYPHER
    assert "coalesce(n.locked_by_analyst, false) = false" in cypher
    assert "coalesce(n.locked_by_analyst, false) = false" in (
        GraphRepository.DELETE_NODE_BY_ID_CYPHER
    )


def test_set_node_locked_marks_node():
    driver = RecordingDriver(
        [
            (
                "SET n.locked_by_analyst = $locked",
                [{"node": {"id": "p1", "locked_by_analyst": True}, "labels": ["Person"]}],
            )
        ]
    )
    repo = GraphRepository(driver)
    result = repo.set_node_locked("p1")
    assert result["locked_by_analyst"] is True
    assert driver.last_params["locked"] is True


def test_lock_edge_marks_relationship():
    driver = RecordingDriver(
        [("SET r.locked_by_analyst = $locked", [{"r": {"locked_by_analyst": True}}])]
    )
    repo = GraphRepository(driver)
    result = repo.lock_edge("p1", "o1", "CONTROLS")
    assert result["locked_by_analyst"] is True
    assert "CONTROLS" in driver.last_cypher  # tipo validado e interpolado
    assert driver.last_params["locked"] is True


def test_lock_edge_rejects_unknown_relationship():
    repo = GraphRepository(driver=None)
    with pytest.raises(ValueError):
        repo.lock_edge("p1", "o1", "HACKS")


# =====================================================================
# Lectura de grafo (SPEC-03 §3) — Cypher parametrizado, nada concatenado
# =====================================================================
def test_list_nodes_projects_graph_node_and_passes_filters():
    node = {
        "id": "p1",
        "canonical_name": "Juan Pérez",
        "aliases": ["J. Pérez"],
        "risk_score": 0.5,
        "locked_by_analyst": True,
        "nationality": "ES",
    }
    driver = RecordingDriver(
        [("MATCH (n)", [{"node": node, "labels": ["Person"]}])]
    )
    repo = GraphRepository(driver)
    result = repo.list_nodes(
        label="Person",
        from_="2026-01-01T00:00:00",
        to="2026-12-31T00:00:00",
        min_confidence=0.5,
        limit=10,
        cursor=None,
    )

    item = result["items"][0]
    assert item["id"] == "p1"
    assert item["label"] == "Person"
    assert item["canonical_name"] == "Juan Pérez"
    assert item["aliases"] == ["J. Pérez"]
    assert item["locked_by_analyst"] is True
    assert item["attributes"] == {"nationality": "ES"}

    # Los valores viajan como parámetros: nunca concatenados en el Cypher.
    assert "Person" not in driver.last_cypher
    assert "2026-01-01T00:00:00" not in driver.last_cypher
    assert driver.last_params["label"] == "Person"
    assert driver.last_params["min_confidence"] == 0.5
    assert driver.last_params["from_time"] == "2026-01-01T00:00:00"
    assert driver.last_params["to_time"] == "2026-12-31T00:00:00"


def test_list_nodes_with_q_uses_fulltext_index():
    driver = RecordingDriver(
        [
            (
                "queryNodes",
                [
                    {
                        "node": {"id": "p1", "canonical_name": "Juan"},
                        "labels": ["Person"],
                        "score": 0.9,
                    }
                ],
            )
        ]
    )
    repo = GraphRepository(driver)
    result = repo.list_nodes(q="Juan")

    assert "entity_names_ft" in driver.last_cypher
    assert driver.last_params["query"] == "Juan"
    assert result["items"][0]["id"] == "p1"


def test_get_node_returns_none_when_missing():
    repo = GraphRepository(RecordingDriver())
    assert repo.get_node("nope") is None


def test_get_node_returns_projection():
    driver = RecordingDriver(
        [("MATCH (n {id: $node_id})", [{"node": {"id": "p1"}, "labels": ["Person"]}])]
    )
    repo = GraphRepository(driver)
    assert repo.get_node("p1")["id"] == "p1"
    assert driver.last_params["node_id"] == "p1"


def test_update_node_sets_props_and_validates():
    updated = {"id": "p1", "canonical_name": "Juan Pérez", "aliases": ["J. Pérez"]}
    driver = RecordingDriver(
        [
            (
                "SET n.canonical_name",
                [{"node": updated, "labels": ["Person"]}],
            )
        ]
    )
    repo = GraphRepository(driver)
    result = repo.update_node(
        "p1",
        {
            "canonical_name": "Juan Pérez",
            "aliases": ["J. Pérez"],
            "attributes": {"nationality": "ES"},
        },
    )
    assert result["canonical_name"] == "Juan Pérez"
    assert driver.last_params["aliases"] == ["J. Pérez"]
    assert driver.last_params["attributes"] == {"nationality": "ES"}


def test_update_node_merge_with_reassigns_duplicates():
    driver = RecordingDriver(
        [("SET n.canonical_name", [{"node": {"id": "p1"}, "labels": ["Person"]}])]
    )
    repo = GraphRepository(driver)
    repo.update_node("p1", {"merge_with": ["dup-1"]})

    detach = [
        params for cypher, params in driver.calls if "DETACH DELETE" in cypher
    ]
    assert detach and detach[-1]["duplicate_id"] == "dup-1"


def test_list_edges_projects_provenance_and_from_to():
    props = dict(VALID_EDGE_PROPS)
    driver = RecordingDriver(
        [
            (
                "MATCH (a)-[r]->(b)",
                [
                    {
                        "id": "e1",
                        "from_id": "p1",
                        "to_id": "o1",
                        "rel_type": "CONTROLS",
                        "props": props,
                    }
                ],
            )
        ]
    )
    repo = GraphRepository(driver)
    edge = repo.list_edges(min_confidence=0.5)["items"][0]

    assert edge["from"] == "p1" and edge["to"] == "o1"
    assert edge["type"] == "CONTROLS"
    assert edge["page_number"] == 3
    assert edge["exact_quote"] == "cita textual"
    assert edge["props"]["source_report_id"] == "report-1"


def test_neighborhood_projects_nodes_and_edges_and_validates_depth():
    driver = RecordingDriver(
        [
            (
                "OPTIONAL MATCH (start)",
                [{"id": "p1", "label": "Person", "props": {"id": "p1"}}],
            ),
            (
                "UNWIND relationships",
                [
                    {
                        "id": "e1",
                        "rel_type": "CONTROLS",
                        "from_id": "p1",
                        "to_id": "o1",
                        "props": dict(VALID_EDGE_PROPS),
                    }
                ],
            ),
        ]
    )
    repo = GraphRepository(driver)
    result = repo.neighborhood("p1", 2)

    assert result["nodes"][0]["id"] == "p1"
    assert result["edges"][0]["type"] == "CONTROLS"

    with pytest.raises(ValueError):
        repo.neighborhood("p1", 4)


# =====================================================================
# CRUD analítico (SPEC-01 §2) — consumido por Agentes 2 y 3
# =====================================================================
def test_create_analysis_persists_and_returns():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    analysis = repo.create_analysis("Tesis X", "resumen", code="AN-1")

    assert "MERGE (n:Analysis" in driver.last_cypher
    assert driver.last_params["props"]["code"] == "AN-1"
    assert driver.last_params["props"]["thesis_summary"] == "resumen"
    assert analysis["code"] == "AN-1"
    assert analysis["status"] == "ACTIVE"
    assert analysis["coherence_score"] == 0.0
    assert analysis["conclusion_body"] is None


def test_create_analysis_rejects_unknown_status():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.create_analysis("T", "r", status="BOGUS")


def test_get_analysis_reads_node():
    driver = RecordingDriver(
        [
            (
                "MATCH (a:Analysis {id: $analysis_id})",
                [{"node": {"id": "a1", "code": "AN-1", "thesis_summary": "t"}}],
            )
        ]
    )
    repo = GraphRepository(driver)
    analysis = repo.get_analysis("a1")
    assert analysis["code"] == "AN-1"
    assert driver.last_params["analysis_id"] == "a1"


def test_update_analysis_filters_fields_and_bumps_updated_at():
    driver = RecordingDriver(
        [("SET a += $patch", [{"node": {"id": "a1", "title": "Nuevo"}}])]
    )
    repo = GraphRepository(driver)
    result = repo.update_analysis(
        "a1", {"title": "Nuevo", "status": "UNDER_REVIEW", "bogus": 1}
    )
    assert result["title"] == "Nuevo"
    assert driver.last_params["patch"] == {"title": "Nuevo", "status": "UNDER_REVIEW"}
    assert driver.last_params["updated_at"]


def test_update_analysis_rejects_unknown_status():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.update_analysis("a1", {"status": "BOGUS"})


def test_list_analyses_passes_status_filter():
    driver = RecordingDriver(
        [
            (
                "WHERE $status IS NULL OR a.status = $status",
                [{"node": {"id": "a1", "status": "ACTIVE"}}],
            )
        ]
    )
    repo = GraphRepository(driver)
    results = repo.list_analyses(status="ACTIVE")
    assert results[0]["id"] == "a1"
    assert driver.last_params["status"] == "ACTIVE"


def test_active_analyses_requires_keywords_and_matches_thesis():
    driver = RecordingDriver(
        [
            (
                "any(k IN $keywords",
                [{"id": "a1", "code": "AN-1", "thesis_summary": "puerto"}],
            )
        ]
    )
    repo = GraphRepository(driver)
    assert repo.active_analyses([]) == []
    results = repo.active_analyses(["puerto"])
    assert results[0]["code"] == "AN-1"
    assert driver.last_params["keywords"] == ["puerto"]


def test_create_requirement_and_links_to_analysis():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    requirement = repo.create_requirement(
        "¿Quién financia?", priority="HIGH", parent_analysis_id="a1"
    )
    assert requirement["status"] == "OPEN"
    assert requirement["priority"] == "HIGH"
    assert any("MERGE (n:IntelRequirement" in c for c in driver.cyphers())
    assert any("HAS_REQUIREMENT" in c for c in driver.cyphers())


def test_create_requirement_rejects_unknown_priority():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.create_requirement("q", priority="URGENT")


def test_get_requirement_reads_node():
    driver = RecordingDriver(
        [
            (
                "MATCH (q:IntelRequirement {id: $requirement_id})",
                [{"node": {"id": "q1", "question": "?", "status": "OPEN"}}],
            )
        ]
    )
    repo = GraphRepository(driver)
    assert repo.get_requirement("q1")["question"] == "?"


def test_list_open_requirements_returns_rows():
    driver = RecordingDriver(
        [
            (
                "MATCH (q:IntelRequirement {status: 'OPEN'})",
                [{"node": {"id": "q1", "question": "?", "priority": "HIGH"}}],
            )
        ]
    )
    repo = GraphRepository(driver)
    results = repo.list_open_requirements()
    assert results[0]["id"] == "q1"


def test_create_alert_links_to_analysis_and_entity():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    alert = repo.create_alert(
        "CONTRADICTION",
        "detalle",
        analysis_id="a1",
        entity_id="e1",
    )
    assert alert["alert_type"] == "CONTRADICTION"
    assert alert["status"] == "PENDING_ANALYST"
    assert alert["analysis_id"] == "a1"
    assert alert["entity_id"] == "e1"
    cyphers = driver.cyphers()
    assert any("MERGE (al:CoherenceAlert" in c for c in cyphers)
    assert any("RAISED_FOR" in c for c in cyphers)
    assert any(":ABOUT" in c for c in cyphers)


def test_create_alert_rejects_unknown_type():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.create_alert("BOGUS", "d")


def test_list_alerts_projects_inbox():
    driver = RecordingDriver(
        [
            (
                "OPTIONAL MATCH (al)-[:RAISED_FOR]->(a:Analysis)",
                [
                    {
                        "node": {
                            "id": "al1",
                            "alert_type": "CONTRADICTION",
                            "status": "PENDING_ANALYST",
                            "description": "d",
                            "created_at": "2026-01-01T00:00:00",
                        },
                        "analysis_id": "a1",
                        "entity_id": "e1",
                    }
                ],
            )
        ]
    )
    repo = GraphRepository(driver)
    alerts = repo.list_alerts(status="PENDING_ANALYST")
    assert alerts[0]["id"] == "al1"
    assert alerts[0]["analysis_id"] == "a1"
    assert driver.last_params["status"] == "PENDING_ANALYST"


def test_resolve_alert_sets_status():
    driver = RecordingDriver(
        [
            (
                "SET al.status = $status, al.resolution_note = $note",
                [{"node": {"id": "al1", "status": "RESOLVED"}}],
            )
        ]
    )
    repo = GraphRepository(driver)
    alert = repo.resolve_alert("al1", "RESOLVED", note="verificado")
    assert alert["status"] == "RESOLVED"
    assert driver.last_params["note"] == "verificado"


def test_resolve_alert_rejects_unknown_status():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.resolve_alert("al1", "PENDING_ANALYST")


# =====================================================================
# Escritores de coherencia (SPEC-01 §3.2 · Coherence Engine, Agente 2)
# =====================================================================
def test_corroborate_bumps_coherence_score_without_touching_thesis():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    result = repo.corroborate(
        "rep1",
        "a1",
        confidence=0.9,
        justification="coincide",
        exact_quote="cita",
        page_number=2,
    )
    cypher = driver.last_cypher
    assert "CORROBORATES" in cypher
    assert "coherence_score" in cypher
    assert "$confidence" in cypher
    assert "conclusion_body" not in cypher  # Regla 7: la tesis nunca se toca
    assert driver.last_params["confidence"] == 0.9
    # SPEC-01 §3.2 v2.0: trazabilidad doble en la arista analítica.
    assert driver.last_params["exact_quote"] == "cita"
    assert driver.last_params["text_evidence_span"] == "cita"
    assert driver.last_params["page_number"] == 2
    assert result["type"] == "CORROBORATES"
    assert result["exact_quote"] == "cita"
    assert result["page_number"] == 2


def test_corroborate_rejects_out_of_range_confidence():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.corroborate("rep1", "a1", confidence=2.0, justification="x", exact_quote="q")


def test_contradict_creates_edge_and_alert_and_keeps_thesis():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    alert = repo.contradict(
        "rep1",
        "a1",
        conflict_detail="discrepancia",
        severity="CRITICAL",
        exact_quote="cita",
        page_number=5,
        entity_id="e1",
    )
    cypher = driver.last_cypher
    assert "CONTRADICTS" in cypher
    assert "CoherenceAlert" in cypher
    assert "RAISED_FOR" in cypher
    assert "ABOUT" in cypher
    assert "conclusion_body" not in cypher  # Regla 9: no borra ni edita la tesis
    assert alert["alert_type"] == "CONTRADICTION"
    assert alert["status"] == "PENDING_ANALYST"
    assert alert["entity_id"] == "e1"
    assert driver.last_params["severity"] == "CRITICAL"
    # SPEC-01 §3.2 v2.0: la arista analítica lleva su trazabilidad doble.
    assert driver.last_params["exact_quote"] == "cita"
    assert driver.last_params["text_evidence_span"] == "cita"
    assert driver.last_params["page_number"] == 5


def test_contradict_rejects_unknown_severity():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.contradict(
            "rep1", "a1", conflict_detail="d", severity="HUGE", exact_quote="q"
        )


def test_extend_analysis_creates_edge():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    result = repo.extend_analysis(
        "rep1",
        "a1",
        new_angle_summary="nuevo ángulo",
        exact_quote="cita del informe",
        page_number=4,
    )
    assert "EXTENDS" in driver.last_cypher
    assert driver.last_params["new_angle_summary"] == "nuevo ángulo"
    assert driver.last_params["exact_quote"] == "cita del informe"
    assert driver.last_params["text_evidence_span"] == "cita del informe"
    assert driver.last_params["page_number"] == 4
    assert result["type"] == "EXTENDS"
    assert result["exact_quote"] == "cita del informe"
    assert result["page_number"] == 4


def test_answer_requirement_marks_answered():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    result = repo.answer_requirement(
        "rep1",
        "q1",
        answer_summary="resuelto",
        confidence=0.7,
        exact_quote="cita de respuesta",
        page_number=7,
    )
    assert "ANSWERS_REQUIREMENT" in driver.last_cypher
    assert "ANSWERED" in driver.last_cypher
    assert driver.last_params["exact_quote"] == "cita de respuesta"
    assert driver.last_params["text_evidence_span"] == "cita de respuesta"
    assert driver.last_params["page_number"] == 7
    assert result["status"] == "ANSWERED"
    assert result["exact_quote"] == "cita de respuesta"


def test_cite_analysis_creates_edge():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    result = repo.cite_analysis(
        "rep1",
        "a1",
        relevance_score=0.6,
        rationale="referencia",
        exact_quote="cita citada",
        page_number=9,
    )
    assert "CITES_ANALYSIS" in driver.last_cypher
    assert driver.last_params["relevance_score"] == 0.6
    assert driver.last_params["exact_quote"] == "cita citada"
    assert driver.last_params["text_evidence_span"] == "cita citada"
    assert driver.last_params["page_number"] == 9
    assert result["type"] == "CITES_ANALYSIS"
    assert result["exact_quote"] == "cita citada"


def test_analytical_edges_persist_provenance_optional():
    """SPEC-01 §3.2 v2.0: ``exact_quote``/``page_number`` son opcionales."""
    for method, kwargs in (
        (
            "extend_analysis",
            {"new_angle_summary": "x"},
        ),
        (
            "answer_requirement",
            {"answer_summary": "x", "confidence": 0.5},
        ),
        (
            "cite_analysis",
            {"relevance_score": 0.5, "rationale": "x"},
        ),
    ):
        driver = RecordingDriver()
        repo = GraphRepository(driver)
        getattr(repo, method)("rep1", "target1", **kwargs)
        # Sin cita no se inventa procedencia: los parámetros viajan como None.
        assert driver.last_params["exact_quote"] is None
        assert driver.last_params["text_evidence_span"] is None
        assert driver.last_params["page_number"] is None


def test_analytical_edges_reject_non_positive_page_number():
    repo = GraphRepository(RecordingDriver())
    with pytest.raises(ValueError):
        repo.extend_analysis(
            "rep1", "a1", new_angle_summary="x", exact_quote="q", page_number=0
        )


def test_analytical_edge_templates_parameterize_provenance():
    """Ninguna arista analítica concatena valores: todo va como ``$param``."""
    for template in (
        GraphRepository.EXTEND_CYPHER,
        GraphRepository.ANSWER_REQUIREMENT_CYPHER,
        GraphRepository.CITE_ANALYSIS_CYPHER,
        GraphRepository.CORROBORATE_CYPHER,
        GraphRepository.CONTRADICT_CYPHER,
    ):
        assert "$exact_quote" in template
        assert "$text_evidence_span" in template
        assert "$page_number" in template
        assert "$report_id" in template


def test_link_analysis_to_entity_creates_investigates():
    driver = RecordingDriver()
    repo = GraphRepository(driver)
    result = repo.link_analysis_to_entity("a1", "e1")
    assert "INVESTIGATES" in driver.last_cypher
    assert driver.last_params["entity_id"] == "e1"
    assert result["type"] == "INVESTIGATES"


# =====================================================================
# Pre-Flight (SPEC-02 §1 Fase 0 · contrato §8)
# =====================================================================
def test_canonical_entities_returns_id_label_name():
    driver = RecordingDriver(
        [
            (
                "MATCH (e:Person|Organization|CyberAsset|Location|Event)",
                [
                    {"id": "p1", "label": "Person", "canonical_name": "Juan"},
                    {"id": "ca1", "label": "CyberAsset", "canonical_name": "1.1.1.1"},
                ],
            )
        ]
    )
    repo = GraphRepository(driver)
    entities = repo.canonical_entities()
    assert entities[0] == {"id": "p1", "label": "Person", "canonical_name": "Juan"}
    assert entities[1]["label"] == "CyberAsset"


# =====================================================================
# Lectura de informes (SPEC-03 §4)
# =====================================================================
def test_get_report_reads_metadata():
    driver = RecordingDriver(
        [
            (
                "RETURN r.id AS id",
                [
                    {
                        "id": "r1",
                        "title": "Informe",
                        "file_path": "/storage/r1.pdf",
                        "ingested_at": "2026-01-01T00:00:00Z",
                        "author_id": "u1",
                    }
                ],
            )
        ]
    )
    repo = GraphRepository(driver)
    report = repo.get_report("r1")

    assert report["id"] == "r1"
    assert report["file_path"] == "/storage/r1.pdf"
    assert driver.last_params["report_id"] == "r1"


def test_get_report_file_path_and_chunks():
    chunks = [
        {"chunk_id": "c1", "page_number": 1, "char_start": 0, "char_end": 5, "text": "hola"}
    ]
    driver = RecordingDriver(
        [
            ("RETURN r.file_path AS file_path", [{"file_path": "/storage/r1.pdf"}]),
            ("RETURN r.chunks AS chunks", [{"chunks": chunks}]),
        ]
    )
    repo = GraphRepository(driver)

    assert repo.get_report_file_path("r1") == "/storage/r1.pdf"
    assert repo.get_report_chunks("r1")["items"][0]["text"] == "hola"


def test_report_reads_return_empty_when_missing():
    repo = GraphRepository(RecordingDriver())
    assert repo.get_report("nope") is None
    assert repo.get_report_file_path("nope") is None
    assert repo.get_report_chunks("nope") == {"items": []}


# =====================================================================
# Adaptador de la interfaz de lectura de GraphRAG (SPEC-03 §5, B5)
# =====================================================================
def test_search_nodes_uses_fulltext_index():
    driver = RecordingDriver(
        [
            (
                "queryNodes",
                [
                    {
                        "node": {"id": "p1", "canonical_name": "Juan"},
                        "labels": ["Person"],
                        "score": 0.9,
                    }
                ],
            )
        ]
    )
    repo = GraphRepository(driver)
    nodes = repo.search_nodes("Juan", limit=5)

    assert nodes[0]["id"] == "p1"
    assert "entity_names_ft" in driver.last_cypher
    assert driver.last_params["limit"] == 5


def test_repository_satisfies_graphrag_reading_interface():
    repo = GraphRepository(RecordingDriver())
    for capability in ("search_nodes", "neighborhood"):
        assert callable(getattr(repo, capability))
