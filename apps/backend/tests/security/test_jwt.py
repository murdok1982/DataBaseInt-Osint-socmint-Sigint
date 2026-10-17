"""JWT: expiración de 15 minutos y refresh cookie ``HttpOnly``/``Secure``.

Autorizado por: **SPEC-04 §2 v2.2** (tokens de acceso JWT con expiración de
15 min + refresh token en cookie ``HttpOnly`` y ``Secure``; topes 15 min / 7 días)
y **SPEC-04 §4** (criterio "JWT de 15 min verificado; refresh solo por cookie").

Se comprueba el token (unidad) y los endpoints ``/auth/login``,
``/auth/refresh``, ``/auth/logout`` y ``/auth/me`` (integración con
``TestClient``).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from app.api.deps import get_user_store
from app.core.config import get_settings, reset_settings_cache
from app.core.rbac import Role
from app.core.security import (
    REFRESH_COOKIE_NAME,
    TOKEN_TYPE_ACCESS,
    TOKEN_TYPE_REFRESH,
    TokenError,
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    decode_access_token,
    decode_refresh_token,
    decode_token,
)
from tests.security.conftest import (
    ADMIN_ID,
    ANALYST_ID,
    TEST_PASSWORD,
    auth_header,
    login_payload,
)


# =====================================================================
# Unidad: emisión y verificación del token
# =====================================================================
def test_settings_pin_15_minutes() -> None:
    """SPEC-04 §2 v2.2: el access token dura exactamente 15 minutos."""
    settings = get_settings()
    assert settings.access_token_expire_minutes == 15
    assert settings.access_token_expire_seconds == 900
    assert access_token_expires_in() == 900
    assert settings.jwt_algorithm == "HS256"


def test_default_access_token_lifetime_is_15_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin variable de entorno, el valor por defecto ya es el normativo."""
    monkeypatch.delenv("ACCESS_TOKEN_EXPIRE_MINUTES", raising=False)
    reset_settings_cache()
    assert get_settings().access_token_expire_minutes == 15


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("ACCESS_TOKEN_EXPIRE_MINUTES", "30"),
        ("ACCESS_TOKEN_EXPIRE_MINUTES", "0"),
        ("REFRESH_TOKEN_EXPIRE_DAYS", "8"),
    ],
    ids=["access-30min", "access-0", "refresh-8d"],
)
def test_settings_reject_lifetimes_above_policy(
    monkeypatch: pytest.MonkeyPatch, variable: str, value: str
) -> None:
    """Expiraciones fuera de los topes (15 min / 7 días) abortan el arranque."""
    monkeypatch.setenv(variable, value)
    reset_settings_cache()
    with pytest.raises(ValueError):
        get_settings()
    monkeypatch.undo()
    reset_settings_cache()


def test_access_token_expires_in_15_minutes() -> None:
    """El claim ``exp`` está 900 s por delante de ``iat``."""
    token = create_access_token(ANALYST_ID, Role.ANALYST.value)
    payload = decode_access_token(token)
    assert payload["exp"] - payload["iat"] == 900


def test_access_token_claims() -> None:
    """Claims mínimos: ``sub``, ``typ``, ``role`` y ``jti`` (anti-replay)."""
    token = create_access_token(ANALYST_ID, Role.ANALYST.value)
    payload = decode_token(token)
    assert payload["sub"] == ANALYST_ID
    assert payload["typ"] == TOKEN_TYPE_ACCESS
    assert payload["role"] == Role.ANALYST.value
    assert payload["jti"]


def test_expired_access_token_is_rejected() -> None:
    """Un token con ``exp`` en el pasado no es verificable."""
    token = create_access_token(
        ANALYST_ID, Role.ANALYST.value, expires_delta=timedelta(seconds=-30)
    )
    with pytest.raises(TokenError):
        decode_token(token)
    # sin verificar expiración sí se puede leer (útil para diagnóstico)
    assert decode_token(token, verify_exp=False)["sub"] == ANALYST_ID


def test_refresh_token_lifetime_and_type() -> None:
    """El refresh token dura 7 días y está tipado como ``refresh``."""
    settings = get_settings()
    token = create_refresh_token(ADMIN_ID, Role.ADMIN.value)
    payload = decode_refresh_token(token)
    assert payload["typ"] == TOKEN_TYPE_REFRESH
    assert payload["exp"] - payload["iat"] == settings.refresh_token_expire_seconds


def test_refresh_token_is_not_accepted_as_access() -> None:
    """Los dos tipos de token no son intercambiables."""
    refresh = create_refresh_token(ADMIN_ID, Role.ADMIN.value)
    access = create_access_token(ADMIN_ID, Role.ADMIN.value)
    with pytest.raises(TokenError):
        decode_access_token(refresh)
    with pytest.raises(TokenError):
        decode_refresh_token(access)


def test_token_signed_with_other_secret_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Firma inválida ⇒ ``TokenError`` (no se acepta un token ajeno)."""
    token = create_access_token(ANALYST_ID, Role.ANALYST.value)
    monkeypatch.setenv("JWT_SECRET_KEY", "otro-secreto-de-prueba-" + "b" * 40)
    reset_settings_cache()
    with pytest.raises(TokenError):
        decode_token(token)


@pytest.mark.parametrize(
    "bad_token",
    ["", "no-es-un-jwt", "aaa.bbb.ccc", "eyJhbGciOiJIUzI1NiJ9.e30.firma-falsa"],
)
def test_malformed_tokens_are_rejected(bad_token: str) -> None:
    """Tokens vacíos o malformados ⇒ ``TokenError``."""
    with pytest.raises(TokenError):
        decode_token(bad_token)


# =====================================================================
# Integración: /auth/login → /auth/refresh → /auth/logout → /auth/me
# =====================================================================
@pytest.fixture
def hashed_client(app: Any, hashed_users: Any) -> Any:
    """Cliente con un operador ADMIN cuyo hash es Argon2id real."""
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_user_store] = lambda: hashed_users
    return TestClient(app)


def test_login_returns_900_and_sets_httponly_secure_cookie(hashed_client: Any) -> None:
    """``/auth/login`` devuelve ``expires_in=900`` y cookie ``HttpOnly``/``Secure``."""
    response = hashed_client.post("/api/v1/auth/login", json=login_payload("admin"))
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 900
    assert decode_access_token(body["access_token"])["sub"] == ADMIN_ID

    set_cookie = response.headers["set-cookie"]
    assert REFRESH_COOKIE_NAME in set_cookie
    assert "HttpOnly" in set_cookie
    assert "Secure" in set_cookie
    assert "samesite=strict" in set_cookie.lower()
    assert "path=/api/v1/auth" in set_cookie.lower()


def test_login_with_wrong_password_returns_401(hashed_client: Any) -> None:
    """Credenciales inválidas ⇒ 401 con el sobre de error del contrato."""
    response = hashed_client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "contraseña-incorrecta"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_refresh_uses_cookie_and_returns_new_access_token(hashed_client: Any) -> None:
    """``/auth/refresh`` renueva el access token a partir de la cookie."""
    login = hashed_client.post("/api/v1/auth/login", json=login_payload("admin"))
    hashed_client.cookies.set(REFRESH_COOKIE_NAME, login.cookies[REFRESH_COOKIE_NAME])

    response = hashed_client.post("/api/v1/auth/refresh")
    assert response.status_code == 200
    assert response.json()["expires_in"] == 900
    assert decode_access_token(response.json()["access_token"])["sub"] == ADMIN_ID
    hashed_client.cookies.clear()


def test_refresh_without_cookie_returns_401(hashed_client: Any) -> None:
    """Sin cookie de refresh no hay renovación (SPEC-04 §2)."""
    response = hashed_client.post("/api/v1/auth/refresh")
    assert response.status_code == 401


def test_refresh_with_access_token_in_cookie_returns_401(hashed_client: Any) -> None:
    """La cookie debe contener un *refresh* token, no un access token."""
    access = create_access_token(ADMIN_ID, Role.ADMIN.value)
    hashed_client.cookies.set(REFRESH_COOKIE_NAME, access)
    response = hashed_client.post("/api/v1/auth/refresh")
    assert response.status_code == 401
    hashed_client.cookies.clear()


def test_logout_requires_auth_and_clears_cookie(hashed_client: Any) -> None:
    """``/auth/logout`` exige identidad y borra la cookie de refresh."""
    anonymous = hashed_client.post("/api/v1/auth/logout")
    assert anonymous.status_code == 401

    token = create_access_token(ADMIN_ID, Role.ADMIN.value)
    response = hashed_client.post("/api/v1/auth/logout", headers=auth_header(token))
    assert response.status_code == 204
    assert REFRESH_COOKIE_NAME in response.headers.get("set-cookie", "")


def test_auth_me_exposes_role(client: Any, tokens: dict[Role, str]) -> None:
    """``/auth/me`` devuelve ``id``, ``username`` y ``role`` (contrato ``User``)."""
    response = client.get("/api/v1/auth/me", headers=auth_header(tokens[Role.VIEWER]))
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "VIEWER"
    assert body["username"] == "viewer"
    assert body["id"]


def test_invalid_bearer_token_returns_401(client: Any) -> None:
    """Token ilegible en ``Authorization`` ⇒ 401."""
    response = client.get("/api/v1/auth/me", headers=auth_header("no-es-un-jwt"))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"
