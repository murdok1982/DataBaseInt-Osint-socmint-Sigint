"""Tests de integración de la migración v3.1 (SPEC-01 §12, SPEC-02 §11.5).

Construyen la imagen desde ``deploy/postgres/Dockerfile`` (PostgreSQL 16 +
pgvector + PostGIS, la misma que producción), aplican el DDL v3.0 y después la
migración real ``migrations/v3.1/0001_schema.sql``. Ningún test usa un doble:
la atestación, la RLS y los permisos se comprueban contra PostgreSQL real.

Sin Docker el módulo se salta. Un skip no cuenta como verificación de
aceptación; en CI (``.github/workflows/v3-db.yml``) Docker está disponible.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

import pytest

from app.db.attestation import issue_attestation
from .conftest import _connect

ROOT = Path(__file__).resolve().parents[5]  # repo osint-nexus-platform
V30_CONTRACT = ROOT / "contracts" / "v3" / "db-contract.sql"
V31_MIGRATION = ROOT / "migrations" / "v3.1" / "0001_schema.sql"
POSTGRES_DOCKERFILE_DIR = ROOT / "deploy" / "postgres"

API_PASSWORD = "api-reader-test-pass"
HERMES_PASSWORD = "hermes-test-pass"
OWNER_PASSWORD = "postgres"
ATTESTATION_KEY = "clave-de-prueba-atestacion-0123456789abcdef"
WRONG_KEY = "otra-clave-que-no-es-la-buena-0123456789abcdef"


@dataclass(frozen=True)
class Pg31Env:
    host: str
    port: int
    dbname: str

    def dsn(self, user: str, password: str) -> str:
        return f"postgresql://{user}:{password}@{self.host}:{self.port}/{self.dbname}"


@pytest.fixture(scope="session")
def pg31_image() -> str:
    """Imagen de PostgreSQL con PostGIS: la de CI/local o la del Dockerfile."""
    override = os.environ.get("POSTGRES_TEST_IMAGE", "").strip()
    if override:
        return override
    try:
        from testcontainers.core.image import DockerImage
    except ImportError:  # pragma: no cover - dependencia de desarrollo
        pytest.skip("testcontainers no instalado")
    try:
        image = DockerImage(
            path=str(POSTGRES_DOCKERFILE_DIR),
            tag="osint-nexus-postgres:test-v31",
        )
        image.build()
    except Exception as exc:  # pragma: no cover - Docker ausente
        pytest.skip(f"No se pudo construir la imagen PostgreSQL: {exc}")
    return str(image)


@pytest.fixture(scope="session")
def pg31(pg31_image: str) -> Iterator[Pg31Env]:
    pytest.importorskip("psycopg")
    try:
        from testcontainers.community.postgres import PostgresContainer
    except ImportError:  # pragma: no cover - versiones anteriores de testcontainers
        from testcontainers.postgres import PostgresContainer

    try:
        container = PostgresContainer(
            pg31_image, username="postgres", password=OWNER_PASSWORD, dbname="osint"
        )
        container.start()
    except Exception as exc:  # pragma: no cover - Docker ausente
        pytest.skip(f"Docker/Testcontainers no disponible: {exc}")

    env = Pg31Env(
        host=container.get_container_host_ip(),
        port=int(container.get_exposed_port(5432)),
        dbname="osint",
    )
    try:
        with _connect(env.dsn("postgres", OWNER_PASSWORD)) as conn:
            conn.execute(V30_CONTRACT.read_text(encoding="utf-8"), prepare=False)
            conn.execute(V31_MIGRATION.read_text(encoding="utf-8"), prepare=False)
            conn.execute(
                f"ALTER ROLE api_reader PASSWORD '{API_PASSWORD}';"
                f"ALTER ROLE svc_hermes PASSWORD '{HERMES_PASSWORD}';",
                prepare=False,
            )
            # La clave la inserta el despliegue desde el gestor de secretos.
            conn.execute(
                "INSERT INTO staging.attestation_keys (secret) VALUES (%s)",
                (ATTESTATION_KEY,),
            )
        yield env
    finally:
        container.stop()


def _api(env: Pg31Env, clearance: str) -> Any:
    """Conexión como ``api_reader`` con el clearance que fija la API para lecturas."""
    conn = _connect(env.dsn("api_reader", API_PASSWORD))
    conn.execute("SELECT set_config('osint.clearance', %s, false)", (clearance,))
    return conn


def _seed_proposal(env: Pg31Env, tlp: str) -> UUID:
    """Inserta una propuesta ``AGENT_STAGED`` como propietario (sin RLS)."""
    with _connect(env.dsn("postgres", OWNER_PASSWORD)) as conn:
        mission = conn.execute(
            """
            INSERT INTO intel.missions (code, name, status, legal_basis, created_by)
            VALUES (%s, 'Migración v3.1', 'ACTIVE', 'test de integración', 'DIRECTOR_CELL')
            RETURNING mission_id
            """,
            (f"M-{os.urandom(4).hex()}",),
        ).fetchone()["mission_id"]
        run = conn.execute(
            """
            INSERT INTO audit.hermes_agent_runs (agent_type, mission_id, status, finished_at)
            VALUES ('HERMES', %s, 'COMPLETED', now())
            RETURNING run_id
            """,
            (mission,),
        ).fetchone()["run_id"]
        return conn.execute(
            """
            INSERT INTO staging.agent_proposals
                (run_id, agent_type, mission_id, proposal_type, payload,
                 payload_sha256, idempotency_key, tlp_classification)
            VALUES (%s, 'HERMES', %s, 'OBSERVABLE', '{}'::jsonb, %s, %s, %s::staging.tlp_enum)
            RETURNING proposal_id
            """,
            (run, mission, "a" * 64, os.urandom(32).hex(), tlp),
        ).fetchone()["proposal_id"]


def _attest(
    proposal: UUID,
    *,
    actor: str = "analista-1",
    clearance: str = "TLP:RED",
    key: str = ATTESTATION_KEY,
    status: str = "ANALYST_VERIFIED",
    version: int = 1,
    ttl_seconds: int = 60,
) -> str:
    return issue_attestation(
        key,
        proposal_id=str(proposal),
        status=status,
        version=version,
        actor=actor,
        clearance=clearance,
        ttl_seconds=ttl_seconds,
    )


def _decide(conn: Any, proposal: UUID, attestation: str, reason: str = "Verificado contra la fuente") -> list[dict]:
    return conn.execute(
        """
        SELECT proposal_id, status, reviewed_by
        FROM staging.fn_decide_proposal(%s::uuid, 'ANALYST_VERIFIED'::intel.proposal_status_enum,
                                        %s, 1, %s)
        """,
        (proposal, reason, attestation),
    ).fetchall()


def _status(env: Pg31Env, proposal_id: UUID) -> str:
    with _connect(env.dsn("postgres", OWNER_PASSWORD)) as conn:
        return conn.execute(
            "SELECT status::text AS status FROM staging.agent_proposals WHERE proposal_id = %s",
            (proposal_id,),
        ).fetchone()["status"]


def test_migracion_v31_aplica_sobre_v30(pg31: Pg31Env) -> None:
    with _connect(pg31.dsn("postgres", OWNER_PASSWORD)) as conn:
        found = conn.execute(
            "SELECT to_regprocedure("
            "'staging.fn_decide_proposal(uuid,intel.proposal_status_enum,text,integer,text)'"
            ") IS NOT NULL AS ok"
        ).fetchone()["ok"]
    assert found, "fn_decide_proposal con atestación debe existir"


def test_fn_decide_no_acepta_identidad_ni_clearance_del_cliente(pg31: Pg31Env) -> None:
    with _connect(pg31.dsn("postgres", OWNER_PASSWORD)) as conn:
        args = conn.execute(
            """
            SELECT pg_get_function_identity_arguments(p.oid) AS args
            FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
            WHERE n.nspname = 'staging' AND p.proname = 'fn_decide_proposal'
            """
        ).fetchall()
    assert len(args) == 1
    assert "p_reviewer" not in args[0]["args"]
    assert "p_clearance" not in args[0]["args"]
    assert "p_attestation" in args[0]["args"]


def test_fn_decide_rechaza_propuesta_por_encima_del_clearance_firmado(pg31: Pg31Env) -> None:
    proposal = _seed_proposal(pg31, "TLP:RED")
    conn = _api(pg31, clearance="TLP:RED")
    try:
        assert _decide(conn, proposal, _attest(proposal, clearance="TLP:GREEN")) == []
    finally:
        conn.close()
    assert _status(pg31, proposal) == "AGENT_STAGED"


def test_fn_decide_registra_como_revisor_el_firmado(pg31: Pg31Env) -> None:
    proposal = _seed_proposal(pg31, "TLP:RED")
    conn = _api(pg31, clearance="TLP:RED")
    try:
        rows = _decide(conn, proposal, _attest(proposal, actor="analista-7"))
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["reviewed_by"] == "analista-7"
    assert _status(pg31, proposal) == "ANALYST_VERIFIED"


def test_fn_decide_rechaza_atestacion_con_otra_clave(pg31: Pg31Env) -> None:
    proposal = _seed_proposal(pg31, "TLP:CLEAR")
    conn = _api(pg31, clearance="TLP:RED")
    try:
        with pytest.raises(Exception, match="no válida"):
            _decide(conn, proposal, _attest(proposal, key=WRONG_KEY))
    finally:
        conn.close()
    assert _status(pg31, proposal) == "AGENT_STAGED"


def test_fn_decide_rechaza_atestacion_sin_firma(pg31: Pg31Env) -> None:
    proposal = _seed_proposal(pg31, "TLP:CLEAR")
    conn = _api(pg31, clearance="TLP:RED")
    try:
        with pytest.raises(Exception, match="malformada|no válida"):
            _decide(conn, proposal, "9999999999|analista-1|TLP:RED|")
    finally:
        conn.close()
    assert _status(pg31, proposal) == "AGENT_STAGED"


def test_fn_decide_rechaza_atestacion_caducada(pg31: Pg31Env) -> None:
    proposal = _seed_proposal(pg31, "TLP:CLEAR")
    conn = _api(pg31, clearance="TLP:RED")
    try:
        with pytest.raises(Exception, match="caducada"):
            _decide(conn, proposal, _attest(proposal, ttl_seconds=-30))
    finally:
        conn.close()
    assert _status(pg31, proposal) == "AGENT_STAGED"


def test_fn_decide_no_reutiliza_atestacion_de_otra_propuesta(pg31: Pg31Env) -> None:
    first = _seed_proposal(pg31, "TLP:CLEAR")
    second = _seed_proposal(pg31, "TLP:CLEAR")
    attestation = _attest(first)
    conn = _api(pg31, clearance="TLP:RED")
    try:
        with pytest.raises(Exception, match="no válida"):
            _decide(conn, second, attestation)
    finally:
        conn.close()
    assert _status(pg31, second) == "AGENT_STAGED"


def test_api_reader_no_puede_leer_ni_firmar_con_la_clave(pg31: Pg31Env) -> None:
    import psycopg

    conn = _api(pg31, clearance="TLP:RED")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT secret FROM staging.attestation_keys")
    finally:
        conn.close()


def test_verify_evidence_chain_solo_para_roles_autorizados(pg31: Pg31Env) -> None:
    import psycopg

    conn = _api(pg31, clearance="TLP:CLEAR")
    try:
        assert conn.execute("SELECT staging.verify_evidence_chain() AS ok").fetchone()["ok"] is True
    finally:
        conn.close()

    hermes = _connect(pg31.dsn("svc_hermes", HERMES_PASSWORD))
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            hermes.execute("SELECT staging.verify_evidence_chain()")
    finally:
        hermes.close()


def test_rls_oculta_propuestas_fuera_del_clearance(pg31: Pg31Env) -> None:
    proposal = _seed_proposal(pg31, "TLP:RED")
    conn = _api(pg31, clearance="TLP:GREEN")
    try:
        visible = conn.execute(
            "SELECT 1 FROM staging.agent_proposals WHERE proposal_id = %s", (proposal,)
        ).fetchall()
    finally:
        conn.close()
    assert visible == []


def test_consulta_por_clave_usa_indice(pg31: Pg31Env) -> None:
    """EXPLAIN de la lectura por clave: el índice de la PK debe ser utilizable."""
    proposal = _seed_proposal(pg31, "TLP:CLEAR")
    with _connect(pg31.dsn("postgres", OWNER_PASSWORD)) as conn:
        conn.execute("SET enable_seqscan = off")
        plan = "\n".join(
            row["QUERY PLAN"]
            for row in conn.execute(
                "EXPLAIN SELECT * FROM staging.agent_proposals WHERE proposal_id = %s",
                (proposal,),
            ).fetchall()
        )
    assert "Index" in plan, plan
