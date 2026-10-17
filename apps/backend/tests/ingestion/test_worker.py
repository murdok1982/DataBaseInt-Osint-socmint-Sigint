"""Verificación del *worker* Celery de ingesta (SPEC-02 §5/§6).

Autorizado por: **SPEC-02 §5** (arquitectura FastAPI + Celery/Redis) y
**SPEC-02 §6** (la subida no bloquea).

Criterios verificados **sin broker ni Neo4j**:

- el nombre de la tarea es estable y trazable a SPEC-02 §5;
- sin ``CELERY_BROKER_URL``/``REDIS_URL`` no hay broker ⇒ ``enqueue_ingest``
  devuelve ``False`` (la API degrada a ``BackgroundTasks``);
- la tarea **nunca** eleva: una composición o ingesta fallida se traduce en un
  dict ``FAILED`` y en el estado del registro de jobs;
- Celery puede ejecutarla *eager* (``task.apply``) sin conexión real.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import app.api.deps as deps
from app.core.jobs import get_job_registry, new_job_payload, reset_job_registry
from app.services.preflight import PreflightRequiredError
from app.workers.celery_app import CELERY_AVAILABLE, broker_configured, broker_url
from app.workers.tasks import (
    ERROR_INGEST_FAILED,
    TASK_NAME,
    enqueue_ingest,
    ingest_report,
    run_ingest_report,
)

TEXT_REPORT = b"Juan Perez controla Acme Ltd. desde 2019."


class _Outcome:
    """Resultado mínimo del pipeline (solo lo que el worker consume)."""

    def __init__(self, pending: list | None = None) -> None:
        self.pending = list(pending or [])


class _FakeExtractor:
    """Extractor de prueba que ejercita la ruta del Informante IA (SPEC-02 §5)."""

    def __init__(self, *, preflight: object | None = None) -> None:
        self.repository = None
        self.preflight = preflight
        self.ingest_informant_calls: list[dict] = []

    def ingest(self, report_id, pages, *, metadata=None, **kwargs):  # noqa: ANN001
        return _Outcome()

    def ingest_informant(
        self, report_id, pages, *, preflight_token, metadata=None, keywords=None,
        reprocess=False,
    ):  # noqa: ANN001
        self.ingest_informant_calls.append(
            {"preflight_token": preflight_token, "keywords": keywords}
        )
        if preflight_token != "valid-token":
            raise PreflightRequiredError("Ingesta ciega prohibida.")
        return _Outcome(pending=[object()])


def _clear_broker_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)


def test_task_name_is_stable() -> None:
    """El nombre de la tarea es el acordado en SPEC-02 §5."""
    assert TASK_NAME == "osint.workers.ingest_report"


def test_broker_is_not_configured_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin variables de broker, ``broker_url``/``broker_configured`` son vacíos."""
    _clear_broker_env(monkeypatch)
    assert broker_url() is None
    assert broker_configured() is False


def test_broker_is_configured_from_redis_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """``REDIS_URL`` sirve de broker de respaldo (SPEC-02 §5)."""
    _clear_broker_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    assert broker_url() == "redis://localhost:6379/0"
    assert broker_configured() is (CELERY_AVAILABLE)


def test_enqueue_returns_false_without_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin broker configurado, el enqueue es un no-op exitoso (degradación)."""
    _clear_broker_env(monkeypatch)
    assert enqueue_ingest("report-sin-broker") is False


def test_run_ingest_report_never_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Sin servicios externos, la tarea devuelve un dict (nunca lanza)."""
    _clear_broker_env(monkeypatch)
    reset_job_registry()
    registry = get_job_registry()
    registry.put("report-1", new_job_payload("report-1"))

    raw = tmp_path / "informe.txt"
    raw.write_bytes(TEXT_REPORT)

    result = run_ingest_report(
        "report-1", raw_path=str(raw), filename="informe.txt", title="Informe"
    )

    assert isinstance(result, dict)
    assert result["report_id"] == "report-1"
    assert result["status"] in {"DONE", "FAILED"}
    stored = registry.get("report-1")
    assert stored is not None
    assert stored["status"] == result["status"]
    reset_job_registry()


def test_run_ingest_report_failure_is_reflected_in_registry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un error de ingesta se registra como ``FAILED`` (nunca propaga)."""
    _clear_broker_env(monkeypatch)
    reset_job_registry()
    registry = get_job_registry()
    registry.put("report-2", new_job_payload("report-2"))

    missing = tmp_path / "no-existe.pdf"
    # PDF ilegible ⇒ ``extract_pages`` eleva ⇒ la tarea lo captura.
    missing.write_bytes(b"%PDF-1.4 contenido corrupto")

    result = run_ingest_report(
        "report-2", raw_path=str(missing), filename="no-existe.pdf"
    )
    assert result["status"] == "FAILED"
    assert "error" in result
    stored = registry.get("report-2")
    assert stored is not None
    assert stored["status"] == "FAILED"
    reset_job_registry()


@pytest.mark.skipif(not CELERY_AVAILABLE, reason="celery no instalado")
def test_task_applies_eagerly_without_broker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Celery ejecuta la tarea *eager* sin conexión al broker (``task.apply``)."""
    _clear_broker_env(monkeypatch)
    reset_job_registry()
    raw = tmp_path / "informe.txt"
    raw.write_bytes(TEXT_REPORT)

    eager: Any = ingest_report.apply(
        kwargs={
            "report_id": "report-3",
            "raw_path": str(raw),
            "filename": "informe.txt",
        }
    )
    payload = eager.get()
    assert payload["report_id"] == "report-3"
    assert payload["status"] in {"DONE", "FAILED"}
    reset_job_registry()


# ---------------------------------------------------------------------
# Pipeline del Informante IA (preflight + coherencia, SPEC-02 §1/§5)
# ---------------------------------------------------------------------
def _seed_job(report_id: str) -> None:
    reset_job_registry()
    get_job_registry().put(report_id, new_job_payload(report_id))


def test_failed_result_carries_error_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Un fallo genérico del pipeline reporta ``error_code`` (contrato de error)."""
    _clear_broker_env(monkeypatch)
    _seed_job("report-err")

    class _Boom(_FakeExtractor):
        def ingest(self, report_id, pages, *, metadata=None, **kwargs):  # noqa: ANN001
            raise RuntimeError("fallo de extracción")

    monkeypatch.setattr(deps, "build_nlp_extractor", lambda: _Boom())
    raw = tmp_path / "informe.txt"
    raw.write_bytes(TEXT_REPORT)

    result = run_ingest_report("report-err", raw_path=str(raw), filename="informe.txt")
    assert result["status"] == "FAILED"
    assert result["error_code"] == ERROR_INGEST_FAILED
    stored = get_job_registry().get("report-err")
    assert stored is not None and stored.get("error_code") == ERROR_INGEST_FAILED
    reset_job_registry()


def test_require_preflight_without_token_fails_with_preflight_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """El Informante IA sin token vigente falla con ``PREFLIGHT_REQUIRED``."""
    _clear_broker_env(monkeypatch)
    _seed_job("report-blind")
    raw = tmp_path / "informe.txt"
    raw.write_bytes(TEXT_REPORT)

    fake = _FakeExtractor(preflight=object())
    monkeypatch.setattr(deps, "build_nlp_extractor", lambda: fake)

    result = run_ingest_report(
        "report-blind",
        raw_path=str(raw),
        filename="informe.txt",
        require_preflight=True,
        preflight_token=None,
    )
    assert result["status"] == "FAILED"
    assert result["error_code"] == "PREFLIGHT_REQUIRED"
    stored = get_job_registry().get("report-blind")
    assert stored is not None and stored["status"] == "FAILED"
    assert stored.get("error_code") == "PREFLIGHT_REQUIRED"
    reset_job_registry()


def test_valid_preflight_token_runs_informant_pipeline(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Con token vigente el worker usa ``ingest_informant`` y reporta ``DONE``."""
    _clear_broker_env(monkeypatch)
    _seed_job("report-ai")
    raw = tmp_path / "informe.txt"
    raw.write_bytes(TEXT_REPORT)

    fake = _FakeExtractor(preflight=object())
    monkeypatch.setattr(deps, "build_nlp_extractor", lambda: fake)

    result = run_ingest_report(
        "report-ai",
        raw_path=str(raw),
        filename="informe.txt",
        require_preflight=True,
        preflight_token="valid-token",
        keywords=["acme"],
    )
    assert result["status"] == "DONE"
    assert result["pending_review"] == 1
    assert fake.ingest_informant_calls[-1]["preflight_token"] == "valid-token"
    reset_job_registry()


def test_enqueue_ingest_accepts_preflight_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """La firma de enqueue es compatible con la ruta del Informante IA."""
    _clear_broker_env(monkeypatch)
    assert (
        enqueue_ingest(
            "report-x",
            require_preflight=True,
            preflight_token="tok",
            keywords=["acme"],
        )
        is False
    )
