"""Factory del driver Neo4j para el grafo OSINT Nexus.

Autorizado por: SPEC-01 §6 (acceso/indexación del grafo) y SPEC-00 §2 (Neo4j).
Zona: Agente 1 — ``apps/backend/app/db/**``.

Decisiones de implementación
----------------------------
- La configuración se lee **exclusivamente** de ``os.environ`` (``NEO4J_URI``,
  ``NEO4J_USER``, ``NEO4J_PASSWORD``), tal y como fija ``deploy/.env.example``.
  No se importa ``app.core`` (propiedad del Agente 3) para evitar acoplamiento
  y dependencias circulares.
- El import de ``neo4j`` es **perezoso/protegido**: el módulo puede importarse
  sin la librería instalada (tests offline). El ``RuntimeError`` se eleva solo
  al intentar crear el driver.
- La base de datos nunca se expone: este factory lo consume el backend, no un
  cliente (Constitución, Regla 4).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

# --- Import perezoso/protegido ---------------------------------------
try:  # pragma: no cover - depende del entorno de ejecución
    from neo4j import GraphDatabase  # type: ignore

    NEO4J_AVAILABLE = True
except Exception:  # pragma: no cover - import protegido deliberado
    GraphDatabase = None  # type: ignore
    NEO4J_AVAILABLE = False

# Valores por defecto alineados con el compose local (no exponen secretos).
DEFAULT_URI = "bolt://localhost:7687"
DEFAULT_USER = "neo4j"


def get_uri() -> str:
    """URI del grafo desde ``NEO4J_URI`` (SPEC-00 §2)."""
    return os.environ.get("NEO4J_URI", DEFAULT_URI)


def get_user() -> str:
    """Usuario del grafo desde ``NEO4J_USER``."""
    return os.environ.get("NEO4J_USER", DEFAULT_USER)


def get_password() -> str:
    """Password del grafo desde ``NEO4J_PASSWORD`` (sin *default* por seguridad)."""
    return os.environ.get("NEO4J_PASSWORD", "")


def create_driver(
    uri: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    **config: Any,
) -> Any:
    """Crea un ``neo4j.Driver`` con autenticación básica.

    :param uri: si es ``None`` se toma de ``NEO4J_URI``.
    :param user: si es ``None`` se toma de ``NEO4J_USER``.
    :param password: si es ``None`` se toma de ``NEO4J_PASSWORD``.
    :raises RuntimeError: si la librería ``neo4j`` no está instalada.
    """
    if not NEO4J_AVAILABLE:
        raise RuntimeError(
            "La librería 'neo4j' no está instalada. Añade 'neo4j>=5,<6' "
            "(apps/backend/requirements.txt) para crear el driver."
        )
    resolved_uri = uri or get_uri()
    resolved_auth = (
        user if user is not None else get_user(),
        password if password is not None else get_password(),
    )
    return GraphDatabase.driver(resolved_uri, auth=resolved_auth, **config)


def verify_connectivity(driver: Any) -> bool:
    """Comprueba que el driver puede abrir una sesión contra el grafo.

    :raises ValueError: si ``driver`` es ``None``.
    """
    if driver is None:
        raise ValueError("verify_connectivity() requiere un driver no nulo.")
    driver.verify_connectivity()
    return True


def close(driver: Any) -> None:
    """Cierra el driver de forma segura (idempotente ante ``None``)."""
    if driver is not None:
        driver.close()


@contextmanager
def neo4j_driver(
    uri: Optional[str] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    **config: Any,
) -> Iterator[Any]:
    """Context manager que crea, verifica y cierra el driver.

    Uso::

        with neo4j_driver() as drv:
            with drv.session() as session:
                ...
    """
    driver = create_driver(uri, user, password, **config)
    try:
        verify_connectivity(driver)
        yield driver
    finally:
        close(driver)


__all__ = [
    "NEO4J_AVAILABLE",
    "DEFAULT_URI",
    "DEFAULT_USER",
    "get_uri",
    "get_user",
    "get_password",
    "create_driver",
    "verify_connectivity",
    "close",
    "neo4j_driver",
]
