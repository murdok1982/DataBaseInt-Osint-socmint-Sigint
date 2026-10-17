"""Configuración central del backend — **solo variables de entorno**.

Autorizado por: **SPEC-04 §2** (seguridad/red) y **SPEC-03 §1** (convenciones).
Valores alineados con ``deploy/.env.example`` (plantilla derivada de SPEC-04);
el archivo real ``.env`` nunca se versiona (Constitución, Regla 4: cero secretos
en el repositorio).

Decisiones
----------
- Se usa ``pydantic-settings`` si está disponible; si no, un ``dataclass`` que
  lee ``os.environ``. Ambos exponen **los mismos campos y propiedades**.
- ``JWT_SECRET_KEY`` **no tiene valor por defecto usable**: en producción su
  ausencia es un error fatal; en desarrollo se genera una clave efímera y se
  avisa por ``RuntimeWarning`` (nunca un secreto hardcodeado versionado).
- Los valores por defecto de expiración son los de SPEC-04: **30 minutos** de
  access token y 7 días de refresh.
"""

from __future__ import annotations

import os
import secrets
import warnings
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Callable

# --- Constantes normativas (SPEC-04 §2) -------------------------------
DEFAULT_JWT_ALGORITHM = "HS256"
DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES = 30
DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS = 7
DEFAULT_EXTRACTION_CONFIDENCE_THRESHOLD = 0.50
DEFAULT_NEO4J_URI = "bolt://localhost:7687"
DEFAULT_NEO4J_USER = "neo4j"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_STORAGE_RAW_DIR = "/storage/raw_reports"
DEFAULT_STORAGE_CHUNKS_DIR = "/storage/processed_chunks"

#: Entornos en los que no se admiten secretos por defecto.
PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod", "staging"})

try:  # pragma: no cover - depende del entorno de ejecución
    from pydantic_settings import BaseSettings, SettingsConfigDict

    PYDANTIC_SETTINGS_AVAILABLE = True
except Exception:  # pragma: no cover - import protegido deliberado
    BaseSettings = object  # type: ignore[assignment, misc]
    SettingsConfigDict = None  # type: ignore[assignment]
    PYDANTIC_SETTINGS_AVAILABLE = False


def _resolve_jwt_secret(value: str, environment: str) -> str:
    """Valida/deriva ``JWT_SECRET_KEY`` (SPEC-04 §2).

    :raises RuntimeError: si falta la clave y el entorno es de producción.
    """
    secret = (value or "").strip()
    if secret:
        return secret
    if environment.strip().lower() in PRODUCTION_ENVIRONMENTS:
        raise RuntimeError(
            "JWT_SECRET_KEY es obligatoria fuera de desarrollo (SPEC-04 §2). "
            'Genera una con: python -c "import secrets; print(secrets.token_urlsafe(64))"'
        )
    warnings.warn(
        "JWT_SECRET_KEY no está configurada: se usa una clave efímera de "
        "desarrollo (válida solo para este proceso). Define JWT_SECRET_KEY en "
        "el entorno (ver deploy/.env.example).",
        RuntimeWarning,
        stacklevel=2,
    )
    return secrets.token_urlsafe(64)


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


if PYDANTIC_SETTINGS_AVAILABLE:

    class Settings(BaseSettings):  # type: ignore[valid-type, misc]
        """Settings del backend (valores por defecto de SPEC-04 / .env.example)."""

        model_config = SettingsConfigDict(  # type: ignore[call-arg]
            env_file=".env",
            env_file_encoding="utf-8",
            extra="ignore",
            case_sensitive=False,
        )

        # --- Entorno ---
        environment: str = "development"

        # --- Seguridad / JWT (SPEC-04 §2) ---
        jwt_secret_key: str = ""
        jwt_algorithm: str = DEFAULT_JWT_ALGORITHM
        access_token_expire_minutes: int = DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
        refresh_token_expire_days: int = DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS
        #: Cookie de refresh solo se sirve por HTTPS (SPEC-04 §2). Se permite
        #: desactivar en tests locales; en producción Nginx fuerza TLS 1.2+.
        cookie_secure: bool = True

        # --- Argon2id (SPEC-04 §2) ---
        argon2_time_cost: int = 3
        argon2_memory_cost: int = 65536

        # --- Base de datos de grafos (SPEC-04 §2, red interna) ---
        neo4j_uri: str = DEFAULT_NEO4J_URI
        neo4j_user: str = DEFAULT_NEO4J_USER
        neo4j_password: str = ""

        # --- Cola / caché (red interna) ---
        redis_url: str = DEFAULT_REDIS_URL

        # --- Extracción NLP (SPEC-02 §umbral) ---
        extraction_confidence_threshold: float = (
            DEFAULT_EXTRACTION_CONFIDENCE_THRESHOLD
        )

        # --- Almacenamiento de informes (servido solo por FastAPI) ---
        storage_raw_dir: str = DEFAULT_STORAGE_RAW_DIR
        storage_chunks_dir: str = DEFAULT_STORAGE_CHUNKS_DIR

        def model_post_init(self, __context: Any) -> None:
            """Normaliza el secreto y los umbrales tras leer el entorno."""
            self.jwt_secret_key = _resolve_jwt_secret(
                self.jwt_secret_key, self.environment
            )
            self.jwt_algorithm = (self.jwt_algorithm or DEFAULT_JWT_ALGORITHM).upper()
            self.access_token_expire_minutes = int(self.access_token_expire_minutes)
            self.refresh_token_expire_days = int(self.refresh_token_expire_days)
            self.extraction_confidence_threshold = float(
                self.extraction_confidence_threshold
            )

        # --- Derivados (segundos, para ``expires_in`` del contrato) ---
        @property
        def access_token_expire_seconds(self) -> int:
            """1800 s = 30 min (SPEC-04 §2, campo ``expires_in``)."""
            return int(self.access_token_expire_minutes) * 60

        @property
        def refresh_token_expire_seconds(self) -> int:
            return int(self.refresh_token_expire_days) * 86400

        @property
        def is_production(self) -> bool:
            return self.environment.strip().lower() in PRODUCTION_ENVIRONMENTS

else:  # pragma: no cover - solo si falta pydantic-settings

    @dataclass
    class Settings:  # type: ignore[no-redef]
        """Fallback stdlib: mismos campos leyendo ``os.environ``."""

        environment: str = "development"
        jwt_secret_key: str = ""
        jwt_algorithm: str = DEFAULT_JWT_ALGORITHM
        access_token_expire_minutes: int = DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES
        refresh_token_expire_days: int = DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS
        cookie_secure: bool = True
        argon2_time_cost: int = 3
        argon2_memory_cost: int = 65536
        neo4j_uri: str = DEFAULT_NEO4J_URI
        neo4j_user: str = DEFAULT_NEO4J_USER
        neo4j_password: str = ""
        redis_url: str = DEFAULT_REDIS_URL
        extraction_confidence_threshold: float = (
            DEFAULT_EXTRACTION_CONFIDENCE_THRESHOLD
        )
        storage_raw_dir: str = DEFAULT_STORAGE_RAW_DIR
        storage_chunks_dir: str = DEFAULT_STORAGE_CHUNKS_DIR

        def __post_init__(self) -> None:
            env_spec: tuple[tuple[str, str, Callable[[str], Any]], ...] = (
                ("environment", "ENVIRONMENT", str),
                ("jwt_secret_key", "JWT_SECRET_KEY", str),
                ("jwt_algorithm", "JWT_ALGORITHM", str),
                (
                    "access_token_expire_minutes",
                    "ACCESS_TOKEN_EXPIRE_MINUTES",
                    int,
                ),
                ("refresh_token_expire_days", "REFRESH_TOKEN_EXPIRE_DAYS", int),
                ("cookie_secure", "COOKIE_SECURE", _as_bool),
                ("argon2_time_cost", "ARGON2_TIME_COST", int),
                ("argon2_memory_cost", "ARGON2_MEMORY_COST", int),
                ("neo4j_uri", "NEO4J_URI", str),
                ("neo4j_user", "NEO4J_USER", str),
                ("neo4j_password", "NEO4J_PASSWORD", str),
                ("redis_url", "REDIS_URL", str),
                (
                    "extraction_confidence_threshold",
                    "EXTRACTION_CONFIDENCE_THRESHOLD",
                    float,
                ),
                ("storage_raw_dir", "STORAGE_RAW_DIR", str),
                ("storage_chunks_dir", "STORAGE_CHUNKS_DIR", str),
            )
            for attr, env_name, caster in env_spec:
                raw = os.environ.get(env_name)
                if raw is None:
                    continue
                try:
                    setattr(self, attr, caster(raw))
                except (TypeError, ValueError):
                    continue
            self.jwt_secret_key = _resolve_jwt_secret(
                self.jwt_secret_key, self.environment
            )
            self.jwt_algorithm = (self.jwt_algorithm or DEFAULT_JWT_ALGORITHM).upper()

        @property
        def access_token_expire_seconds(self) -> int:
            return int(self.access_token_expire_minutes) * 60

        @property
        def refresh_token_expire_seconds(self) -> int:
            return int(self.refresh_token_expire_days) * 86400

        @property
        def is_production(self) -> bool:
            return self.environment.strip().lower() in PRODUCTION_ENVIRONMENTS


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Settings cacheadas (una instancia por proceso)."""
    return Settings()


def reset_settings_cache() -> None:
    """Limpia la caché de settings (tests y recarga de configuración)."""
    get_settings.cache_clear()


__all__ = [
    "Settings",
    "get_settings",
    "reset_settings_cache",
    "PYDANTIC_SETTINGS_AVAILABLE",
    "PRODUCTION_ENVIRONMENTS",
    "DEFAULT_JWT_ALGORITHM",
    "DEFAULT_ACCESS_TOKEN_EXPIRE_MINUTES",
    "DEFAULT_REFRESH_TOKEN_EXPIRE_DAYS",
    "DEFAULT_EXTRACTION_CONFIDENCE_THRESHOLD",
]
