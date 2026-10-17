"""Aplicación Celery de OSINT Nexus (SPEC-02 §5).

Autorizado por: **SPEC-02 §5** (arquitectura FastAPI + Celery/Redis) y
**SPEC-04 §2** (cero secretos por defecto: el broker se lee del entorno).

Decisiones
----------
- El *import* de Celery es **protegido**: el backend arranca (y los tests corren)
  sin la librería y sin broker. ``CELERY_AVAILABLE`` refleja la disponibilidad.
- El broker se resuelve de ``CELERY_BROKER_URL`` o, como respaldo, ``REDIS_URL``.
  ``broker_configured()`` es ``True`` solo cuando hay Celery **y** un broker
  explícitamente configurado; en caso contrario la API usa ``BackgroundTasks``
  (SPEC-GAP-3.6, degradación controlada).
- ``celery_app`` se construye siempre que Celery esté disponible (con un broker
  de marcador ``memory://`` si no hay URL) para poder invocar la tarea de forma
  *eager* en tests (``task.apply``).
"""

from __future__ import annotations

import os
from typing import Any, Optional

try:  # pragma: no cover - depende del entorno de ejecución
    from celery import Celery

    CELERY_AVAILABLE = True
except Exception:  # pragma: no cover - import protegido deliberado
    Celery = None  # type: ignore[assignment]
    CELERY_AVAILABLE = False

#: Orden de resolución del broker (SPEC-02 §5).
BROKER_ENV_VARS: tuple[str, ...] = ("CELERY_BROKER_URL", "REDIS_URL")

#: Broker de marcador cuando no hay URL: permite construir la app Celery para
#: ejecución *eager* sin conexión real.
PLACEHOLDER_BROKER = "memory://"

#: Nombre lógico de la aplicación Celery.
APP_NAME = "osint"


def broker_url() -> Optional[str]:
    """URL del broker desde el entorno (``CELERY_BROKER_URL`` → ``REDIS_URL``)."""
    for name in BROKER_ENV_VARS:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def broker_configured() -> bool:
    """``True`` si hay Celery **y** un broker explícitamente configurado."""
    return CELERY_AVAILABLE and broker_url() is not None


def build_celery_app(
    broker: Optional[str] = None, backend: Optional[str] = None
) -> Any:
    """Construye la aplicación Celery (SPEC-02 §5).

    :raises RuntimeError: si la librería ``celery`` no está instalada.
    """
    if not CELERY_AVAILABLE:  # pragma: no cover - rama dependiente del entorno
        raise RuntimeError("La librería 'celery' no está instalada.")
    url = broker or broker_url() or PLACEHOLDER_BROKER
    # Sin backend por defecto: el estado del job vive en Redis (SPEC-02 §5), no
    # en el result backend de Celery. Un backend ``memory://`` no es válido para
    # Celery (lo interpretaría como módulo), así que solo se usa si se indica.
    application = Celery(APP_NAME, broker=url, backend=backend)
    application.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        broker_connection_retry_on_startup=True,
    )
    return application


#: Instancia compartida (``None`` sin Celery instalado).
celery_app: Any = build_celery_app() if CELERY_AVAILABLE else None


__all__ = [
    "APP_NAME",
    "BROKER_ENV_VARS",
    "CELERY_AVAILABLE",
    "PLACEHOLDER_BROKER",
    "broker_configured",
    "broker_url",
    "build_celery_app",
    "celery_app",
]
