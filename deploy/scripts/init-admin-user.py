#!/usr/bin/env python3
"""
init-admin-user.py — Crea el primer operador ADMIN de OSINT Nexus.

Deriva de SPEC-04 §2/§4/§5.1 (v2.2). Requisitos:
- Contraseña hasheada con Argon2id (nunca texto plano, nunca por defecto).
- **2FA TOTP obligatorio para ADMIN** (SPEC-04 §2): se genera siempre un secreto
  TOTP nuevo (160 bits, base32) y su URI ``otpauth://`` para la app
  autenticadora. Volver a ejecutar el script rota el secreto (SPEC-06 §5).
- Sin credenciales por defecto: si no se pasan argumentos, se piden de forma
  interactiva (getpass) y se valida un mínimo de seguridad.
- Idempotente en su *salida*: no sobrescribe nada por sí mismo; imprime un
  fragmento listo para pegar en ``.env`` (``OSINT_USERS_JSON``). El alta
  definitiva en el nodo ``User`` (Agente 1) reemplazará este paso sin cambiar
  el contrato de entrada.

Uso:
    python deploy/scripts/init-admin-user.py --username admin
    (la contraseña se solicita de forma interactiva)

Salida: hash Argon2id completo + fragmento ``OSINT_USERS_JSON`` y la variante
de un solo ADMIN (``OSINT_ADMIN_USERNAME`` / ``OSINT_ADMIN_PASSWORD_HASH``).
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import secrets
import sys
import uuid
from urllib.parse import quote

try:
    from argon2 import PasswordHasher
except ImportError:  # pragma: no cover
    sys.exit("Falta 'argon2-cffi'. Instala dependencias del backend antes de continuar.")

# El driver real (Neo4j) se importa del backend del Agente 1:
#   from app.db.driver import get_driver
# Aquí se deja el esqueleto de persistencia a propósito: el Agente 3 solo
# define el alta del usuario; la escritura en grafo la implementa el Agente 1.
# Mientras tanto, ``app/core/users.py::EnvUserStore`` consume esta salida.

MIN_PASSWORD_LEN = 14
_pwd_hasher = PasswordHasher()  # Argon2id por defecto en argon2-cffi


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        sys.exit(f"La contraseña debe tener al menos {MIN_PASSWORD_LEN} caracteres.")
    if password.lower() in {"password", "admin", "changeme"}:
        sys.exit("Contraseña demasiado común.")


def prompt_credentials(username: str | None) -> tuple[str, str]:
    username = username or input("Usuario administrador: ").strip()
    if not username:
        sys.exit("El usuario es obligatorio.")
    password = getpass.getpass("Contraseña: ")
    confirm = getpass.getpass("Repite la contraseña: ")
    if password != confirm:
        sys.exit("Las contraseñas no coinciden.")
    validate_password(password)
    return username, password


def generate_totp_secret() -> str:
    """Secreto TOTP base32 de 160 bits (RFC 4226 §4), sin relleno."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def otpauth_uri(username: str, secret: str, issuer: str = "OSINT Nexus") -> str:
    """URI ``otpauth://`` para dar de alta el secreto en la app autenticadora."""
    label = quote(f"{issuer}:{username}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )


def build_record(
    username: str, password_hash: str, user_id: str | None, totp_secret: str
) -> dict:
    """Entrada de ``OSINT_USERS_JSON`` / nodo ``User`` (SPEC-04 §5.1)."""
    return {
        "id": user_id or str(uuid.uuid4()),
        "username": username,
        "password_hash": password_hash,
        "role": "ADMIN",
        "totp_secret": totp_secret,
    }


def create_admin(username: str, password: str, user_id: str | None = None) -> None:
    password_hash = _pwd_hasher.hash(password)  # Argon2id (PHC $argon2id$...)
    totp_secret = generate_totp_secret()
    record = build_record(username, password_hash, user_id, totp_secret)

    print("")
    print("[OK] Usuario ADMIN preparado (Argon2id).")
    print("     Copia UNA de las dos variantes en deploy/.env (NUNCA versionar):")
    print("")
    print("     -- Opción A: lista de usuarios (preferida) ------------------")
    print(f"     OSINT_USERS_JSON={json.dumps([record], ensure_ascii=False)}")
    print("")
    print("     -- Opción B: un solo ADMIN ---------------------------------")
    print(f"     OSINT_ADMIN_USERNAME={username}")
    print(f"     OSINT_ADMIN_PASSWORD_HASH={password_hash}")
    print(f"     OSINT_ADMIN_TOTP_SECRET={totp_secret}")
    print("")
    print("     -- 2FA (obligatorio para ADMIN, SPEC-04 §2) -----------------")
    print("     Da de alta este URI en la app autenticadora y destrúyelo después:")
    print(f"     {otpauth_uri(username, totp_secret)}")
    print("")
    print("     Tras editar .env: docker compose ... up -d --force-recreate backend")
    print("     Hash (no compartir en claro):", password_hash[:24] + "…")
    # TODO(Agente 1): persistir el nodo User (MERGE por username) cuando el
    # repositorio del grafo lo exponga; hoy el origen de verdad es .env.


def main() -> None:
    parser = argparse.ArgumentParser(description="Crea el primer ADMIN de OSINT Nexus.")
    parser.add_argument("--username", default=os.environ.get("OSINT_ADMIN_USERNAME"))
    parser.add_argument("--user-id", help="UUID del nodo User (opcional).")
    args = parser.parse_args()

    username, password = prompt_credentials(args.username)
    create_admin(username, password, args.user_id)


if __name__ == "__main__":
    main()
