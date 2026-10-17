"""API Keys M2M — principal ``AI_INFORMANT`` (SPEC-04 §5.2).

Autorizado por: **SPEC-04 §1/§2/§5.2** (principal M2M, ``X-API-Key``, hash
Argon2id en el nodo ``ApiKey``) y ``contracts/openapi.yaml`` (``apiKeyAuth``).

Decisiones
----------
- El nodo ``ApiKey`` vive en el grafo (Agente 1, ``app/db/**``). Mientras el
  repositorio no exponga ``list_active_api_keys``, esta zona (Agente 3)
  implementa dos fuentes honestas y **sin credenciales por defecto**:
  :class:`EnvApiKeyStore` (variables de entorno, alineado con
  ``deploy/scripts/init-api-keys.py``) y :class:`InMemoryApiKeyStore` (tests).
- El **hash Argon2id** se verifica con :func:`app.core.security.verify_password`;
  la clave en claro **nunca** se almacena ni se registra (SPEC-04 §2).
- Un principal M2M no es un ``User`` (SPEC-04 §1): su :class:`Principal` usa el
  rol ``AI_INFORMANT`` y un ``username`` = ``client_name``.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol, runtime_checkable

from app.core.rbac import Role
from app.core.security import hash_password, verify_password


@dataclass(frozen=True)
class ApiKeyRecord:
    """Registro persistido de una API Key M2M (nodo ``ApiKey``, SPEC-04 §5.2)."""

    id: str
    client_name: str
    key_hash: str
    role: Role = Role.AI_INFORMANT
    created_at: str = ""
    revoked: bool = False

    def is_active(self) -> bool:
        return not self.revoked


@runtime_checkable
class ApiKeyStore(Protocol):
    """Contrato mínimo del almacén de API Keys."""

    def list_active(self) -> list[ApiKeyRecord]: ...

    def get_by_id(self, key_id: str) -> Optional[ApiKeyRecord]: ...


def hash_api_key(raw_key: str) -> str:
    """Devuelve el hash Argon2id de una API Key (SPEC-04 §2/§5.2)."""
    if not raw_key:
        raise ValueError("La API Key no puede estar vacía.")
    return hash_password(raw_key)


@dataclass
class InMemoryApiKeyStore:
    """Almacén en memoria (tests y ``dependency_overrides``)."""

    keys: list[ApiKeyRecord] = field(default_factory=list)

    def add(
        self,
        client_name: str,
        key_hash: str,
        *,
        key_id: Optional[str] = None,
        role: Role = Role.AI_INFORMANT,
        revoked: bool = False,
    ) -> ApiKeyRecord:
        record = ApiKeyRecord(
            id=key_id or str(uuid.uuid4()),
            client_name=client_name,
            key_hash=key_hash,
            role=role,
            revoked=revoked,
        )
        self.keys.append(record)
        return record

    def list_active(self) -> list[ApiKeyRecord]:
        return [key for key in self.keys if key.is_active()]

    def get_by_id(self, key_id: str) -> Optional[ApiKeyRecord]:
        for key in self.keys:
            if key.id == key_id:
                return key
        return None


@dataclass
class EnvApiKeyStore:
    """API Keys alimentadas por ``OSINT_API_KEYS_JSON`` (sin defaults inseguros).

    Formato::

        [{"id": "<uuid>", "client_name": "informant-1",
          "key_hash": "$argon2id$...", "role": "AI_INFORMANT", "revoked": false}]

    Si la variable no está definida, el almacén queda **vacío** y toda petición
    M2M responde 401 (SPEC-04 §4: no existe ninguna API Key por defecto).
    """

    keys: list[ApiKeyRecord] = field(default_factory=list)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "EnvApiKeyStore":
        records: list[ApiKeyRecord] = []
        raw = (env.get("OSINT_API_KEYS_JSON") or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    client_name = str(item.get("client_name") or "").strip()
                    key_hash = str(item.get("key_hash") or "").strip()
                    if not client_name or not key_hash:
                        continue
                    # El principal M2M es siempre AI_INFORMANT (SPEC-04 §5.2).
                    records.append(
                        ApiKeyRecord(
                            id=str(item.get("id") or uuid.uuid4()),
                            client_name=client_name,
                            key_hash=key_hash,
                            role=Role.AI_INFORMANT,
                            created_at=str(item.get("created_at") or ""),
                            revoked=bool(item.get("revoked", False)),
                        )
                    )
        return cls(keys=records)

    def list_active(self) -> list[ApiKeyRecord]:
        return [key for key in self.keys if key.is_active()]

    def get_by_id(self, key_id: str) -> Optional[ApiKeyRecord]:
        for key in self.keys:
            if key.id == key_id:
                return key
        return None


@dataclass
class GraphApiKeyStore:
    """Adaptador del nodo ``ApiKey`` del grafo (Agente 1, ``app/db/**``).

    Consume por *duck-typing* ``list_active_api_keys()`` del repositorio. Si el
    repositorio no expone la capacidad, el almacén queda vacío (la API responde
    401 sin romper el arranque).
    """

    repository: Any

    def list_active(self) -> list[ApiKeyRecord]:
        method = getattr(self.repository, "list_active_api_keys", None)
        if not callable(method):
            return []
        records: list[ApiKeyRecord] = []
        for raw in method() or []:
            data = dict(raw)
            records.append(
                ApiKeyRecord(
                    id=str(data.get("id") or ""),
                    client_name=str(data.get("client_name") or ""),
                    key_hash=str(data.get("key_hash") or ""),
                    role=Role.AI_INFORMANT,
                    created_at=str(data.get("created_at") or ""),
                    revoked=bool(data.get("revoked", False)),
                )
            )
        return [key for key in records if key.is_active()]

    def get_by_id(self, key_id: str) -> Optional[ApiKeyRecord]:
        for key in self.list_active():
            if key.id == key_id:
                return key
        return None


def authenticate_api_key(raw_key: str, store: ApiKeyStore) -> Optional[ApiKeyRecord]:
    """Valida una API Key contra los hashes Argon2id activos.

    Devuelve ``None`` (nunca eleva) si la clave es vacía, no coincide con ningún
    hash o la clave está revocada. La comparación es constante en tiempo (Argon2)
    y no revela qué clave falló.
    """
    candidate = str(raw_key or "")
    if not candidate:
        return None
    try:
        active = store.list_active()
    except Exception:  # pragma: no cover - almacén no disponible
        return None
    for record in active:
        if not record.is_active():
            continue
        try:
            if verify_password(record.key_hash, candidate):
                return record
        except Exception:  # pragma: no cover - hash inválido
            continue
    return None


__all__ = [
    "ApiKeyRecord",
    "ApiKeyStore",
    "InMemoryApiKeyStore",
    "EnvApiKeyStore",
    "GraphApiKeyStore",
    "hash_api_key",
    "authenticate_api_key",
]
