"""Estado y progreso de los jobs de ingesta (SPEC-02 §5).

Autorizado por: **SPEC-02 §5** — *«Estado y progreso por job en Redis; servido en
``GET /api/v1/ingest/{job_id}`` y por WebSocket
``/api/v1/ingest/{job_id}/ws``»* — y **SPEC-02 §6** (*«la subida no bloquea: el
endpoint responde 202 y el progreso llega por WS»*).

Zona: Agente 3 (``app/core/**``) — el registro de estado es infraestructura de
servicio (Redis en la red interna, **jamás publicado**, Constitución Regla 4).

Implementaciones
----------------
- :class:`RedisJobRegistry` — fuente de verdad en Redis (SPEC-02 §5). Si el
  *ping* inicial falla (desarrollo/tests) se degrada a memoria, y si Redis se
  cae a mitad de la ejecución se sigue atendiendo con el espejo local.
- :class:`InMemoryJobRegistry` — un solo proceso (tests/dev).

**SPEC-GAP-3.6 (resuelto).** SPEC-02 §5 prevé un *worker* Celery que ejecute
``chunk → extract → validate → resolve → persist``. La tarea vive en
``app/workers/tasks.py`` (``osint.workers.ingest_report``) y la API la encola
solo cuando hay broker configurado; si no, ejecuta el pipeline en
``BackgroundTasks``. Este registro sigue siendo el estado observable de los
endpoints del contrato.
"""

from __future__ import annotations

import json
import os
import threading
from functools import lru_cache
from typing import Any, Optional, Protocol, runtime_checkable

# --- Estados del contrato (``IngestJob.status``, openapi.yaml) ---------
STATUS_QUEUED = "QUEUED"
STATUS_PROCESSING = "PROCESSING"
STATUS_DONE = "DONE"
STATUS_FAILED = "FAILED"

#: Estados terminales: el WebSocket puede cerrar al alcanzarlos.
TERMINAL_STATUSES: frozenset[str] = frozenset({STATUS_DONE, STATUS_FAILED})

#: Prefijo de las claves de job en Redis (SPEC-02 §5).
JOB_KEY_PREFIX = "osint:ingest:job:"

#: TTL del estado de un job (24 h) para no crecer sin límite en Redis.
JOB_TTL_SECONDS = 86400

try:  # pragma: no cover - depende del entorno
    import redis as _redis

    REDIS_AVAILABLE = True
except Exception:  # pragma: no cover - import protegido deliberado
    _redis = None  # type: ignore
    REDIS_AVAILABLE = False


@runtime_checkable
class JobRegistry(Protocol):
    """Contrato mínimo del registro de jobs (inyectable en tests)."""

    def put(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]: ...

    def get(self, job_id: str) -> Optional[dict[str, Any]]: ...

    def update(self, job_id: str, **fields: Any) -> Optional[dict[str, Any]]: ...


def new_job_payload(job_id: str, **extra: Any) -> dict[str, Any]:
    """Job recién aceptado: ``QUEUED`` y progreso 0 (contrato ``IngestJob``)."""
    payload: dict[str, Any] = {
        "job_id": job_id,
        "status": STATUS_QUEUED,
        "progress": 0.0,
        "pending_review": 0,
    }
    payload.update(extra)
    return payload


class InMemoryJobRegistry:
    """Registro en memoria, seguro ante concurrencia (tests y desarrollo)."""

    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def put(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._jobs[job_id] = dict(payload)
            return dict(self._jobs[job_id])

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            payload = self._jobs.get(job_id)
            return dict(payload) if payload is not None else None

    def update(self, job_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        with self._lock:
            payload = self._jobs.get(job_id)
            if payload is None:
                return None
            payload.update(fields)
            return dict(payload)


class RedisJobRegistry:
    """Estado de los jobs en Redis (SPEC-02 §5) con espejo local de respaldo."""

    def __init__(self, url: str, *, prefix: str = JOB_KEY_PREFIX) -> None:
        if not REDIS_AVAILABLE:
            raise RuntimeError("La librería 'redis' no está instalada.")
        self._prefix = prefix
        self._mirror = InMemoryJobRegistry()
        self._client = _redis.Redis.from_url(  # type: ignore[union-attr]
            url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        # Falla rápido si Redis no está accesible (se degrada a memoria).
        self._client.ping()

    def _key(self, job_id: str) -> str:
        return f"{self._prefix}{job_id}"

    def put(self, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self._mirror.put(job_id, payload)
        try:
            self._client.set(self._key(job_id), json.dumps(payload), ex=JOB_TTL_SECONDS)
        except Exception:  # pragma: no cover - Redis caído en caliente
            pass
        return dict(payload)

    def get(self, job_id: str) -> Optional[dict[str, Any]]:
        try:
            raw = self._client.get(self._key(job_id))
        except Exception:  # pragma: no cover - Redis caído en caliente
            return self._mirror.get(job_id)
        if raw:
            payload = json.loads(raw)
            self._mirror.put(job_id, payload)
            return payload
        return self._mirror.get(job_id)

    def update(self, job_id: str, **fields: Any) -> Optional[dict[str, Any]]:
        payload = self.get(job_id)
        if payload is None:
            return None
        payload.update(fields)
        self.put(job_id, payload)
        return dict(payload)


@lru_cache(maxsize=1)
def get_job_registry() -> JobRegistry:
    """Registro de jobs: Redis si está configurado y accesible; si no, memoria."""
    url = (os.environ.get("REDIS_URL") or "").strip()
    if url and REDIS_AVAILABLE:
        try:
            return RedisJobRegistry(url)
        except Exception:  # pragma: no cover - Redis no accesible
            pass
    return InMemoryJobRegistry()


def reset_job_registry() -> None:
    """Limpia la caché del registro (tests y recarga de configuración)."""
    get_job_registry.cache_clear()


__all__ = [
    "STATUS_QUEUED",
    "STATUS_PROCESSING",
    "STATUS_DONE",
    "STATUS_FAILED",
    "TERMINAL_STATUSES",
    "JOB_KEY_PREFIX",
    "JOB_TTL_SECONDS",
    "JobRegistry",
    "InMemoryJobRegistry",
    "RedisJobRegistry",
    "new_job_payload",
    "get_job_registry",
    "reset_job_registry",
    "REDIS_AVAILABLE",
]
