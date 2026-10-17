#!/usr/bin/env python3
"""init-api-keys.py — Genera una API Key M2M del principal ``AI_INFORMANT``.

Autorizado por: **SPEC-04 §5.2** (nodo ``ApiKey``: ``id``, ``client_name``,
``key_hash`` Argon2id, ``role`` = ``AI_INFORMANT``, ``created_at``, ``revoked``).

Requisitos
----------
- **Sin credenciales por defecto**: el ``client_name`` es obligatorio.
- La clave en claro se genera con ``secrets`` (o se aporta por STDIN/entorno) y
  **nunca** se persiste ni se registra; solo se guarda su **hash Argon2id**.
- El hash se inyecta en el nodo ``ApiKey`` (Agente 1) o, como *fallback* hasta
  su implementación, en ``OSINT_API_KEYS_JSON`` del ``.env`` del backend.

Uso
---
    python init-api-keys.py --client-name informant-agent
    # Muestra la API Key UNA vez y el fragmento JSON con su hash.

Persistencia en el grafo (Agente 1): el fragmento JSON de salida también sirve
para ``OSINT_API_KEYS_JSON``; el alta definitiva en el nodo ``ApiKey`` exige una
constraint ``apikey_key_hash_unique`` (``contracts/graph-constraints.cypher``).
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
import uuid

try:
    from argon2 import PasswordHasher
except ImportError:  # pragma: no cover - entorno sin dependencias del backend
    sys.exit(
        "Falta 'argon2-cffi'. Instala las dependencias del backend "
        "(apps/backend/requirements.txt) antes de continuar."
    )

#: Rol único del principal M2M (SPEC-04 §5.2).
API_KEY_ROLE = "AI_INFORMANT"

#: Entropía mínima de la clave generada (256 bits ⇒ 43 chars base64url).
DEFAULT_BYTES = 32

#: Etiqueta del cliente por entorno (sin valor por defecto).
ENV_CLIENT_NAME = "OSINT_API_KEY_CLIENT_NAME"

_pwd_hasher = PasswordHasher()  # Argon2id por defecto en argon2-cffi


def generate_key(nbytes: int = DEFAULT_BYTES) -> str:
    """API Key aleatoria (base64url, sin ``=``) con entropía suficiente."""
    return secrets.token_urlsafe(int(nbytes))


def hash_key(raw_key: str) -> str:
    """Hash Argon2id (PHC ``$argon2id$...``) de la API Key (SPEC-04 §2)."""
    if not raw_key:
        raise ValueError("La API Key no puede estar vacía.")
    return _pwd_hasher.hash(raw_key)


def build_record(client_name: str, key_hash: str, key_id: str | None = None) -> dict:
    """Entrada del nodo ``ApiKey`` / ``OSINT_API_KEYS_JSON`` (SPEC-04 §5.2)."""
    from datetime import datetime, timezone

    return {
        "id": key_id or str(uuid.uuid4()),
        "client_name": client_name,
        "key_hash": key_hash,
        "role": API_KEY_ROLE,
        "created_at": datetime.now(tz=timezone.utc).isoformat(),
        "revoked": False,
    }


def _resolve_client_name(arg: str | None) -> str:
    """``--client-name`` o ``OSINT_API_KEY_CLIENT_NAME``; nunca un valor por defecto."""
    name = (arg or os.environ.get(ENV_CLIENT_NAME) or "").strip()
    if not name:
        name = input("Nombre del cliente M2M (client_name): ").strip()
    if not name:
        sys.exit("El 'client_name' es obligatorio (sin valores por defecto).")
    return name


def _resolve_key(arg: str | None, *, generate: bool) -> str:
    """Clave aportada por ``--api-key``, STDIN o generada; nunca un valor por defecto."""
    if arg:
        return arg
    if generate:
        return generate_key()
    provided = getpass.getpass("API Key (se oculta; ENTER para generar una): ")
    return provided or generate_key()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Genera el hash Argon2id de una API Key M2M (AI_INFORMANT)."
    )
    parser.add_argument("--client-name", default=os.environ.get(ENV_CLIENT_NAME))
    parser.add_argument("--api-key", help="API Key en claro (evita exponer historial).")
    parser.add_argument("--key-id", help="UUID del nodo ApiKey (opcional).")
    parser.add_argument(
        "--generate",
        action="store_true",
        help="Genera una API Key aleatoria en vez de pedirla por STDIN.",
    )
    args = parser.parse_args()

    client_name = _resolve_client_name(args.client_name)
    raw_key = _resolve_key(args.api_key, generate=args.generate)
    record = build_record(client_name, hash_key(raw_key), args.key_id)

    print("")
    print("[OK] API Key M2M preparada (rol AI_INFORMANT).")
    print("     Guarda la clave ahora: NO se vuelve a mostrar ni se persiste en claro.")
    print("")
    print(f"     client_name : {client_name}")
    print(f"     id          : {record['id']}")
    print(f"     API Key     : {raw_key}")
    print("")
    print("     Fragmento para OSINT_API_KEYS_JSON (o alta en el nodo ApiKey):")
    print(json.dumps([record], ensure_ascii=False, indent=2))
    print("")


if __name__ == "__main__":
    main()
