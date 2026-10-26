"""Normalización canónica de selectores multi-INT (SPEC-01 §6.2).

Zona: Agente 2 — ``apps/backend/app/services/**``.

El mismo selector puede aparecer como ``@JuanPerez``, ``juanperez`` o
``JUANPEREZ``. La base no puede deduplicar por el valor crudo: necesita una
forma canónica estable y un hash SHA-256 calculado **después** de normalizar.
Este módulo es puro (stdlib) y se usa tanto en el gateway de Hermes como en la
resolución de entidades y en la API.

Reglas de oro:

* El valor original se conserva en ``observables.immutable_value``.
* El valor normalizado se conserva en ``observables.normalized_value``.
* El hash se calcula siempre con :func:`selector_value_hash`.
* Una forma ambigua **no** se adivina: se rechaza con ``InvalidSelector``.

No se aceptan selectores vacíos ni tipos desconocidos. Los valores sensibles
(IMSI, IMEI, MAC, MSISDN) se normalizan, pero **no** se registran en logs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
from typing import Final
from urllib.parse import urlsplit, urlunsplit

#: Tipos de observable del contrato v3.1 (SPEC-01 §6.2 / SPEC-02 §3).
OBSERVABLE_TYPES: Final[frozenset[str]] = frozenset(
    {
        "ACCOUNT_ID",
        "USERNAME",
        "DISPLAY_NAME",
        "PHONE_E164",
        "MSISDN",
        "EMAIL",
        "WALLET",
        "DOMAIN",
        "IPV4",
        "IPV6",
        "URL",
        "MAC_ADDRESS",
        "IMSI",
        "IMEI",
        "ICCID",
        "PLATFORM_ID",
        "OTHER",
    }
)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_DIGITS_RE = re.compile(r"^\d+$")
_MAC_RE = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class InvalidSelector(ValueError):
    """El selector no se puede normalizar sin adivinar."""


def _text(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidSelector("El selector debe ser texto.")
    # NFKC convierte variantes de ancho/compatibilidad; strip elimina espacios
    # de borde. No se eliminan caracteres internos en tipos que los admiten.
    return unicodedata.normalize("NFKC", value).strip()


def _collapse(value: str) -> str:
    return " ".join(value.split())


def _username(value: str) -> str:
    text = _text(value).lstrip("@").lower()
    if not text or any(ch.isspace() for ch in text):
        raise InvalidSelector("USERNAME inválido.")
    return text


def _phone(value: str) -> str:
    raw = _text(value)
    # Acepta espacios, guiones, paréntesis y puntos; exige prefijo internacional.
    digits = re.sub(r"[^\d+]", "", raw)
    if digits.count("+") > 1 or ("+" in digits and not digits.startswith("+")):
        raise InvalidSelector("PHONE_E164 inválido: prefijo internacional mal formado.")
    if not digits.startswith("+"):
        raise InvalidSelector("PHONE_E164 requiere prefijo internacional (+).")
    if not _E164_RE.fullmatch(digits):
        raise InvalidSelector("PHONE_E164 inválido.")
    return digits


def _email(value: str) -> str:
    text = _text(value).lower()
    if not _EMAIL_RE.fullmatch(text):
        raise InvalidSelector("EMAIL inválido.")
    return text


def _domain(value: str) -> str:
    text = _text(value).lower().rstrip(".")
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise InvalidSelector("DOMAIN no convertible a IDNA.") from exc
    if not _DOMAIN_RE.fullmatch(text):
        raise InvalidSelector("DOMAIN inválido.")
    return text


def _url(value: str) -> str:
    text = _text(value)
    parts = urlsplit(text)
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        raise InvalidSelector("URL debe ser http(s) con host.")
    scheme = parts.scheme.lower()
    host = parts.hostname.lower()
    port = parts.port
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    else:
        netloc = host
    # El fragmento no identifica el recurso remoto; se descarta. La query sí se
    # conserva porque puede ser parte del identificador.
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _mac(value: str) -> str:
    text = _text(value).upper().replace("-", ":").replace(".", ":")
    if ":" not in text and len(text) == 12:
        text = ":".join(text[i : i + 2] for i in range(0, 12, 2))
    if not _MAC_RE.fullmatch(text):
        raise InvalidSelector("MAC_ADDRESS inválido.")
    return text


def _digits(value: str, *, min_len: int, max_len: int, label: str) -> str:
    text = re.sub(r"[\s-]", "", _text(value))
    if not _DIGITS_RE.fullmatch(text) or not (min_len <= len(text) <= max_len):
        raise InvalidSelector(f"{label} inválido.")
    return text


def _wallet(value: str) -> str:
    text = _text(value)
    if not re.fullmatch(r"[A-Za-z0-9:_-]{16,128}", text):
        raise InvalidSelector("WALLET inválido.")
    return text.lower()


def normalize_selector(observable_type: str, value: str) -> str:
    """Devuelve la forma canónica de ``value`` para ``observable_type``.

    :raises InvalidSelector: tipo no soportado o valor ambiguo.
    """
    kind = (observable_type or "").strip().upper()
    if kind not in OBSERVABLE_TYPES:
        raise InvalidSelector(f"Tipo de observable no soportado: {observable_type!r}")

    if kind in {"ACCOUNT_ID", "PLATFORM_ID"}:
        text = _text(value)
        if not text or len(text) > 512:
            raise InvalidSelector(f"{kind} inválido.")
        return text
    if kind == "USERNAME":
        return _username(value)
    if kind == "DISPLAY_NAME":
        text = _collapse(_text(value)).lower()
        if not text or len(text) > 512:
            raise InvalidSelector("DISPLAY_NAME inválido.")
        return text
    if kind in {"PHONE_E164", "MSISDN"}:
        return _phone(value)
    if kind == "EMAIL":
        return _email(value)
    if kind == "DOMAIN":
        return _domain(value)
    if kind == "IPV4":
        try:
            return str(ipaddress.IPv4Address(_text(value)))
        except ValueError as exc:
            raise InvalidSelector("IPV4 inválido.") from exc
    if kind == "IPV6":
        try:
            return str(ipaddress.IPv6Address(_text(value)))
        except ValueError as exc:
            raise InvalidSelector("IPV6 inválido.") from exc
    if kind == "URL":
        return _url(value)
    if kind == "MAC_ADDRESS":
        return _mac(value)
    if kind == "IMSI":
        return _digits(value, min_len=14, max_len=15, label="IMSI")
    if kind == "IMEI":
        return _digits(value, min_len=14, max_len=16, label="IMEI")
    if kind == "ICCID":
        return _digits(value, min_len=18, max_len=22, label="ICCID")
    if kind == "WALLET":
        return _wallet(value)
    # OTHER: se conserva el texto normalizado, sin inferir semántica.
    text = _text(value)
    if not text or len(text) > 1024:
        raise InvalidSelector("OTHER inválido.")
    return text


def selector_value_hash(observable_type: str, value: str) -> str:
    """SHA-256 de ``normalized_value`` (nunca del valor crudo)."""
    normalized = normalize_selector(observable_type, value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def selector_fingerprint(observable_type: str, value: str) -> tuple[str, str]:
    """Devuelve ``(normalized_value, value_hash)`` para persistir una fila."""
    normalized = normalize_selector(observable_type, value)
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "OBSERVABLE_TYPES",
    "InvalidSelector",
    "normalize_selector",
    "selector_fingerprint",
    "selector_value_hash",
]
