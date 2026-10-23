"""Fixtures de integración PostgreSQL v3 (Hito 1).

Los tests se saltan si Docker no está disponible. Un skip **no** cuenta como
verificación de aceptación: el Hito 1 exige ejecutarlos en CI con Docker.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

import pytest

ROOT = Path(__file__).resolve().parents[4]
CONTRACT = ROOT / "contracts" / "v3" / "db-contract.sql"


@dataclass(frozen=True)
class PgEnv:
    dsn_owner: str
    dsn_hermes: str
    dsn_atenea: str
    dsn_api: str
    mission_id: str
    pir_id: str
    task_id: str
    closed_mission_id: str
    closed_pir_id: str


def _connect(dsn: str, retries: int = 30) -> Any:
    import psycopg
    from psycopg.rows import dict_row

    last: Exception | None = None
    for _ in range(retries):
        try:
            return psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        except Exception as exc:  # pragma: no cover - depende de Docker
            last = exc
            time.sleep(0.5)
    raise RuntimeError(f"No se pudo conectar a PostgreSQL: {last}")


@pytest.fixture(scope="session")
def pg() -> Iterator[PgEnv]:
    pytest.importorskip("psycopg")
    pytest.importorskip("testcontainers.postgres")
    from testcontainers.postgres import PostgresContainer

    try:
        container = PostgresContainer(
            "pgvector/pgvector:pg16",
            username="postgres",
            password="postgres",
            dbname="osint",
        )
        container.start()
    except Exception as exc:  # pragma: no cover - Docker ausente
        pytest.skip(f"Docker/Testcontainers no disponible: {exc}")

    host = container.get_container_host_ip()
    port = container.get_exposed_port(5432)
    owner_dsn = f"postgresql://postgres:postgres@{host}:{port}/osint"
    try:
        with _connect(owner_dsn) as conn:
            sql = CONTRACT.read_text(encoding="utf-8")
            # psycopg3: prepare=False usa el protocolo simple y acepta múltiples
            # sentencias (el contrato es un script DDL completo).
            conn.execute(sql, prepare=False)
            conn.execute(
                """
                ALTER ROLE svc_hermes PASSWORD 'hermes';
                ALTER ROLE svc_atenea PASSWORD 'atenea';
                ALTER ROLE svc_api PASSWORD 'api';
                """,
                prepare=False,
            )

            mission = conn.execute(
                """
                INSERT INTO intel.missions
                    (code, name, status, legal_basis, created_by)
                VALUES ('M-H1', 'Hito 1', 'ACTIVE', 'test de integración',
                        'DIRECTOR_CELL')
                RETURNING mission_id
                """
            ).fetchone()["mission_id"]
            pir = conn.execute(
                """
                INSERT INTO intel.pirs
                    (mission_id, code, question, priority, status, created_by)
                VALUES (%s, 'PIR-001', '¿Quién coordina la campaña?', 1, 'OPEN',
                        'DIRECTOR_CELL')
                RETURNING pir_id
                """,
                (mission,),
            ).fetchone()["pir_id"]
            task = conn.execute(
                """
                INSERT INTO intel.tasking_orders
                    (mission_id, pir_id, assignee_agent, objective, status, issued_by)
                VALUES (%s, %s, 'HERMES', 'Recolectar evidencia', 'ISSUED', 'DIRECTOR_CELL')
                RETURNING task_id
                """,
                (mission, pir),
            ).fetchone()["task_id"]
            conn.execute(
                """
                INSERT INTO humint.opsec_policies
                    (mission_id, max_interactions_per_day, max_messages_per_target,
                     forbidden_topics, created_by)
                VALUES (%s, 20, 5, ARRAY['menores'], 'DIRECTOR_CELL')
                """,
                (mission,),
            )

            closed_mission = conn.execute(
                """
                INSERT INTO intel.missions
                    (code, name, status, legal_basis, created_by)
                VALUES ('M-CLOSED', 'Misión cerrada', 'ACTIVE', 'test',
                        'DIRECTOR_CELL')
                RETURNING mission_id
                """
            ).fetchone()["mission_id"]
            closed_pir = conn.execute(
                """
                INSERT INTO intel.pirs
                    (mission_id, code, question, priority, status, created_by)
                VALUES (%s, 'PIR-002', '¿Pregunta cerrada?', 2, 'OPEN',
                        'DIRECTOR_CELL')
                RETURNING pir_id
                """,
                (closed_mission,),
            ).fetchone()["pir_id"]
            conn.execute(
                """
                INSERT INTO intel.tasking_orders
                    (mission_id, pir_id, assignee_agent, objective, status, issued_by)
                VALUES (%s, %s, 'HERMES', 'Tarea cerrada', 'ISSUED', 'DIRECTOR_CELL')
                """,
                (closed_mission, closed_pir),
            )
            conn.execute(
                """
                UPDATE intel.missions
                SET status = 'CLOSED', closed_at = now()
                WHERE mission_id = %s
                """,
                (closed_mission,),
            )
    except Exception:
        container.stop()
        raise

    env = PgEnv(
        dsn_owner=owner_dsn,
        dsn_hermes=f"postgresql://svc_hermes:hermes@{host}:{port}/osint",
        dsn_atenea=f"postgresql://svc_atenea:atenea@{host}:{port}/osint",
        dsn_api=f"postgresql://svc_api:api@{host}:{port}/osint",
        mission_id=str(mission),
        pir_id=str(pir),
        task_id=str(task),
        closed_mission_id=str(closed_mission),
        closed_pir_id=str(closed_pir),
    )
    try:
        yield env
    finally:
        container.stop()


def evidence_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


__all__ = ["PgEnv", "pg", "evidence_bytes"]
