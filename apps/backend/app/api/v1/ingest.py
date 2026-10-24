"""Rutas de ingesta (subida del informe y progreso en vivo).

Autorizado por: **SPEC-03 §4** (``/ingest``), **SPEC-04 §1** (matriz RBAC),
**SPEC-02 §1** (flujo de ingesta), **SPEC-02 §5** (estado/progreso en Redis) y
**SPEC-02 §6** (*la subida no bloquea: 202 + progreso por WS*).

| operationId        | Método | Ruta                    | Rol mínimo |
| :----------------- | :----- | :---------------------- | :--------- |
| ``createIngestJob``| POST   | ``/ingest``             | ANALYST    |
| ``createInformantIngestJob``| POST | ``/ingest/informant`` | AI_INFORMANT (M2M) |
| ``getIngestJob``   | GET    | ``/ingest/{job_id}``    | VIEWER     |
| ``ingestProgressWs``| WS    | ``/ingest/{job_id}/ws`` | VIEWER     |

Reparto de responsabilidades
----------------------------
- La **API** (Agente 3): recibe el archivo, lo almacena íntegro
  (``app/core/documents.py``, SPEC-02 §1.2), registra el job
  (``app/core/jobs.py``, SPEC-02 §5) y orquesta el pipeline.
- El **NLP** (Agente 2, ``app.services.nlp_extractor``): ``chunk → extract →
  validate → resolve``. Se inyecta por dependencia; si su composición no está
  disponible la ruta responde **503** (nunca rompe el arranque).

**SPEC-GAP-3.6 (resuelto).** SPEC-02 §5 prevé un *worker* Celery. La API encola
la ingesta en Celery cuando hay un **broker explícitamente configurado**
(``CELERY_BROKER_URL``/``REDIS_URL``) y, si no, ejecuta el pipeline síncrono en
``BackgroundTasks`` (devolviendo 202 inmediatamente, SPEC-02 §6) manteniendo el
estado en el registro compartido. El arranque y los tests nunca requieren broker.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import uuid
from enum import Enum
from typing import Any, AsyncIterator, Callable, Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from pydantic import BaseModel, Field

from app.api.deps import (
    authorize_websocket,
    get_informant_extractor,
    get_job_registry,
    get_nlp_extractor,
    get_optional_graph_gateway,
    get_preflight_service,
    service_unavailable,
)
from app.core.config import get_settings
from app.core.documents import (
    UploadRejected,
    extract_pages,
    read_upload_limited,
    report_metadata,
    save_raw_report,
    validate_upload,
)
from app.core.errors import CODE_CONFLICT, CODE_NOT_FOUND, detail_from_code
from app.core.jobs import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PROCESSING,
    TERMINAL_STATUSES,
    new_job_payload,
)
from app.core.rbac import Role, require_role, require_roles
from app.core.users import Principal

router = APIRouter(prefix="/ingest", tags=["ingest"])

#: Actores del endpoint M2M (SPEC-04 §1: solo el principal ``AI_INFORMANT``).
_INFORMANT_ACTORS = frozenset({Role.AI_INFORMANT})

#: Cabecera del contrato con el token efímero de la Fase 0 (SPEC-03 §5).
PREFLIGHT_HEADER = "X-Preflight-Token"

#: Códigos de cierre WebSocket propios (rango 4000–4999, RFC 6455).
WS_CLOSE_UNAUTHORIZED = 4401
WS_CLOSE_FORBIDDEN = 4403
WS_CLOSE_NOT_FOUND = 4404
WS_CLOSE_SERVICE_UNAVAILABLE = 4503

#: Cadencia de sondeo del progreso y tope de duración del socket.
WS_POLL_SECONDS = 0.2
WS_MAX_SECONDS = 300


class IngestStatus(str, Enum):
    """``IngestJob.status`` del contrato."""

    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"


class IngestJob(BaseModel):
    """``IngestJob`` del contrato."""

    job_id: str
    status: IngestStatus
    progress: float = Field(ge=0, le=1)
    pending_review: int = Field(default=0, ge=0)


def _as_job(payload: Any) -> IngestJob:
    """Proyecta el estado del registro sobre el schema ``IngestJob``."""
    if isinstance(payload, IngestJob):
        return payload
    if not isinstance(payload, dict):
        raise service_unavailable("Estado de job no reconocible.")
    return IngestJob(
        job_id=str(payload.get("job_id") or ""),
        status=IngestStatus(str(payload.get("status") or IngestStatus.QUEUED.value)),
        progress=float(payload.get("progress") or 0.0),
        pending_review=int(payload.get("pending_review") or 0),
    )


def _not_found(job_id: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail_from_code(CODE_NOT_FOUND, f"Job de ingesta no encontrado: {job_id}"),
    )


def _pipeline_callable(extractor: Any) -> Callable[..., Any]:
    """Método de pipeline del extractor (``ingest``/``process``, SPEC-02 §5)."""
    for name in ("ingest", "process"):
        candidate = getattr(extractor, name, None)
        if callable(candidate):
            return candidate
    raise service_unavailable(
        "Extractor NLP incompleto: falta 'ingest'/'process' (SPEC-02 §5)."
    )


def _pending_count(outcome: Any) -> int:
    """Nº de tripletas pendientes de revisión humana del ``ReportOutcome``."""
    pending = getattr(outcome, "pending", None)
    if pending is None and isinstance(outcome, dict):
        pending = outcome.get("pending")
    try:
        return len(pending or [])
    except TypeError:  # pragma: no cover - salida atípica
        return 0


def run_ingest_job(
    registry: Any,
    extractor: Any,
    report_id: str,
    content: bytes,
    filename: Optional[str],
) -> None:
    """Ejecuta el pipeline y refleja el progreso en el registro (SPEC-02 §5).

    Se invoca como ``BackgroundTask``: la respuesta 202 ya se envió
    (SPEC-02 §6). Cualquier fallo se traduce en ``FAILED`` en el registro; nunca
    se propaga al cliente.
    """
    try:
        registry.update(report_id, status=STATUS_PROCESSING, progress=0.25)
        pages = extract_pages(content, filename)
        pipeline = _pipeline_callable(extractor)
        outcome = pipeline(report_id, pages)
        if inspect.isawaitable(outcome):  # pragma: no cover - pipeline async
            asyncio.run(outcome)
        registry.update(
            report_id,
            status=STATUS_DONE,
            progress=1.0,
            pending_review=_pending_count(outcome),
        )
    except Exception as exc:
        registry.update(
            report_id, status=STATUS_FAILED, progress=1.0, error=str(exc)
        )


# =====================================================================
# POST /ingest — createIngestJob (ANALYST+)
# =====================================================================
@router.post(
    "",
    operation_id="createIngestJob",
    response_model=IngestJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Sube informe OSINT (ANALYST+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def create_ingest_job(
    background: BackgroundTasks,
    user: Principal = Depends(require_role(Role.ANALYST)),
    extractor: Any = Depends(get_nlp_extractor),
    registry: Any = Depends(get_job_registry),
    gateway: Any = Depends(get_optional_graph_gateway),
    file: UploadFile = File(..., description="Informe OSINT (PDF/TXT/MD/JSON)"),
    title: str = Form(..., min_length=1, max_length=512),
) -> IngestJob:
    """Recibe el informe, lo almacena y encola su extracción (202 inmediato)."""
    content = await _receive_upload(file)
    if not content:
        raise service_unavailable("El archivo recibido está vacío.")

    report_id = str(uuid.uuid4())
    path = save_raw_report(report_id, file.filename, content)
    job = registry.put(report_id, new_job_payload(report_id, title=title))

    # SPEC-02 §1.5: nodo Report con sus metadatos (best-effort; el grafo puede
    # no estar disponible sin que la subida ya aceptada falle).
    if gateway is not None:
        gateway.register_report(
            report_id, report_metadata(report_id, title, path, user.id)
        )

    # SPEC-02 §5: encolar en Celery si hay broker; si no, BackgroundTasks
    # (degradación controlada, SPEC-GAP-3.6). El 202 ya se devuelve.
    queued = False
    try:
        from app.workers.tasks import enqueue_ingest

        queued = enqueue_ingest(
            report_id, raw_path=path, filename=file.filename, title=title
        )
    except Exception:  # pragma: no cover - Celery/broker no disponible
        queued = False

    if not queued:
        background.add_task(
            run_ingest_job, registry, extractor, report_id, content, file.filename
        )
    return _as_job(job)


# =====================================================================
# POST /ingest/informant — createInformantIngestJob (AI_INFORMANT)
# =====================================================================
async def _receive_upload(file: UploadFile) -> bytes:
    """Lee y valida la subida (tamaño, extensión y contenido) antes de guardarla.

    El rechazo se traduce al sobre de error del contrato: 413 si excede
    ``MAX_UPLOAD_MB`` y 415 si el formato o el contenido no son admitidos.
    """
    try:
        content = await read_upload_limited(file, get_settings().max_upload_bytes)
        validate_upload(file.filename, content)
    except UploadRejected as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=detail_from_code(exc.code, str(exc)),
        ) from exc
    return content


def _conflict(message: str, *, code: str = CODE_CONFLICT) -> HTTPException:
    """``409`` con el sobre de error del contrato (SPEC-03 §5)."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=detail_from_code(code, message),
    )


def run_informant_job(
    registry: Any,
    extractor: Any,
    preflight: Any,
    report_id: str,
    content: bytes,
    filename: Optional[str],
    metadata: Optional[dict[str, Any]],
    preflight_token: str,
) -> None:
    """Ejecuta el pipeline M2M y refleja el progreso en el registro (SPEC-02 §5).

    Usa ``NLPExtractor.ingest_informant`` (que revalida el token) cuando existe;
    si el extractor inyectado no lo expone, cae al pipeline humano estándar.
    """
    try:
        registry.update(report_id, status=STATUS_PROCESSING, progress=0.25)
        pages = extract_pages(content, filename)
        informant = getattr(extractor, "ingest_informant", None)
        if callable(informant):
            outcome = informant(
                report_id,
                pages,
                preflight_token=preflight_token,
                metadata=metadata,
            )
        else:  # pragma: no cover - extractor mínimo inyectado en tests
            outcome = _pipeline_callable(extractor)(report_id, pages)
        if inspect.isawaitable(outcome):  # pragma: no cover - pipeline async
            asyncio.run(outcome)
        registry.update(
            report_id,
            status=STATUS_DONE,
            progress=1.0,
            pending_review=_pending_count(outcome),
        )
    except Exception as exc:
        registry.update(
            report_id, status=STATUS_FAILED, progress=1.0, error=str(exc)
        )


@router.post(
    "/informant",
    operation_id="createInformantIngestJob",
    response_model=IngestJob,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingesta del Informante IA, con vínculos analíticos (AI_INFORMANT)",
    responses={
        401: {"description": "API Key M2M ausente o inválida"},
        409: {"description": "Ingesta ciega sin Pre-Flight o payload inválido"},
    },
)
async def create_informant_ingest_job(
    background: BackgroundTasks,
    principal: Principal = Depends(require_roles(_INFORMANT_ACTORS)),
    extractor: Any = Depends(get_informant_extractor),
    registry: Any = Depends(get_job_registry),
    gateway: Any = Depends(get_optional_graph_gateway),
    preflight: Any = Depends(get_preflight_service),
    # La cabecera es obligatoria *semánticamente* (SPEC-03 §5): su ausencia
    # devuelve 409 CONFLICT (no el 422 genérico de FastAPI).
    x_preflight_token: str = Header(default="", alias=PREFLIGHT_HEADER),
    file: UploadFile = File(..., description="Informe OSINT (PDF/TXT/MD/JSON)"),
    payload: str = Form(..., description="JSON validado contra extraction-schema"),
) -> IngestJob:
    """Ingesta M2M **nunca ciega**: exige API Key + ``X-Preflight-Token`` vigente."""
    # 1) Prohibición de ingesta ciega (Constitución Regla 3 · SPEC-03 §5).
    preflight.require_valid_token(x_preflight_token)

    # 2) El payload debe validar contra contracts/extraction-schema.json (v2).
    try:
        parsed = json.loads(payload)
    except (ValueError, TypeError) as exc:
        raise _conflict(f"payload no es JSON válido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise _conflict("payload debe ser un objeto JSON (extraction-schema v2).")
    try:
        from app.schemas.extraction import validate_contract

        validate_contract(parsed)
    except HTTPException:
        raise
    except Exception as exc:
        raise _conflict(
            f"payload no valida contra extraction-schema.json: {exc}"
        ) from exc

    content = await _receive_upload(file)
    if not content:
        raise service_unavailable("El archivo recibido está vacío.")

    report_id = str(uuid.uuid4())
    path = save_raw_report(report_id, file.filename, content)
    title = str((parsed.get("report_metadata") or {}).get("title") or file.filename or "")
    job = registry.put(report_id, new_job_payload(report_id, title=title))

    if gateway is not None:
        gateway.register_report(
            report_id, report_metadata(report_id, title, path, principal.id)
        )

    background.add_task(
        run_informant_job,
        registry,
        extractor,
        preflight,
        report_id,
        content,
        file.filename,
        parsed.get("report_metadata"),
        x_preflight_token,
    )
    return _as_job(job)


# =====================================================================
# GET /ingest/{job_id} — getIngestJob (VIEWER+)
# =====================================================================
@router.get(
    "/{job_id}",
    operation_id="getIngestJob",
    response_model=IngestJob,
    summary="Estado del job de ingesta (VIEWER+)",
)
async def get_ingest_job(
    job_id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    registry: Any = Depends(get_job_registry),
) -> IngestJob:
    """Estado/progreso de un job de ingesta (SPEC-02 §5)."""
    payload = registry.get(job_id)
    if payload is None:
        raise _not_found(job_id)
    return _as_job(payload)


# =====================================================================
# WS /ingest/{job_id}/ws — ingestProgressWs (VIEWER+)
# =====================================================================
@router.websocket("/{job_id}/ws")
async def ingest_progress_ws(
    websocket: WebSocket,
    job_id: str,
    token: str = Query(
        default="", description="Access JWT (el navegador no envía cabeceras)"
    ),
) -> None:
    """Progreso en vivo del job por WebSocket (SPEC-02 §5/§6).

    El rol se valida **antes** de aceptar el socket (SPEC-04 §1). Códigos de
    cierre propios: 4401 (sin identidad), 4403 (rol insuficiente), 4404 (job
    desconocido), 4503 (estado no disponible).
    """
    resolved = websocket.app.dependency_overrides
    store = resolved.get(_user_store_dependency(), _user_store_dependency())()
    try:
        await authorize_websocket(token, Role.VIEWER, store)
    except HTTPException as exc:
        close_code = (
            WS_CLOSE_FORBIDDEN
            if exc.status_code == status.HTTP_403_FORBIDDEN
            else WS_CLOSE_UNAUTHORIZED
        )
        await websocket.close(code=close_code, reason="No autorizado")
        return

    await websocket.accept()
    try:
        registry = resolved.get(get_job_registry, get_job_registry)()
        if registry.get(job_id) is None:
            await websocket.close(code=WS_CLOSE_NOT_FOUND, reason="Job desconocido")
            return
        async for snapshot in _stream_job(registry, job_id):
            await websocket.send_json(snapshot)
        await websocket.close()
    except WebSocketDisconnect:  # pragma: no cover - el cliente se fue
        return
    except Exception:  # pragma: no cover - defensivo
        await websocket.close(
            code=WS_CLOSE_SERVICE_UNAVAILABLE, reason="Estado no disponible"
        )


async def _stream_job(registry: Any, job_id: str) -> AsyncIterator[dict[str, Any]]:
    """Emite el estado del job en cada cambio hasta alcanzar un estado terminal."""
    last_sent: Optional[dict[str, Any]] = None
    waited = 0.0
    while waited < WS_MAX_SECONDS:
        payload = registry.get(job_id)
        if payload is None:
            return
        snapshot = {
            "job_id": payload.get("job_id", job_id),
            "status": payload.get("status"),
            "progress": payload.get("progress", 0.0),
            "pending_review": payload.get("pending_review", 0),
        }
        if snapshot != last_sent:
            last_sent = snapshot
            yield snapshot
        if str(snapshot["status"]) in TERMINAL_STATUSES:
            return
        await asyncio.sleep(WS_POLL_SECONDS)
        waited += WS_POLL_SECONDS


def _user_store_dependency() -> Callable[[], Any]:
    """Dependencia del store de usuarios (respeta ``dependency_overrides``)."""
    from app.api.deps import get_user_store

    return get_user_store


__all__ = [
    "router",
    "IngestJob",
    "IngestStatus",
    "run_ingest_job",
    "WS_CLOSE_UNAUTHORIZED",
    "WS_CLOSE_FORBIDDEN",
    "WS_CLOSE_NOT_FOUND",
]
