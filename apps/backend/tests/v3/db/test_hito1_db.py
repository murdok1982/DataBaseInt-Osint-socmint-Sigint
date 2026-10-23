"""Tests de aceptación T-H1-01…T-H1-10 (PostgreSQL real).

Requieren Docker + Testcontainers. Si Docker no está disponible, el fixture
``pg`` marca los tests como skip; el Hito 1 no se considera cerrado hasta que
se ejecuten en CI.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.db.postgres_repository import PostgresRepository

from .conftest import PgEnv, evidence_bytes

try:  # pragma: no cover - depende del entorno
    import psycopg
    from psycopg.rows import dict_row  # noqa: E402
except Exception:  # pragma: no cover
    psycopg = None  # type: ignore[assignment]
    dict_row = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    psycopg is None, reason="psycopg no instalado (requirements-dev.txt)"
)


def _append(
    repo: PostgresRepository,
    pg: PgEnv,
    *,
    payload: dict[str, Any] | None = None,
    sha256: str | None = None,
    mission_pir_id: str | None = None,
) -> str:
    body = payload or {"text": "evidencia de prueba", "n": 1}
    raw = evidence_bytes(body)
    digest = sha256 or hashlib.sha256(raw).hexdigest()
    return repo.append_evidence(
        idempotency_key=uuid.uuid4().hex,
        mission_pir_id=mission_pir_id or pg.pir_id,
        source_platform="test",
        source_uri="https://example.invalid/evidence",
        raw_payload=body,
        raw_payload_bytes=raw,
        payload_sha256=digest,
        captured_at_utc=datetime.now(timezone.utc) - timedelta(hours=1),
        tlp_classification="TLP:AMBER",
        collection_method="test",
        legal_basis="test de integración",
        attachments=[],
        metadata={"suite": "hito1"},
    )


def _entity(
    repo: PostgresRepository,
    evidence_id: str,
    *,
    name: str = "Sujeto de prueba",
    rating: str = "B2",
    tlp: str = "TLP:AMBER",
) -> str:
    return repo.upsert_entity(
        stix_type="identity",
        name=name,
        admiralty_rating=rating,
        tlp_classification=tlp,
        evidence_ids=[evidence_id],
        aliases=["alias de prueba"],
        properties={"role": "test"},
    )


def _sqlstate(exc: BaseException) -> str | None:
    return getattr(exc, "sqlstate", None)


def test_t_h1_01_hash_mismatch(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as repo:
        with pytest.raises(Exception) as exc:
            _append(repo, pg, sha256="0" * 64)
    assert _sqlstate(exc.value) == "23514"


def test_t_h1_02_ledger_append_only(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as repo:
        evidence_id = _append(repo, pg)

    with psycopg.connect(pg.dsn_hermes, autocommit=True) as conn:
        for statement, allowed in (
            ("UPDATE staging.raw_intel_ledger SET source_platform = 'x'", {"42501"}),
            ("DELETE FROM staging.raw_intel_ledger", {"42501"}),
            # TRUNCATE puede fallar por privilegio (42501) o por FKs (0A000);
            # ambos demuestran que el rol de colector no puede vaciar el ledger.
            ("TRUNCATE staging.raw_intel_ledger", {"42501", "0A000"}),
        ):
            with pytest.raises(Exception) as exc:
                conn.execute(statement)
            assert _sqlstate(exc.value) in allowed
    assert evidence_id


def test_t_h1_03_entity_requires_evidence(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_atenea) as repo:
        with pytest.raises(Exception) as exc:
            repo.upsert_entity(
                stix_type="identity",
                name="Sin evidencia",
                admiralty_rating="B2",
                tlp_classification="TLP:AMBER",
                evidence_ids=[],
            )
        assert _sqlstate(exc.value) == "23514"

        with pytest.raises(Exception) as exc:
            repo.upsert_entity(
                stix_type="identity",
                name="Evidencia inexistente",
                admiralty_rating="B2",
                tlp_classification="TLP:AMBER",
                evidence_ids=[str(uuid.uuid4())],
            )
        assert _sqlstate(exc.value) == "23503"


def test_t_h1_04_relationship_requires_evidence(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        evidence_id = _append(hermes, pg)
    with PostgresRepository(dsn=pg.dsn_atenea) as atenea:
        left = _entity(atenea, evidence_id, name="Izquierda")
        right = _entity(atenea, evidence_id, name="Derecha")

        with pytest.raises(Exception) as exc:
            atenea.upsert_relationship(
                relationship_type="RELATED_TO",
                source_entity_id=left,
                target_entity_id=right,
                admiralty_rating="B2",
                evidence_ids=[],
            )
        assert _sqlstate(exc.value) == "23514"

        rel = atenea.upsert_relationship(
            relationship_type="RELATED_TO",
            source_entity_id=left,
            target_entity_id=right,
            admiralty_rating="B2",
            evidence_ids=[evidence_id],
        )
    assert rel


def test_t_h1_05_admiralty_and_confidence(pg: PgEnv) -> None:
    with psycopg.connect(pg.dsn_atenea) as conn:
        assert conn.execute(
            "SELECT intel.admiralty_confidence('A1')"
        ).fetchone()[0] == 100
        assert conn.execute(
            "SELECT intel.admiralty_confidence('F6')"
        ).fetchone()[0] == 10
        with pytest.raises(Exception) as exc:
            conn.execute("SELECT intel.admiralty_confidence('Z9')")
        assert _sqlstate(exc.value) == "23514"

    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        evidence_id = _append(hermes, pg, payload={"text": "fuente única"})
    with PostgresRepository(dsn=pg.dsn_atenea) as atenea:
        entity_id = _entity(atenea, evidence_id, name="Confianza F6", rating="F6")
    with PostgresRepository(dsn=pg.dsn_api, clearance="TLP:AMBER") as api:
        node = api.get_node(entity_id)
    assert node is not None
    assert node["confidence"] == 15  # base 10 + bonus 5 por una fuente


def test_t_h1_06_temporal_overlap(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        evidence_id = _append(hermes, pg, payload={"text": "temporal"})
    with PostgresRepository(dsn=pg.dsn_atenea) as atenea:
        left = _entity(atenea, evidence_id, name="Temporal A")
        right = _entity(atenea, evidence_id, name="Temporal B")
        atenea.upsert_relationship(
            relationship_type="RELATED_TO",
            source_entity_id=left,
            target_entity_id=right,
            admiralty_rating="B2",
            evidence_ids=[evidence_id],
            valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
            valid_until=datetime(2026, 12, 31, tzinfo=timezone.utc),
        )
        with pytest.raises(Exception) as exc:
            atenea.upsert_relationship(
                relationship_type="RELATED_TO",
                source_entity_id=left,
                target_entity_id=right,
                admiralty_rating="B2",
                evidence_ids=[evidence_id],
                valid_from=datetime(2026, 6, 1, tzinfo=timezone.utc),
                valid_until=datetime(2027, 1, 1, tzinfo=timezone.utc),
            )
    assert _sqlstate(exc.value) == "23P01"


def test_t_h1_07_hermes_cannot_write_graph(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        evidence_id = _append(hermes, pg, payload={"text": "separación"})
        with pytest.raises(Exception) as exc:
            hermes.upsert_entity(
                stix_type="identity",
                name="Hermes no escribe",
                admiralty_rating="B2",
                tlp_classification="TLP:AMBER",
                evidence_ids=[evidence_id],
            )
    assert _sqlstate(exc.value) == "42501"


def test_t_h1_08_orphan_check(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        evidence_id = _append(hermes, pg, payload={"text": "huérfano"})
    with PostgresRepository(dsn=pg.dsn_atenea) as atenea:
        _entity(atenea, evidence_id, name="Con evidencia")
    with PostgresRepository(dsn=pg.dsn_api, clearance="TLP:AMBER") as api:
        assert api.orphan_check() == []


def test_t_h1_09_rls_clearance(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        evidence_id = _append(hermes, pg, payload={"text": "TLP"})
    with PostgresRepository(dsn=pg.dsn_atenea) as atenea:
        _entity(atenea, evidence_id, name="Entidad TLP AMBER", tlp="TLP:AMBER")

    with PostgresRepository(dsn=pg.dsn_api, clearance="TLP:CLEAR") as clear:
        names_clear = {n["name"] for n in clear.list_nodes()}
    with PostgresRepository(dsn=pg.dsn_api, clearance="TLP:AMBER") as amber:
        names_amber = {n["name"] for n in amber.list_nodes()}

    assert "Entidad TLP AMBER" not in names_clear
    assert "Entidad TLP AMBER" in names_amber


def test_t_h1_10_closed_mission_and_audit(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        _append(hermes, pg, payload={"text": "auditoria"})
        with pytest.raises(Exception) as exc:
            _append(hermes, pg, mission_pir_id=pg.closed_pir_id)
        assert _sqlstate(exc.value) == "23514"

    with psycopg.connect(pg.dsn_owner, autocommit=True) as conn:
        with pytest.raises(Exception) as exc:
            conn.execute(
                """
                INSERT INTO intel.tasking_orders
                    (mission_id, pir_id, assignee_agent, objective, status, issued_by)
                VALUES (%s, %s, 'HERMES', 'Tarea tardía', 'ISSUED', 'DIRECTOR_CELL')
                """,
                (pg.closed_mission_id, pg.closed_pir_id),
            )
        assert _sqlstate(exc.value) == "23514"

        with pytest.raises(Exception) as exc:
            conn.execute("UPDATE audit.events SET action = 'TAMPERED'")
        assert _sqlstate(exc.value) == "42501"

        with pytest.raises(Exception) as exc:
            conn.execute("DELETE FROM audit.events")
        assert _sqlstate(exc.value) == "42501"


def test_ledger_hash_chain_and_verify(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        first = _append(hermes, pg, payload={"text": "cadena 1"})
        second = _append(hermes, pg, payload={"text": "cadena 2"})
    with psycopg.connect(pg.dsn_owner, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT evidence_id, prev_hash, record_hash
            FROM staging.raw_intel_ledger
            ORDER BY ledger_seq DESC
            LIMIT 2
            """
        ).fetchall()
    assert len(rows) == 2
    newer, older = rows
    assert str(newer["evidence_id"]) == second
    assert str(older["evidence_id"]) == first
    assert newer["prev_hash"] == older["record_hash"]

    with PostgresRepository(dsn=pg.dsn_api, clearance="TLP:AMBER") as api:
        assert api.verify_evidence(first) is True
        assert api.verify_evidence(second) is True


def test_evidence_metadata_is_tlp_filtered(pg: PgEnv) -> None:
    with PostgresRepository(dsn=pg.dsn_hermes) as hermes:
        evidence_id = _append(hermes, pg, payload={"text": "metadatos"})
    with PostgresRepository(dsn=pg.dsn_api, clearance="TLP:CLEAR") as clear:
        ids = {e["evidence_id"] for e in clear.list_evidence()}
    with PostgresRepository(dsn=pg.dsn_api, clearance="TLP:AMBER") as amber:
        ids_amber = {e["evidence_id"] for e in amber.list_evidence()}
    assert evidence_id not in ids
    assert evidence_id in ids_amber
