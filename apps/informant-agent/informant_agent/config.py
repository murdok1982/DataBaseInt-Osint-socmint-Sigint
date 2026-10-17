"""Configuración por variables de entorno (SPEC-04 §2: cero secretos en el repo).

Variables reconocidas
---------------------
``INFORMANT_API_BASE_URL``
    Base de la API (por defecto ``http://localhost:8000``).
``INFORMANT_API_KEY``
    API Key M2M del principal ``AI_INFORMANT`` (header ``X-API-Key``). **Obligatoria**
    para hablar con la API real; nunca se persiste en el repositorio.
``INFORMANT_SOURCE_DIR``
    Directorio de fuentes abiertas que consume el recolector por defecto.
``INFORMANT_TIMEOUT``
    Timeout HTTP en segundos (por defecto ``30``).
``INFORMANT_CONTRACT_SCHEMA``
    Ruta opcional al contrato sellado (por defecto
    ``contracts/extraction-schema.json`` del repositorio).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "http://localhost:8000"
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable del servicio."""

    base_url: str = DEFAULT_BASE_URL
    api_key: str = ""
    source_dir: Path | None = None
    timeout: float = DEFAULT_TIMEOUT
    contract_schema: Path | None = None

    @classmethod
    def from_env(cls, env: "os._Environ | dict | None" = None) -> "Settings":
        env = env if env is not None else os.environ
        source_dir = env.get("INFORMANT_SOURCE_DIR")
        schema = env.get("INFORMANT_CONTRACT_SCHEMA")
        try:
            timeout = float(env.get("INFORMANT_TIMEOUT") or DEFAULT_TIMEOUT)
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT
        return cls(
            base_url=(env.get("INFORMANT_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/"),
            api_key=env.get("INFORMANT_API_KEY") or "",
            source_dir=Path(source_dir) if source_dir else None,
            timeout=timeout,
            contract_schema=Path(schema) if schema else None,
        )


__all__ = ["DEFAULT_BASE_URL", "DEFAULT_TIMEOUT", "Settings"]
