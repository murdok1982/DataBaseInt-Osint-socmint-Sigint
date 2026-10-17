"""Argon2id: hash verificable y contraseñas nunca en claro (SPEC-04 §2).

Autorizado por: **SPEC-04 §2** (*Contraseñas hasheadas exclusivamente con
Argon2id*) y **SPEC-04 §4** (``init-admin-user.py`` sin credenciales por
defecto).

Si ``argon2-cffi`` no está instalado, los tests se marcan como **skip**
justificado: la librería es una dependencia obligatoria de ``requirements.txt``
declarada por el Agente 3.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.security import (
    ARGON2_AVAILABLE,
    HashingUnavailable,
    hash_password,
    password_needs_rehash,
    verify_password,
)

pytestmark = pytest.mark.skipif(
    not ARGON2_AVAILABLE,
    reason="argon2-cffi no instalado (SPEC-04 §2 lo exige; ver requirements.txt)",
)

#: Parámetros de ``deploy/.env.example`` (ARGON2_TIME_COST / ARGON2_MEMORY_COST).
SAMPLE_PASSWORD = "contraseña-de-prueba-muy-larga"


def test_hash_is_argon2id_phc_string() -> None:
    """El hash es una cadena PHC con el identificador ``argon2id``."""
    encoded = hash_password(SAMPLE_PASSWORD)
    assert encoded.startswith("$argon2id$")
    assert "m=65536" in encoded and "t=3" in encoded


def test_hash_verifies_and_rejects_wrong_password() -> None:
    """``verify_password`` acepta la correcta y rechaza cualquier otra."""
    encoded = hash_password(SAMPLE_PASSWORD)
    assert verify_password(encoded, SAMPLE_PASSWORD) is True
    assert verify_password(encoded, SAMPLE_PASSWORD + "x") is False
    assert verify_password(encoded, "") is False


def test_salt_is_unique_per_hash() -> None:
    """Dos hashes de la misma contraseña difieren (sal aleatoria)."""
    first = hash_password(SAMPLE_PASSWORD)
    second = hash_password(SAMPLE_PASSWORD)
    assert first != second
    assert verify_password(first, SAMPLE_PASSWORD)
    assert verify_password(second, SAMPLE_PASSWORD)


@pytest.mark.parametrize(
    "corrupt_hash",
    ["", "no-es-un-hash", "$argon2id$incompleto", "$2b$12$" + "a" * 53],
)
def test_corrupt_hashes_return_false_without_raising(corrupt_hash: str) -> None:
    """Un hash corrupto no filtra información: devuelve ``False``."""
    assert verify_password(corrupt_hash, SAMPLE_PASSWORD) is False


def test_needs_rehash_detects_other_parameters() -> None:
    """``password_needs_rehash`` detecta parámetros distintos a los actuales."""
    assert password_needs_rehash(hash_password(SAMPLE_PASSWORD)) is False
    # ``t=1, m=8`` (parámetros débiles) ⇒ recomendar re-hash.
    weak = (
        "$argon2id$v=19$m=8,t=1,p=1$"
        "c2FsdHNhbHRzYWx0c2FsdA$Zm9vYmFyZm9vYmFyZm9vYmFyZm9vYmFyZm9vYg"
    )
    assert password_needs_rehash(weak) is True


def test_empty_password_is_rejected() -> None:
    """No se hashea la cadena vacía (evita credenciales vacías)."""
    with pytest.raises(ValueError):
        hash_password("")


def test_login_endpoint_never_returns_the_hash(app: Any, hashed_users: Any) -> None:
    """El endpoint de login no filtra el hash (SPEC-04 §2/§4)."""
    from fastapi.testclient import TestClient

    from app.api.deps import get_user_store
    from tests.security.conftest import login_payload

    app.dependency_overrides[get_user_store] = lambda: hashed_users
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json=login_payload("admin"))
    assert response.status_code == 200
    assert "$argon2id$" not in response.text


def test_hashing_unavailable_raises_when_library_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Si la librería faltara, se eleva un error explícito (no un hash débil)."""
    import app.core.security as security

    monkeypatch.setattr(security, "ARGON2_AVAILABLE", False)
    security.reset_hasher_cache()
    with pytest.raises(HashingUnavailable):
        security.hash_password("otra-contraseña")
    security.reset_hasher_cache()
