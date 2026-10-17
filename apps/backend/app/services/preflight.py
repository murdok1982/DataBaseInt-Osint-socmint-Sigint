"""Pre-Flight Context Check — Fase 0 (SPEC-02 §1 · SPEC-03 §5).

Autorizado por: **SPEC-02 §1 Fase 0** (análisis activos + RFIs + diccionario de
entidades canónicas), **SPEC-02 §5** (``app/services/preflight.py``, dueño
Agente 2) y **SPEC-03 §5** (``POST /context/preflight`` y token M2M).

Zona: Agente 2 — ``apps/backend/app/services/preflight.py``.

Responsabilidades
-----------------
1. Construir el contexto del pre-vuelo a partir del repositorio (Agente 1):
   ``active_analyses(keywords)``, ``list_open_requirements()`` y
   ``canonical_entities()``.
2. **Emitir** un ``preflight_token`` **efímero** (HMAC-SHA256 con expiración) y
   **verificarlo** antes de aceptar la ingesta del Informante IA.

Constitución Regla 3 (prohibición de ingesta ciega): la ingesta del Informante IA
**sin** un token vigente se rechaza con :class:`PreflightRequiredError`, que la
capa API (Agente 3) traduce a ``409 CONFLICT`` (SPEC-03 §5).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

#: Cabecera M2M del contrato (``openapi.yaml`` → ``X-Preflight-Token``).
TOKEN_HEADER = "X-Preflight-Token"

#: Vigencia por defecto del token efímero (15 min).
DEFAULT_TTL_SECONDS = 900

#: Variable de entorno para el secreto HMAC (por proceso si no se define).
ENV_SECRET = "PREFLIGHT_SECRET"

#: Versión del formato de token (permite evolución sin romper la verificación).
TOKEN_VERSION = 1

#: Máximo de entidades canónicas devueltas (evita respuestas gigantes).
DEFAULT_MAX_ENTITIES = 1000


class PreflightRequiredError(ValueError):
    """Ingesta sin un pre-vuelo válido — ``409`` en la capa API (SPEC-03 §5)."""

    code = "PREFLIGHT_REQUIRED"

    def __init__(self, message: str = "Se requiere un preflight_token vigente.") -> None:
        super().__init__(message)


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    """Normaliza un nombre para indexar el diccionario canónico (SPEC-02 §4.1)."""
    return _strip_accents(str(name or "")).lower().strip()


def clean_keywords(keywords: Iterable[Any]) -> list[str]:
    """Normaliza/desduplica palabras clave preservando el orden."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in keywords or []:
        value = str(raw).strip()
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def derive_keywords(*texts: Any) -> list[str]:
    """Palabras clave preliminares a partir de títulos/etiquetas (Fase 0 paso 1)."""
    import re

    stop = {
        "the", "a", "an", "of", "and", "or", "to", "in", "on", "for",
        "el", "la", "los", "las", "de", "del", "y", "o", "en", "un", "una",
    }
    words: list[str] = []
    for text in texts:
        for token in re.split(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]+", str(text or "")):
            token = token.strip()
            if len(token) > 3 and token.casefold() not in stop:
                words.append(token)
    cleaned = clean_keywords(words)
    return cleaned or ["osint"]


@dataclass(frozen=True)
class TokenClaims:
    """Contenido verificado de un ``preflight_token``."""

    issued_at: int
    expires_at: int
    nonce: str
    keywords: tuple[str, ...]


@dataclass
class PreflightContext:
    """Contexto de la Fase 0 + token efímero (SPEC-02 §1 Fase 0)."""

    token: str
    analyses: list[dict[str, Any]] = field(default_factory=list)
    requirements: list[dict[str, Any]] = field(default_factory=list)
    canonical_entities: list[dict[str, Any]] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    issued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    claims: Optional[TokenClaims] = None

    @property
    def preflight_token(self) -> str:
        """Alias del token (nombre del contrato ``PreflightResponse``)."""
        return self.token

    def canonical_index(self) -> dict[str, tuple[str | None, str | None]]:
        """``normalize_name(canonical_name) -> (id, label)`` (SPEC-02 §4.5)."""
        index: dict[str, tuple[str | None, str | None]] = {}
        for entity in self.canonical_entities:
            name = entity.get("canonical_name")
            if not name:
                continue
            index[normalize_name(str(name))] = (
                entity.get("id"),
                entity.get("label"),
            )
        return index

    def to_response(self) -> dict[str, Any]:
        """Proyección sobre ``PreflightResponse`` (``openapi.yaml``)."""
        return {
            "preflight_token": self.token,
            "analyses": list(self.analyses),
            "requirements": list(self.requirements),
            "canonical_entities": list(self.canonical_entities),
        }


class PreflightService:
    """Resuelve la Fase 0 y gestiona los tokens efímeros (SPEC-02 §1/§5).

    :param repository: objeto con ``active_analyses(keywords)``,
        ``list_open_requirements()`` y ``canonical_entities()`` (Agente 1).
    :param secret: secreto HMAC; por defecto ``PREFLIGHT_SECRET`` del entorno o
        uno aleatorio por proceso.
    :param ttl_seconds: vigencia del token (por defecto :data:`DEFAULT_TTL_SECONDS`).
    :param now: inyectable en tests (reloj determinista).
    """

    def __init__(
        self,
        repository: Any,
        *,
        secret: bytes | str | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entities: int = DEFAULT_MAX_ENTITIES,
        now: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if int(ttl_seconds) <= 0:
            raise ValueError("ttl_seconds debe ser > 0.")
        self.repository = repository
        self.ttl_seconds = int(ttl_seconds)
        self.max_entities = int(max_entities)
        resolved = secret if secret is not None else os.environ.get(ENV_SECRET)
        if resolved is None:
            resolved = secrets.token_urlsafe(32)
        self._secret: bytes = (
            resolved.encode("utf-8") if isinstance(resolved, str) else bytes(resolved)
        )
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -----------------------------------------------------------------
    # Construcción del contexto (Fase 0)
    # -----------------------------------------------------------------
    def build_context(
        self,
        keywords: Sequence[str],
        *,
        entity_hints: Optional[Sequence[str]] = None,
    ) -> PreflightContext:
        """Construye el contexto (análisis + RFIs + canónicas) y emite el token."""
        clean = clean_keywords(keywords)
        analyses = self._active_analyses(clean)
        requirements = self._open_requirements()
        entities = self._canonical_entities(entity_hints)
        issued, expires, token, claims = self._issue(clean)
        return PreflightContext(
            token=token,
            analyses=analyses,
            requirements=requirements,
            canonical_entities=entities,
            keywords=clean,
            issued_at=issued,
            expires_at=expires,
            claims=claims,
        )

    #: Alias semántico del flujo (SPEC-02 §1 Fase 0).
    preflight = build_context

    def response(
        self, keywords: Sequence[str], *, entity_hints: Optional[Sequence[str]] = None
    ) -> dict[str, Any]:
        """Contexto proyectado sobre ``PreflightResponse`` (``openapi.yaml``)."""
        return self.build_context(keywords, entity_hints=entity_hints).to_response()

    # -----------------------------------------------------------------
    # Token efímero (emisión / verificación)
    # -----------------------------------------------------------------
    def _issue(
        self, keywords: Sequence[str]
    ) -> tuple[datetime, datetime, str, TokenClaims]:
        issued = self._now()
        expires = issued + timedelta(seconds=self.ttl_seconds)
        claims = TokenClaims(
            issued_at=int(issued.timestamp()),
            expires_at=int(expires.timestamp()),
            nonce=secrets.token_urlsafe(8),
            keywords=tuple(clean_keywords(keywords)),
        )
        return issued, expires, self._encode(claims), claims

    def issue_token(self, keywords: Sequence[str] = ()) -> str:
        """Emite un ``preflight_token`` efímero sin consultar el grafo."""
        return self._issue(keywords)[2]

    def _encode(self, claims: TokenClaims) -> str:
        body = json.dumps(
            {
                "v": TOKEN_VERSION,
                "iat": claims.issued_at,
                "exp": claims.expires_at,
                "n": claims.nonce,
                "kw": list(claims.keywords),
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        signature = hmac.new(self._secret, body, hashlib.sha256).digest()
        return f"{_b64(body)}.{_b64(signature)}"

    def verify_token(self, token: Optional[str]) -> TokenClaims:
        """Verifica firma y vigencia; eleva :class:`PreflightRequiredError`.

        No consulta el grafo: solo acredita que la ingesta del Informante IA fue
        precedida por un ``POST /context/preflight`` de esta plataforma.
        """
        if not token or not str(token).strip():
            raise PreflightRequiredError(
                "Ingesta ciega prohibida: falta el preflight_token "
                "(Constitución Regla 3 · SPEC-03 §5)."
            )
        parts = str(token).split(".")
        if len(parts) != 2:
            raise PreflightRequiredError("preflight_token con formato inválido.")
        body_b64, signature_b64 = parts
        try:
            body = _unb64(body_b64)
            provided = _unb64(signature_b64)
        except (ValueError, TypeError) as exc:  # pragma: no cover - defensivo
            raise PreflightRequiredError("preflight_token ilegible.") from exc
        expected = hmac.new(self._secret, body, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, provided):
            raise PreflightRequiredError("preflight_token con firma no válida.")
        try:
            data = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:  # pragma: no cover
            raise PreflightRequiredError("preflight_token ilegible.") from exc
        if int(data.get("v", 0)) != TOKEN_VERSION:
            raise PreflightRequiredError("preflight_token de versión no soportada.")
        expires_at = int(data.get("exp", 0))
        if expires_at <= int(self._now().timestamp()):
            raise PreflightRequiredError("preflight_token expirado.")
        return TokenClaims(
            issued_at=int(data.get("iat", 0)),
            expires_at=expires_at,
            nonce=str(data.get("n", "")),
            keywords=tuple(str(k) for k in data.get("kw", []) or []),
        )

    def require_valid_token(self, token: Optional[str]) -> TokenClaims:
        """Alias explícito de :meth:`verify_token` para la ruta M2M."""
        return self.verify_token(token)

    # -----------------------------------------------------------------
    # Consultas al repositorio (duck-typing defensivo)
    # -----------------------------------------------------------------
    def _active_analyses(self, keywords: Sequence[str]) -> list[dict[str, Any]]:
        method = getattr(self.repository, "active_analyses", None)
        if not callable(method):  # pragma: no cover - repositorio mínimo
            return []
        return [dict(item) for item in (method(list(keywords)) or [])]

    def _open_requirements(self) -> list[dict[str, Any]]:
        method = getattr(self.repository, "list_open_requirements", None)
        if not callable(method):  # pragma: no cover - repositorio mínimo
            return []
        return [dict(item) for item in (method() or [])]

    def _canonical_entities(
        self, entity_hints: Optional[Sequence[str]]
    ) -> list[dict[str, Any]]:
        method = getattr(self.repository, "canonical_entities", None)
        if not callable(method):  # pragma: no cover - repositorio mínimo
            return []
        entities = [dict(item) for item in (method() or [])]
        hints = [normalize_name(h) for h in (entity_hints or []) if str(h).strip()]
        if hints:
            entities = [
                entity
                for entity in entities
                if any(
                    hint in normalize_name(str(entity.get("canonical_name") or ""))
                    or hint in normalize_name(str(entity.get("id") or ""))
                    for hint in hints
                )
            ]
        return entities[: self.max_entities]


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


__all__ = [
    "DEFAULT_MAX_ENTITIES",
    "DEFAULT_TTL_SECONDS",
    "ENV_SECRET",
    "PreflightContext",
    "PreflightRequiredError",
    "PreflightService",
    "TOKEN_HEADER",
    "TOKEN_VERSION",
    "TokenClaims",
    "clean_keywords",
    "derive_keywords",
    "normalize_name",
]
