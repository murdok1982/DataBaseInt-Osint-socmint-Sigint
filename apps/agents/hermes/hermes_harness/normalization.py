"""Normalización mínima de selectores para el proceso Hermes.

Es un espejo deliberado y acotado de
``apps/backend/app/services/selector_normalizer.py``. Los dos procesos se
despliegan por separado, así que el harness no puede importar ``app.*``; la
coherencia se comprueba en ``tests/test_contract_parity.py`` con los mismos
valores canónicos.

Si el tipo no está soportado se rechaza; nunca se adivina.
"""

from __future__ import annotations

import hashlib
import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit

_E164 = re.compile(r"^\+[1-9]\d{6,14}$")
_DIGITS = re.compile(r"^\d+$")
_MAC = re.compile(r"^[0-9A-F]{2}(?::[0-9A-F]{2}){5}$")
_DOMAIN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class InvalidSelector(ValueError):
    """El valor no se puede normalizar sin adivinar."""


def _text(value: str) -> str:
    if not isinstance(value, str):
        raise InvalidSelector("El selector debe ser texto.")
    return unicodedata.normalize("NFKC", value).strip()


def normalize_selector(observable_type: str, value: str) -> str:
    """Forma canónica usada para ``value_hash`` e idempotencia."""
    kind = (observable_type or "").strip().upper()
    text = _text(value)

    if kind in {"ACCOUNT_ID", "PLATFORM_ID"}:
        if not text or len(text) > 512:
            raise InvalidSelector(f"{kind} inválido.")
        return text
    if kind == "USERNAME":
        result = text.lstrip("@").lower()
        if not result or any(ch.isspace() for ch in result):
            raise InvalidSelector("USERNAME inválido.")
        return result
    if kind == "DISPLAY_NAME":
        result = " ".join(text.split()).lower()
        if not result or len(result) > 512:
            raise InvalidSelector("DISPLAY_NAME inválido.")
        return result
    if kind in {"PHONE_E164", "MSISDN"}:
        digits = re.sub(r"[^\d+]", "", text)
        if not _E164.fullmatch(digits):
            raise InvalidSelector(f"{kind} requiere E.164 con prefijo +.")
        return digits
    if kind == "EMAIL":
        result = text.lower()
        if not _EMAIL.fullmatch(result):
            raise InvalidSelector("EMAIL inválido.")
        return result
    if kind == "DOMAIN":
        result = text.lower().rstrip(".")
        try:
            result = result.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise InvalidSelector("DOMAIN no convertible a IDNA.") from exc
        if not _DOMAIN.fullmatch(result):
            raise InvalidSelector("DOMAIN inválido.")
        return result
    if kind == "IPV4":
        try:
            return str(ipaddress.IPv4Address(text))
        except ValueError as exc:
            raise InvalidSelector("IPV4 inválido.") from exc
    if kind == "IPV6":
        try:
            return str(ipaddress.IPv6Address(text))
        except ValueError as exc:
            raise InvalidSelector("IPV6 inválido.") from exc
    if kind == "URL":
        parts = urlsplit(text)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
            raise InvalidSelector("URL debe ser http(s) con host.")
        scheme = parts.scheme.lower()
        host = parts.hostname.lower()
        port = parts.port
        netloc = (
            f"{host}:{port}"
            if port
            and not (
                (scheme == "http" and port == 80)
                or (scheme == "https" and port == 443)
            )
            else host
        )
        return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))
    if kind == "MAC_ADDRESS":
        result = text.upper().replace("-", ":").replace(".", ":")
        if ":" not in result and len(result) == 12:
            result = ":".join(result[i : i + 2] for i in range(0, 12, 2))
        if not _MAC.fullmatch(result):
            raise InvalidSelector("MAC_ADDRESS inválido.")
        return result
    if kind in {"IMSI", "IMEI", "ICCID"}:
        minimum, maximum = {"IMSI": (14, 15), "IMEI": (14, 16), "ICCID": (18, 22)}[kind]
        result = re.sub(r"[\s-]", "", text)
        if not _DIGITS.fullmatch(result) or not (minimum <= len(result) <= maximum):
            raise InvalidSelector(f"{kind} inválido.")
        return result
    if kind == "WALLET":
        if not re.fullmatch(r"[A-Za-z0-9:_-]{16,128}", text):
            raise InvalidSelector("WALLET inválido.")
        return text.lower()
    if kind == "COORDINATE":
        # El contrato pide lat,lon; se normaliza el separador y espacios.
        parts = [p.strip() for p in text.replace(";", ",").split(",")]
        if len(parts) != 2:
            raise InvalidSelector("COORDINATE requiere 'lat,lon'.")
        try:
            lat, lon = float(parts[0]), float(parts[1])
        except ValueError as exc:
            raise InvalidSelector("COORDINATE no numérica.") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise InvalidSelector("COORDINATE fuera de rango.")
        return f"{lat:.6f},{lon:.6f}"
    if kind == "OTHER":
        if not text or len(text) > 1024:
            raise InvalidSelector("OTHER inválido.")
        return text
    raise InvalidSelector(f"Tipo de observable no soportado: {observable_type!r}")


def selector_value_hash(observable_type: str, value: str) -> str:
    normalized = normalize_selector(observable_type, value)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def selector_fingerprint(observable_type: str, value: str) -> tuple[str, str]:
    normalized = normalize_selector(observable_type, value)
    return normalized, hashlib.sha256(normalized.encode("utf-8")).hexdigest()


__all__ = [
    "InvalidSelector",
    "normalize_selector",
    "selector_fingerprint",
    "selector_value_hash",
]
