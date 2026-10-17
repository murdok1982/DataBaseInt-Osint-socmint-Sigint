"""Seguridad: hashing Argon2id y tokens JWT.

Autorizado por: **SPEC-04 §2**
- Contraseñas hasheadas **exclusivamente con Argon2id**.
- Access token JWT con expiración de **15 minutos** (v2.2).
- Refresh token de 7 días, entregado solo en cookie ``HttpOnly``/``Secure``.

Los imports de ``argon2-cffi`` y del backend JWT (``PyJWT`` o ``python-jose``)
son **protegidos**: el módulo se importa sin esas librerías instaladas y los
errores se elevan únicamente al usar la función, de modo que el backend pueda
arrancar (y los tests degraden con ``skip``) en entornos incompletos.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
import uuid
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from app.core.config import get_settings

# =====================================================================
# Backend JWT — PyJWT preferente, python-jose como respaldo
# =====================================================================
try:  # pragma: no cover - depende del entorno
    import jwt as _pyjwt  # PyJWT

    JWT_AVAILABLE = True
    JWT_BACKEND = "pyjwt"
    _jose = None
except Exception:  # pragma: no cover - import protegido deliberado
    try:
        from jose import jwt as _jose  # type: ignore

        _pyjwt = None  # type: ignore
        JWT_AVAILABLE = True
        JWT_BACKEND = "jose"
    except Exception:
        _pyjwt = None  # type: ignore
        _jose = None  # type: ignore
        JWT_AVAILABLE = False
        JWT_BACKEND = ""

# =====================================================================
# Argon2id — import protegido
# =====================================================================
try:  # pragma: no cover - depende del entorno
    import argon2.exceptions as _argon2_exceptions
    from argon2 import PasswordHasher
    from argon2.low_level import Type as _Argon2Type

    ARGON2_AVAILABLE = True
except Exception:  # pragma: no cover - import protegido deliberado
    PasswordHasher = None  # type: ignore
    _Argon2Type = None  # type: ignore
    _argon2_exceptions = None  # type: ignore
    ARGON2_AVAILABLE = False

if ARGON2_AVAILABLE:  # pragma: no cover - trivial
    _ARGON2_ERRORS: tuple[type[BaseException], ...] = tuple(
        exc
        for name in (
            "VerifyMismatchError",
            "VerificationError",
            "InvalidHashError",
            "InvalidHash",
        )
        if (exc := getattr(_argon2_exceptions, name, None)) is not None
    ) or (Exception,)
else:  # pragma: no cover - trivial
    _ARGON2_ERRORS = (Exception,)

#: Tipo de token en el claim ``typ``/``token_type`` del payload.
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_REFRESH = "refresh"

#: Nombre de la cookie de refresh (SPEC-04 §2: HttpOnly + Secure).
REFRESH_COOKIE_NAME = "osint_refresh_token"
REFRESH_COOKIE_PATH = "/api/v1/auth"


class TokenError(Exception):
    """Token ausente, malformado, expirado o de tipo incorrecto."""


class HashingUnavailable(RuntimeError):
    """``argon2-cffi`` no está instalado (SPEC-04 §2 lo exige)."""


# =====================================================================
# Contraseñas — Argon2id
# =====================================================================
@lru_cache(maxsize=1)
def _password_hasher() -> Any:
    """Hasher Argon2id con los parámetros de ``deploy/.env.example``."""
    if not ARGON2_AVAILABLE:
        raise HashingUnavailable(
            "argon2-cffi no está instalado: instala 'argon2-cffi' "
            "(apps/backend/requirements.txt). SPEC-04 §2 exige Argon2id."
        )
    settings = get_settings()
    return PasswordHasher(
        time_cost=int(settings.argon2_time_cost),
        memory_cost=int(settings.argon2_memory_cost),
        parallelism=1,
        hash_len=32,
        salt_len=16,
        type=_Argon2Type.ID,  # Argon2id explícito (SPEC-04 §2)
    )


def hash_password(password: str) -> str:
    """Devuelve el hash Argon2id (PHC string ``$argon2id$...``).

    :raises HashingUnavailable: si ``argon2-cffi`` no está instalado.
    """
    if not password:
        raise ValueError("La contraseña no puede estar vacía.")
    return _password_hasher().hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """Verifica una contraseña contra su hash Argon2id.

    Devuelve ``False`` (nunca eleva) ante un hash inválido o una contraseña que
    no coincide, para no filtrar información por el tipo de excepción.
    """
    if not password_hash or password is None:
        return False
    if not ARGON2_AVAILABLE:
        raise HashingUnavailable(
            "argon2-cffi no está instalado: no se puede verificar la contraseña."
        )
    try:
        return bool(_password_hasher().verify(password_hash, password))
    except _ARGON2_ERRORS:
        return False


def password_needs_rehash(password_hash: str) -> bool:
    """``True`` si el hash usa parámetros distintos a los actuales."""
    if not ARGON2_AVAILABLE or not password_hash:
        return False
    try:
        return bool(_password_hasher().check_needs_rehash(password_hash))
    except _ARGON2_ERRORS:
        return True


# =====================================================================
# JWT
# =====================================================================
def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _encode(payload: dict[str, Any], algorithm: str) -> str:
    if not JWT_AVAILABLE:
        raise TokenError(
            "No hay backend JWT disponible: instala 'pyjwt' o 'python-jose' "
            "(apps/backend/requirements.txt)."
        )
    secret = get_settings().jwt_secret_key
    if JWT_BACKEND == "pyjwt":
        return _pyjwt.encode(payload, secret, algorithm=algorithm)
    return _jose.encode(payload, secret, algorithm=algorithm)


def _decode_raw(token: str, algorithms: list[str]) -> dict[str, Any]:
    if not JWT_AVAILABLE:
        raise TokenError(
            "No hay backend JWT disponible: instala 'pyjwt' o 'python-jose' "
            "(apps/backend/requirements.txt)."
        )
    secret = get_settings().jwt_secret_key
    try:
        if JWT_BACKEND == "pyjwt":
            return dict(_pyjwt.decode(token, secret, algorithms=algorithms))
        return dict(_jose.decode(token, secret, algorithms=algorithms))
    except Exception as exc:  # PyJWT/Jose elevan varias jerarquías propias
        raise TokenError(f"Token inválido: {exc}") from exc


def _create_token(
    subject: str,
    token_type: str,
    expires_delta: timedelta,
    role: Optional[str] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """Emite un JWT firmado con ``sub``, ``typ``, ``iat``, ``exp`` y ``jti``."""
    if not subject:
        raise ValueError("El claim 'sub' es obligatorio.")
    settings = get_settings()
    issued_at = _now()
    payload: dict[str, Any] = {
        "sub": str(subject),
        "typ": token_type,
        "iat": int(issued_at.timestamp()),
        "exp": int((issued_at + expires_delta).timestamp()),
        "jti": uuid.uuid4().hex,
    }
    if role:
        payload["role"] = role
    if extra_claims:
        payload.update(extra_claims)
    return _encode(payload, settings.jwt_algorithm)


def access_token_expires_in() -> int:
    """Segundos de vida del access token (``expires_in`` del contrato ≤ 900)."""
    return int(get_settings().access_token_expire_seconds)


def create_access_token(
    sub: str,
    role: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
    extra_claims: Optional[dict[str, Any]] = None,
) -> str:
    """Access token JWT de **15 minutos** (SPEC-04 §2 v2.2).

    :param expires_delta: override *solo* para tests (p. ej. forzar expiración).
    """
    delta = expires_delta or timedelta(seconds=access_token_expires_in())
    return _create_token(sub, TOKEN_TYPE_ACCESS, delta, role, extra_claims)


def create_refresh_token(
    sub: str,
    role: Optional[str] = None,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Refresh token (7 días por defecto) — viaja solo en cookie HttpOnly."""
    delta = expires_delta or timedelta(
        seconds=int(get_settings().refresh_token_expire_seconds)
    )
    return _create_token(sub, TOKEN_TYPE_REFRESH, delta, role)


def decode_token(token: str, verify_exp: bool = True) -> dict[str, Any]:
    """Decodifica y valida firma (y expiración) de un JWT.

    :raises TokenError: si el token es inválido, expirado o no verificable.
    """
    if not token or not isinstance(token, str):
        raise TokenError("Token ausente.")
    settings = get_settings()
    if not verify_exp:
        # PyJWT exige desactivar la verificación de exp de forma explícita.
        try:
            if JWT_BACKEND == "pyjwt":
                return dict(
                    _pyjwt.decode(
                        token,
                        settings.jwt_secret_key,
                        algorithms=[settings.jwt_algorithm],
                        options={"verify_exp": False},
                    )
                )
            return dict(
                _jose.decode(
                    token,
                    settings.jwt_secret_key,
                    algorithms=[settings.jwt_algorithm],
                    options={"verify_exp": False},
                )
            )
        except Exception as exc:
            raise TokenError(f"Token inválido: {exc}") from exc
    return _decode_raw(token, [settings.jwt_algorithm])


def decode_access_token(token: str) -> dict[str, Any]:
    """Decodifica exigiendo que sea un **access** token."""
    payload = decode_token(token)
    if payload.get("typ") != TOKEN_TYPE_ACCESS:
        raise TokenError("Se esperaba un access token.")
    return payload


def decode_refresh_token(token: str) -> dict[str, Any]:
    """Decodifica exigiendo que sea un **refresh** token."""
    payload = decode_token(token)
    if payload.get("typ") != TOKEN_TYPE_REFRESH:
        raise TokenError("Se esperaba un refresh token.")
    return payload


# =====================================================================
# 2FA TOTP (RFC 6238) — SPEC-04 §2 (obligatorio para ADMIN)
# =====================================================================
#: Parámetros TOTP por defecto (configuración estándar de apps autenticadoras).
TOTP_DIGITS = 6
TOTP_PERIOD_SECONDS = 30
#: Ventana de tolerancia (en periodos) por reloj desajustado.
TOTP_DEFAULT_WINDOW = 1


def generate_totp_secret(nbytes: int = 20) -> str:
    """Genera un secreto TOTP en base32 (160 bits CSPRNG por defecto, RFC 4226 §4)."""
    return base64.b32encode(secrets.token_bytes(nbytes)).decode("ascii").rstrip("=")


def _totp_code(
    secret: str,
    counter: int,
    *,
    digits: int = TOTP_DIGITS,
) -> str:
    """HOTP/TOTP: ``HMAC-SHA1(secret, counter)`` truncado dinámicamente."""
    try:
        key = base64.b32decode(str(secret).strip().upper() + "=" * (-len(str(secret).strip()) % 8))
    except Exception as exc:  # pragma: no cover - secreto malformado
        raise ValueError("Secreto TOTP inválido (base32).") from exc
    message = struct.pack(">Q", int(counter))
    digest = hmac.new(key, message, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % (10**digits)).zfill(digits)


def totp_now(
    secret: str,
    *,
    at: Optional[float] = None,
    period: int = TOTP_PERIOD_SECONDS,
    digits: int = TOTP_DIGITS,
) -> str:
    """Código TOTP vigente para ``secret`` (por defecto, ahora)."""
    timestamp = time.time() if at is None else float(at)
    return _totp_code(secret, int(timestamp) // period, digits=digits)


def verify_totp(
    secret: str,
    code: str,
    *,
    at: Optional[float] = None,
    window: int = TOTP_DEFAULT_WINDOW,
    period: int = TOTP_PERIOD_SECONDS,
    digits: int = TOTP_DIGITS,
) -> bool:
    """Verifica un código TOTP con tolerancia ``window`` periodos (nunca eleva).

    Devuelve ``False`` (sin filtrar el motivo) ante un secreto o código inválido.
    """
    return (
        match_totp_counter(
            secret, code, at=at, window=window, period=period, digits=digits
        )
        is not None
    )


def match_totp_counter(
    secret: str,
    code: str,
    *,
    at: Optional[float] = None,
    window: int = TOTP_DEFAULT_WINDOW,
    period: int = TOTP_PERIOD_SECONDS,
    digits: int = TOTP_DIGITS,
) -> Optional[int]:
    """Contador TOTP que valida ``code``, o ``None``.

    Devolver el contador permite al llamador rechazar la **reutilización** de
    un código ya aceptado (SPEC-04 §2 v2.2, anti-repetición).
    """
    candidate = str(code or "").strip()
    if not candidate or not candidate.isdigit() or len(candidate) != digits:
        return None
    timestamp = time.time() if at is None else float(at)
    counter = int(timestamp) // period
    for offset in range(-window, window + 1):
        try:
            expected = _totp_code(secret, counter + offset, digits=digits)
        except ValueError:
            return None
        if hmac.compare_digest(expected, candidate):
            return counter + offset
    return None


def reset_hasher_cache() -> None:
    """Limpia la caché del hasher (tests que cambian parámetros Argon2)."""
    _password_hasher.cache_clear()


__all__ = [
    "TOKEN_TYPE_ACCESS",
    "TOKEN_TYPE_REFRESH",
    "REFRESH_COOKIE_NAME",
    "REFRESH_COOKIE_PATH",
    "ARGON2_AVAILABLE",
    "JWT_AVAILABLE",
    "JWT_BACKEND",
    "TokenError",
    "HashingUnavailable",
    "hash_password",
    "verify_password",
    "password_needs_rehash",
    "create_access_token",
    "create_refresh_token",
    "decode_token",
    "decode_access_token",
    "decode_refresh_token",
    "access_token_expires_in",
    "reset_hasher_cache",
    "TOTP_DIGITS",
    "TOTP_PERIOD_SECONDS",
    "TOTP_DEFAULT_WINDOW",
    "generate_totp_secret",
    "totp_now",
    "verify_totp",
    "match_totp_counter",
]
