"""Pruebas unitarias de la capa PostgreSQL v3 **sin Docker** (PLAN.md Hito 1).

Las pruebas de integración contra PostgreSQL real viven en ``tests/v3/db`` y
requieren Testcontainers. Aquí se ejercitan el driver y el repositorio con un
pool falso que devuelve filas programadas, de modo que la lógica de sesión
(RLS/clearance, ``break_glass``, propiedad del pool, serialización) queda
verificada también en el entorno local y en CI sin Docker.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

import pytest

from app.db import postgres_driver
from app.db import postgres_repository as repo_module
from app.db.postgres_driver import (
    DEFAULT_CLEARANCE,
    close,
    get_dsn,
    postgres_pool,
    transaction,
    verify_connectivity,
)
from app.db.postgres_repository import PostgresRepository


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchone(self) -> Optional[dict[str, Any]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)


class FakeConnection:
    """Registra cada SQL y sirve, en orden, las filas programadas.

    Las llamadas ``set_config`` (cabecera de sesión de cada transacción) no
    consumen resultados programados; así las pruebas no dependen de ellas.
    """

    def __init__(self, results: Optional[list[list[dict[str, Any]]]] = None) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._results = list(results or [])

    def execute(self, sql: str, params: Any = None) -> FakeResult:
        statement = " ".join(sql.split())
        self.calls.append((statement, params))
        if "set_config" in statement:
            return FakeResult([])
        return FakeResult(self._results.pop(0) if self._results else [])

    @contextmanager
    def transaction(self) -> Iterator["FakeConnection"]:
        yield self

    def session_settings(self) -> dict[str, Any]:
        """Valores que la sesión fijó con ``set_config`` (clearance y break_glass)."""
        settings: dict[str, Any] = {}
        for statement, params in self.calls:
            if "set_config" in statement:
                key = "clearance" if "osint.clearance" in statement else "break_glass"
                settings[key] = params[0]
        return settings


class FakePool:
    def __init__(self, conn: FakeConnection) -> None:
        self.conn = conn
        self.closed = False

    @contextmanager
    def connection(self) -> Iterator[FakeConnection]:
        yield self.conn

    def close(self) -> None:
        self.closed = True

    def wait(self, timeout: Optional[float] = None) -> None:
        return None


# =====================================================================
# Driver
# =====================================================================
def test_get_dsn_reads_postgres_dsn_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POSTGRES_DSN", "postgresql://svc:x@postgres:5432/osint")
    assert get_dsn() == "postgresql://svc:x@postgres:5432/osint"
    monkeypatch.delenv("POSTGRES_DSN")
    assert get_dsn() == ""


def test_create_pool_refuses_to_run_without_dsn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("POSTGRES_DSN", raising=False)
    with pytest.raises(RuntimeError):
        postgres_driver.create_pool(dsn="   ")


def test_verify_connectivity_executes_select_one() -> None:
    conn = FakeConnection()
    assert verify_connectivity(FakePool(conn)) is True
    assert conn.calls[-1][0] == "SELECT 1"


def test_verify_connectivity_rejects_missing_pool() -> None:
    with pytest.raises(ValueError):
        verify_connectivity(None)


def test_close_is_idempotent_and_tolerates_none() -> None:
    close(None)
    pool = FakePool(FakeConnection())
    close(pool)
    assert pool.closed is True


def test_postgres_pool_context_manager_closes_the_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(FakeConnection())
    monkeypatch.setattr(postgres_driver, "create_pool", lambda *a, **k: pool)
    with postgres_pool("postgresql://x") as yielded:
        assert yielded is pool
        assert pool.closed is False
    assert pool.closed is True


def test_transaction_sets_clearance_and_break_glass_for_the_session() -> None:
    conn = FakeConnection()
    with transaction(FakePool(conn), clearance="TLP:AMBER", break_glass=True):
        pass
    assert conn.session_settings() == {"clearance": "TLP:AMBER", "break_glass": "true"}


def test_transaction_defaults_to_least_privilege() -> None:
    conn = FakeConnection()
    with transaction(FakePool(conn)):
        pass
    assert conn.session_settings() == {
        "clearance": DEFAULT_CLEARANCE,
        "break_glass": "false",
    }


# =====================================================================
# Repositorio
# =====================================================================
def test_health_runs_under_the_repository_clearance() -> None:
    conn = FakeConnection()
    repo = PostgresRepository(FakePool(conn), clearance="TLP:GREEN")
    assert repo.health() is True
    assert conn.session_settings()["clearance"] == "TLP:GREEN"


def test_repository_does_not_close_a_pool_it_does_not_own() -> None:
    pool = FakePool(FakeConnection())
    with PostgresRepository(pool):
        pass
    assert pool.closed is False


def test_repository_closes_a_pool_it_created(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(FakeConnection())
    monkeypatch.setattr(repo_module, "create_pool", lambda *a, **k: pool)
    with PostgresRepository(dsn="postgresql://x") as repo:
        assert repo.pool is pool
    assert pool.closed is True


def test_append_evidence_passes_the_hash_and_returns_the_id() -> None:
    evidence_id = "018f2c2a-7a1e-7000-8000-000000000001"
    conn = FakeConnection([[{"evidence_id": evidence_id}]])
    repo = PostgresRepository(FakePool(conn))

    returned = repo.append_evidence(
        idempotency_key="idem-1",
        mission_pir_id=None,
        source_platform="web",
        source_uri="https://example.org/a",
        raw_payload={"k": "v"},
        raw_payload_bytes=b'{"k":"v"}',
        payload_sha256="ab" * 32,
        captured_at_utc=datetime(2026, 10, 24, tzinfo=timezone.utc),
        tlp_classification="TLP:CLEAR",
        collection_method="OSINT",
        legal_basis="interes publico",
    )

    assert returned == evidence_id
    statement, params = next(c for c in conn.calls if "append_evidence" in c[0])
    assert "staging.append_evidence(" in statement
    assert params[0] == "idem-1"
    assert params[6] == "ab" * 32  # el hash viaja tal cual a la función SQL
    assert params[5] == b'{"k":"v"}'  # los bytes exactos, no una re-serialización


def test_verify_evidence_is_false_when_the_ledger_has_no_row() -> None:
    assert PostgresRepository(FakePool(FakeConnection([[]]))).verify_evidence(
        "018f2c2a-7a1e-7000-8000-000000000001"
    ) is False


def test_verify_evidence_reads_the_database_verdict() -> None:
    conn = FakeConnection([[{"ok": True}]])
    assert PostgresRepository(FakePool(conn)).verify_evidence(
        "018f2c2a-7a1e-7000-8000-000000000001"
    ) is True


def test_get_evidence_returns_none_for_unknown_ids() -> None:
    repo = PostgresRepository(FakePool(FakeConnection([[]])))
    assert repo.get_evidence("018f2c2a-7a1e-7000-8000-000000000009") is None


def test_list_evidence_serialises_timestamps_to_iso_strings() -> None:
    captured = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
    conn = FakeConnection([[{"evidence_id": "e1", "captured_at_utc": captured}]])
    rows = PostgresRepository(FakePool(conn)).list_evidence(limit=5)

    assert rows == [{"evidence_id": "e1", "captured_at_utc": captured.isoformat()}]
    _, params = next(c for c in conn.calls if "v_evidence_chain" in c[0])
    assert params[-1] == 5
