"""Pool PostgreSQL 16 + pgvector para la capa v3 (Hito 1).

Zona: Agente 1 — ``apps/backend/app/db/**``.

El módulo es importable sin ``psycopg`` instalado (tests offline); el error se
eleva solo al crear el pool. La configuración se lee de ``POSTGRES_DSN`` y no
se registra nunca en logs.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

try:  # pragma: no cover - depende del entorno de ejecución
    import psycopg  # type: ignore
    from psycopg.rows import dict_row  # type: ignore
    from psycopg_pool import ConnectionPool  # type: ignore

    PSYCOPG_AVAILABLE = True
except Exception:  # pragma: no cover - import protegido deliberado
    psycopg = None  # type: ignore
    dict_row = None  # type: ignore
    ConnectionPool = None  # type: ignore
    PSYCOPG_AVAILABLE = False

DEFAULT_CLEARANCE = "TLP:CLEAR"


def get_dsn() -> str:
    """DSN desde ``POSTGRES_DSN``; sin valor por defecto con contraseña."""
    return os.environ.get("POSTGRES_DSN", "").strip()


def create_pool(
    dsn: Optional[str] = None,
    *,
    min_size: int = 1,
    max_size: int = 10,
    timeout: float = 10.0,
    **kwargs: Any,
) -> Any:
    """Crea un ``psycopg_pool.ConnectionPool``.

    :raises RuntimeError: si falta ``psycopg`` o el DSN.
    """
    if not PSYCOPG_AVAILABLE:
        raise RuntimeError(
            "La librería 'psycopg[binary,pool]' no está instalada. "
            "Añádela desde apps/backend/requirements.txt."
        )
    resolved = (dsn or get_dsn()).strip()
    if not resolved:
        raise RuntimeError(
            "POSTGRES_DSN no está definido. Configúralo en el entorno o pasa dsn=."
        )
    pool = ConnectionPool(
        resolved,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        kwargs={"row_factory": dict_row, "autocommit": False},
        open=True,
        **kwargs,
    )
    pool.wait(timeout=timeout)
    return pool


def verify_connectivity(pool: Any) -> bool:
    """Comprueba que el pool puede ejecutar ``SELECT 1``."""
    if pool is None:
        raise ValueError("verify_connectivity() requiere un pool no nulo.")
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return True


def close(pool: Any) -> None:
    """Cierra el pool de forma idempotente."""
    if pool is not None:
        pool.close()


@contextmanager
def postgres_pool(
    dsn: Optional[str] = None,
    *,
    min_size: int = 1,
    max_size: int = 10,
    **kwargs: Any,
) -> Iterator[Any]:
    """Context manager que crea, verifica y cierra el pool."""
    pool = create_pool(dsn, min_size=min_size, max_size=max_size, **kwargs)
    try:
        verify_connectivity(pool)
        yield pool
    finally:
        close(pool)


@contextmanager
def transaction(
    pool: Any,
    *,
    clearance: str = DEFAULT_CLEARANCE,
    break_glass: bool = False,
) -> Iterator[Any]:
    """Transacción con contexto RLS de la API.

    ``set_config(..., true)`` es local a la transacción: no contamina
    conexiones reutilizadas por el pool.
    """
    with pool.connection() as conn:
        with conn.transaction():
            conn.execute(
                "SELECT set_config('osint.clearance', %s, true)",
                (clearance,),
            )
            conn.execute(
                "SELECT set_config('osint.break_glass', %s, true)",
                ("true" if break_glass else "false",),
            )
            yield conn


__all__ = [
    "PSYCOPG_AVAILABLE",
    "DEFAULT_CLEARANCE",
    "get_dsn",
    "create_pool",
    "verify_connectivity",
    "close",
    "postgres_pool",
    "transaction",
]
