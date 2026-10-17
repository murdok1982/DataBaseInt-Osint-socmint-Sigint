"""Directorio de operadores (identidad para ``/auth``).

Autorizado por: **SPEC-03 §2** (``/auth/login``, ``/auth/me``) y **SPEC-04 §1/§4**
(3 roles, Argon2id, sin credenciales por defecto).

**SPEC-GAP-3.1 (elevada al Orquestador).** Ninguna spec define *dónde* se
persisten los usuarios del sistema: SPEC-01 solo describe entidades OSINT
(``Person``, ``Organization``…) y la capa ``app/db/**`` es propiedad del Agente 1.
Por tanto esta zona (Agente 3) implementa un **directorio inyectable** con dos
implementaciones honestas:

- :class:`EnvUserStore` — fuente de verdad por variables de entorno
  (``OSINT_USERS_JSON`` o ``OSINT_ADMIN_USERNAME`` + ``OSINT_ADMIN_PASSWORD_HASH``),
  alineada con ``deploy/scripts/init-admin-user.py`` y **sin credenciales por
  defecto** (si no hay configuración, el login devuelve 401).
- :class:`InMemoryUserStore` — solo para tests / sustitución vía
  ``app.dependency_overrides``.

Cuando el Orquestador defina el store definitivo (p. ej. nodo ``User`` en el
grafo con índice único por ``username``), basta sustituir la dependencia
``get_user_store`` sin tocar las rutas.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable

from app.core.rbac import HUMAN_ROLES, Role
from app.core.security import verify_password


@dataclass(frozen=True)
class Principal:
    """Identidad autenticada del operador (contrato ``User``, SPEC-03 §2)."""

    id: str
    username: str
    role: Role

    def to_contract(self) -> dict[str, str]:
        """Representación exacta del schema ``User`` de ``openapi.yaml``."""
        return {"id": self.id, "username": self.username, "role": self.role.value}


@dataclass
class UserRecord:
    """Registro persistido del operador (hash Argon2id, nunca texto plano)."""

    id: str
    username: str
    password_hash: str
    role: Role
    is_active: bool = True
    #: Secreto TOTP (SPEC-04 §2: 2FA obligatorio para ADMIN, opcional ANALYST).
    totp_secret: Optional[str] = None

    def to_principal(self) -> Principal:
        return Principal(id=self.id, username=self.username, role=self.role)

    @property
    def totp_enabled(self) -> bool:
        return bool(self.totp_secret)


@runtime_checkable
class UserStore(Protocol):
    """Contrato mínimo del directorio de operadores."""

    def get_by_username(self, username: str) -> Optional[UserRecord]: ...

    def get_by_id(self, user_id: str) -> Optional[UserRecord]: ...


@dataclass
class InMemoryUserStore:
    """Store en memoria (tests y sustitución por ``dependency_overrides``)."""

    users: list[UserRecord] = field(default_factory=list)

    def add(
        self,
        username: str,
        password_hash: str,
        role: Role = Role.VIEWER,
        user_id: Optional[str] = None,
        is_active: bool = True,
        totp_secret: Optional[str] = None,
    ) -> UserRecord:
        record = UserRecord(
            id=user_id or str(uuid.uuid4()),
            username=username,
            password_hash=password_hash,
            role=role,
            is_active=is_active,
            totp_secret=totp_secret,
        )
        self.users.append(record)
        return record

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        target = (username or "").strip().lower()
        for user in self.users:
            if user.username.lower() == target:
                return user
        return None

    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        for user in self.users:
            if user.id == user_id:
                return user
        return None


@dataclass
class EnvUserStore:
    """Directorio alimentado por variables de entorno (sin defaults inseguros).

    Formato preferido (``OSINT_USERS_JSON``)::

        [{"id": "<uuid>", "username": "analista",
          "password_hash": "$argon2id$...", "role": "ANALYST"}]

    Alternativa de un solo ADMIN (compatible con ``init-admin-user.py``):
    ``OSINT_ADMIN_USERNAME`` + ``OSINT_ADMIN_PASSWORD_HASH`` (opcional
    ``OSINT_ADMIN_ID``, ``OSINT_ADMIN_ROLE`` = ``ADMIN``).
    """

    users: list[UserRecord] = field(default_factory=list)

    @classmethod
    def from_env(cls, env: dict[str, str]) -> "EnvUserStore":
        records: list[UserRecord] = []
        raw_json = (env.get("OSINT_USERS_JSON") or "").strip()
        if raw_json:
            try:
                parsed = json.loads(raw_json)
            except json.JSONDecodeError:
                parsed = []
            if isinstance(parsed, list):
                for index, item in enumerate(parsed):
                    if not isinstance(item, dict):
                        continue
                    username = str(item.get("username") or "").strip()
                    password_hash = str(item.get("password_hash") or "").strip()
                    role_raw = str(item.get("role") or "VIEWER").strip().upper()
                    if not username or not password_hash:
                        continue
                    # Un ``User`` humano nunca es AI_INFORMANT (SPEC-04 §1).
                    if role_raw not in HUMAN_ROLES:
                        continue
                    records.append(
                        UserRecord(
                            id=str(item.get("id") or uuid.uuid4()),
                            username=username,
                            password_hash=password_hash,
                            role=Role(role_raw),
                            is_active=bool(item.get("is_active", True)),
                            totp_secret=(
                                str(item["totp_secret"]).strip()
                                if item.get("totp_secret")
                                else None
                            ),
                        )
                    )
        else:
            admin_username = (env.get("OSINT_ADMIN_USERNAME") or "").strip()
            admin_hash = (env.get("OSINT_ADMIN_PASSWORD_HASH") or "").strip()
            if admin_username and admin_hash:
                role_raw = (env.get("OSINT_ADMIN_ROLE") or Role.ADMIN.value).upper()
                records.append(
                    UserRecord(
                        id=str(env.get("OSINT_ADMIN_ID") or uuid.uuid4()),
                        username=admin_username,
                        password_hash=admin_hash,
                        role=Role(role_raw) if role_raw in HUMAN_ROLES else Role.ADMIN,
                        totp_secret=(
                            (env.get("OSINT_ADMIN_TOTP_SECRET") or "").strip() or None
                        ),
                    )
                )
        return cls(users=records)

    def get_by_username(self, username: str) -> Optional[UserRecord]:
        target = (username or "").strip().lower()
        for user in self.users:
            if user.username.lower() == target:
                return user
        return None

    def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        for user in self.users:
            if user.id == user_id:
                return user
        return None


def authenticate(
    username: str, password: str, store: UserStore
) -> Optional[UserRecord]:
    """Valida credenciales. Devuelve ``None`` si son inválidas o el usuario está inactivo.

    El hash se verifica con Argon2id (SPEC-04 §2). Nunca se revela si el fallo
    fue por usuario inexistente o por contraseña incorrecta.
    """
    record = store.get_by_username(username)
    if record is None or not record.is_active:
        return None
    if not verify_password(record.password_hash, password):
        return None
    return record


__all__ = [
    "Principal",
    "UserRecord",
    "UserStore",
    "InMemoryUserStore",
    "EnvUserStore",
    "authenticate",
]
