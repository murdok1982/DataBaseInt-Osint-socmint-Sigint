"""Tarea de ingesta y *enqueue* tolerante a fallos (SPEC-02 §5/§6).

Autorizado por: **SPEC-02 §5** (estado/progreso del job, arquitectura Celery) y
**SPEC-02 §6** (la subida no bloquea: 202 + progreso).

Diseño
------
- :func:`run_ingest_report` es el cuerpo **puro de Celery**: compone el pipeline
  del Agente 2 (``NLPExtractor`` + ``EntityResolver`` + ``GraphRepository``),
  procesa el informe y refleja el progreso en el registro de jobs. **Nunca**
  eleva: cualquier fallo se traduce en ``FAILED`` en el registro y en el dict de
  retorno (el *worker* no debe morir por una ingesta).
- La ingesta del **Informante IA** (``require_preflight=True`` o con
  ``preflight_token``) pasa por ``ingest_informant``: sin un token vigente el
  pipeline eleva :class:`~app.services.preflight.PreflightRequiredError`, que se
  traduce a ``FAILED`` con ``error_code='PREFLIGHT_REQUIRED'`` (la API responde
  ``409``, SPEC-03 §5 · Constitución Regla 3).
- :data:`ingest_report` es la tarea Celery (si Celery está disponible) o la
  función plana, de modo que ``tests`` puedan invocarla igualmente.
- :func:`enqueue_ingest` devuelve ``False`` salvo que haya un **broker
  configurado** (``CELERY_BROKER_URL``/``REDIS_URL``); así la API degrada a
  ``BackgroundTasks`` en desarrollo y tests sin romper el arranque.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Sequence

from app.workers.celery_app import CELERY_AVAILABLE, broker_configured, celery_app

#: Nombre lógico de la tarea Celery (SPEC-02 §5).
TASK_NAME = "osint.workers.ingest_report"

#: Código de error genérico de una ingesta fallida.
ERROR_INGEST_FAILED = "INGEST_FAILED"


def _pending_count(outcome: Any) -> int:
    """Nº de tripletas pendientes de revisión humana del ``ReportOutcome``."""
    pending = getattr(outcome, "pending", None)
    if pending is None and isinstance(outcome, dict):
        pending = outcome.get("pending")
    try:
        return len(pending or [])
    except TypeError:  # pragma: no cover - salida atípica
        return 0


def _ensure_preflight(extractor: Any) -> Any:
    """Garantiza un ``PreflightService`` composable sobre el extractor.

    ``build_nlp_extractor`` (Agente 3) no cablea el pre-vuelo; el worker lo
    compone perezosamente sobre el repositorio ya inyectado (Fase 0, SPEC-02 §1).
    """
    existing = getattr(extractor, "preflight", None)
    if existing is not None:
        return existing
    repository = getattr(extractor, "repository", None)
    if repository is None:
        return None
    from app.services.preflight import PreflightService

    preflight = PreflightService(repository)
    extractor.preflight = preflight
    return preflight


def _error_code(exc: BaseException) -> str:
    """Código de error estable de una ingesta fallida (contrato de error)."""
    code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return ERROR_INGEST_FAILED


def run_ingest_report(
    report_id: str,
    raw_path: Optional[str] = None,
    filename: Optional[str] = None,
    title: Optional[str] = None,
    *,
    preflight_token: Optional[str] = None,
    keywords: Optional[Sequence[str]] = None,
    require_preflight: bool = False,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Procesa un informe ya almacenado y actualiza el registro de jobs.

    Compone el pipeline de forma perezosa: si el grafo o el LLM no están
    disponibles, la ingesta queda ``FAILED`` en el registro (nunca propaga una
    excepción al *worker*).
    """
    from app.api.deps import build_nlp_extractor, get_job_registry
    from app.core.documents import extract_pages
    from app.core.jobs import STATUS_DONE, STATUS_FAILED, STATUS_PROCESSING

    registry = get_job_registry()
    try:
        extractor = build_nlp_extractor()
        registry.update(report_id, status=STATUS_PROCESSING, progress=0.25)
        content = Path(raw_path).read_bytes() if raw_path else b""
        pages = extract_pages(content, filename)
        payload_metadata = dict(metadata or {})
        if title and "title" not in payload_metadata:
            payload_metadata["title"] = title

        use_preflight = require_preflight or preflight_token is not None
        if use_preflight:
            if _ensure_preflight(extractor) is None:
                raise _preflight_unavailable()
            outcome = extractor.ingest_informant(
                report_id,
                pages,
                preflight_token=preflight_token,
                metadata=payload_metadata,
                keywords=list(keywords or []),
            )
        else:
            outcome = extractor.ingest(report_id, pages, metadata=payload_metadata)

        pending = _pending_count(outcome)
        registry.update(
            report_id, status=STATUS_DONE, progress=1.0, pending_review=pending
        )
        return {
            "report_id": str(report_id),
            "status": STATUS_DONE,
            "progress": 1.0,
            "pending_review": pending,
        }
    except Exception as exc:  # noqa: BLE001 - el worker nunca debe morir
        code = _error_code(exc)
        try:
            registry.update(
                report_id,
                status=STATUS_FAILED,
                progress=1.0,
                error=str(exc),
                error_code=code,
            )
        except Exception:  # pragma: no cover - registro no disponible
            pass
        return {
            "report_id": str(report_id),
            "status": STATUS_FAILED,
            "progress": 1.0,
            "error": str(exc),
            "error_code": code,
        }


def _preflight_unavailable() -> Exception:
    """Error de pre-vuelo no composable (mismo código que la ingesta ciega)."""
    from app.services.preflight import PreflightRequiredError

    return PreflightRequiredError(
        "El pipeline no pudo resolver el pre-vuelo (Fase 0, SPEC-02 §1): "
        "no hay repositorio de grafo disponible."
    )


#: Tarea Celery (o función plana sin Celery instalado).
if CELERY_AVAILABLE and celery_app is not None:  # pragma: no branch
    ingest_report = celery_app.task(name=TASK_NAME, bind=False)(run_ingest_report)
else:  # pragma: no cover - rama dependiente del entorno
    ingest_report = run_ingest_report


def enqueue_ingest(
    report_id: str,
    *,
    raw_path: Optional[str] = None,
    filename: Optional[str] = None,
    title: Optional[str] = None,
    preflight_token: Optional[str] = None,
    keywords: Optional[Sequence[str]] = None,
    require_preflight: bool = False,
) -> bool:
    """Encola la ingesta en Celery; ``False`` si no hay broker configurado."""
    if not broker_configured():
        return False
    apply_async = getattr(ingest_report, "apply_async", None)
    if not callable(apply_async):  # pragma: no cover - defensivo
        return False
    try:
        apply_async(
            kwargs={
                "report_id": str(report_id),
                "raw_path": raw_path,
                "filename": filename,
                "title": title,
                "preflight_token": preflight_token,
                "keywords": list(keywords) if keywords else None,
                "require_preflight": bool(require_preflight),
            }
        )
        return True
    except Exception:  # pragma: no cover - broker caído en caliente
        return False


__all__ = [
    "ERROR_INGEST_FAILED",
    "TASK_NAME",
    "enqueue_ingest",
    "ingest_report",
    "run_ingest_report",
]
