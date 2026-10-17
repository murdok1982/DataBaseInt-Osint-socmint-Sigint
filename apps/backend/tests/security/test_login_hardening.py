"""Endurecimiento del login: 2FA TOTP y bloqueo por fuerza bruta.

Autorizado por: **SPEC-04 §2 v2.2** (TOTP obligatorio para ADMIN, anti-repetición,
mensaje genérico) y **SPEC-04 §2.1** (5 fallos en 15 min ⇒ ``429`` +
``Retry-After``; un éxito reinicia el contador).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.api.deps import get_user_store
from app.core.config import get_settings, reset_settings_cache
from app.core.login_guard import LoginThrottle, TotpReplayGuard
from app.core.rbac import Role
from app.core.security import (
    generate_totp_secret,
    match_totp_counter,
    totp_now,
    verify_totp,
)
from app.core.users import UserRecord
from tests.security.conftest import (
    ADMIN_TOTP_SECRET,
    TEST_PASSWORD,
    login_payload,
)

LOGIN = "/api/v1/auth/login"
GENERIC = "Credenciales o código 2FA incorrectos."


@pytest.fixture
def login_client(app: Any, hashed_users: Any) -> Any:
    from fastapi.testclient import TestClient

    app.dependency_overrides[get_user_store] = lambda: hashed_users
    return TestClient(app)


# =====================================================================
# TOTP (unidad)
# =====================================================================
def test_generated_totp_secret_has_160_bits_and_is_base32() -> None:
    secret = generate_totp_secret()
    assert len(secret) == 32  # 20 bytes -> 32 caracteres base32 sin relleno
    assert secret != generate_totp_secret()
    assert verify_totp(secret, totp_now(secret))


def test_totp_rfc6238_reference_vector() -> None:
    """Vector de RFC 6238 (SHA-1, T=59 s): 94287082 → 6 dígitos ``287082``."""
    rfc_secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # "12345678901234567890"
    assert totp_now(rfc_secret, at=59) == "287082"


def test_totp_tolerates_one_period_of_clock_skew_only() -> None:
    now = 1_700_000_000
    previous = totp_now(ADMIN_TOTP_SECRET, at=now - 30)
    too_old = totp_now(ADMIN_TOTP_SECRET, at=now - 90)
    assert verify_totp(ADMIN_TOTP_SECRET, previous, at=now)
    assert not verify_totp(ADMIN_TOTP_SECRET, too_old, at=now)


def test_replay_guard_rejects_reused_or_older_counters() -> None:
    guard = TotpReplayGuard()
    counter = match_totp_counter(ADMIN_TOTP_SECRET, totp_now(ADMIN_TOTP_SECRET))
    assert counter is not None
    assert guard.accept("u1", counter)
    assert not guard.accept("u1", counter)
    assert not guard.accept("u1", counter - 1)
    assert guard.accept("u2", counter)  # el contador es por operador


# =====================================================================
# 2FA en /auth/login
# =====================================================================
def test_admin_with_valid_totp_logs_in(login_client: Any) -> None:
    response = login_client.post(LOGIN, json=login_payload("admin"))
    assert response.status_code == 200, response.text


@pytest.mark.parametrize(
    "totp_code", [None, "000000"], ids=["sin-codigo", "codigo-erroneo"]
)
def test_admin_without_valid_totp_is_401_with_generic_message(
    login_client: Any, totp_code: Any
) -> None:
    payload: dict[str, Any] = {"username": "admin", "password": TEST_PASSWORD}
    if totp_code is not None:
        payload["totp_code"] = totp_code
    response = login_client.post(LOGIN, json=payload)
    assert response.status_code == 401
    assert response.json()["error"]["message"] == GENERIC


def test_wrong_password_uses_the_same_generic_message(login_client: Any) -> None:
    response = login_client.post(
        LOGIN, json={"username": "analyst", "password": "incorrecta-123456"}
    )
    assert response.status_code == 401
    assert response.json()["error"]["message"] == GENERIC


def test_reused_totp_code_is_rejected(login_client: Any) -> None:
    payload = login_payload("admin")
    assert login_client.post(LOGIN, json=payload).status_code == 200
    replay = login_client.post(LOGIN, json=payload)
    assert replay.status_code == 401


def test_malformed_totp_code_is_rejected_by_validation(login_client: Any) -> None:
    response = login_client.post(
        LOGIN,
        json={"username": "admin", "password": TEST_PASSWORD, "totp_code": "12ab"},
    )
    assert response.status_code == 422


def test_admin_without_totp_secret_cannot_log_in(
    app: Any, hashed_users: Any
) -> None:
    """Un ADMIN sin ``totp_secret`` no entra mientras el 2FA sea obligatorio."""
    from fastapi.testclient import TestClient

    hashed_users.users.append(
        UserRecord(
            id="eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee",
            username="admin2",
            password_hash=hashed_users.users[0].password_hash,
            role=Role.ADMIN,
        )
    )
    app.dependency_overrides[get_user_store] = lambda: hashed_users
    response = TestClient(app).post(
        LOGIN, json={"username": "admin2", "password": TEST_PASSWORD}
    )
    assert response.status_code == 401


def test_admin_totp_can_only_be_relaxed_outside_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADMIN_TOTP_REQUIRED", "false")
    monkeypatch.setenv("ENVIRONMENT", "test")
    reset_settings_cache()
    assert get_settings().admin_totp_required is False

    monkeypatch.setenv("ENVIRONMENT", "production")
    reset_settings_cache()
    with pytest.raises(ValueError):
        get_settings()
    monkeypatch.undo()
    reset_settings_cache()


def test_analyst_with_totp_secret_must_present_code(
    app: Any, hashed_users: Any
) -> None:
    """Si un ANALYST activa 2FA, el código pasa a ser obligatorio para él."""
    from fastapi.testclient import TestClient

    secret = generate_totp_secret()
    analyst = next(u for u in hashed_users.users if u.username == "analyst")
    analyst.totp_secret = secret
    app.dependency_overrides[get_user_store] = lambda: hashed_users
    client = TestClient(app)
    without = client.post(LOGIN, json={"username": "analyst", "password": TEST_PASSWORD})
    assert without.status_code == 401
    with_code = client.post(
        LOGIN,
        json={
            "username": "analyst",
            "password": TEST_PASSWORD,
            "totp_code": totp_now(secret),
        },
    )
    assert with_code.status_code == 200


# =====================================================================
# Fuerza bruta (SPEC-04 §2.1)
# =====================================================================
def test_sixth_failed_attempt_is_429_with_retry_after(login_client: Any) -> None:
    bad = {"username": "analyst", "password": "incorrecta-123456"}
    for _ in range(5):
        assert login_client.post(LOGIN, json=bad).status_code == 401
    locked = login_client.post(LOGIN, json=bad)
    assert locked.status_code == 429
    assert locked.json()["error"]["code"] == "TOO_MANY_REQUESTS"
    assert 1 <= int(locked.headers["Retry-After"]) <= 900


def test_lockout_applies_even_with_the_right_password(login_client: Any) -> None:
    bad = {"username": "analyst", "password": "incorrecta-123456"}
    for _ in range(5):
        login_client.post(LOGIN, json=bad)
    good = login_client.post(LOGIN, json=login_payload("analyst"))
    assert good.status_code == 429


def test_successful_login_resets_the_counter(login_client: Any) -> None:
    bad = {"username": "analyst", "password": "incorrecta-123456"}
    for _ in range(4):
        login_client.post(LOGIN, json=bad)
    assert login_client.post(LOGIN, json=login_payload("analyst")).status_code == 200
    for _ in range(4):
        assert login_client.post(LOGIN, json=bad).status_code == 401


def test_lockout_is_scoped_per_username(login_client: Any) -> None:
    bad = {"username": "analyst", "password": "incorrecta-123456"}
    for _ in range(6):
        login_client.post(LOGIN, json=bad)
    assert login_client.post(LOGIN, json=login_payload("viewer")).status_code == 200


def test_throttle_window_expires() -> None:
    now = [1000.0]
    throttle = LoginThrottle(max_failures=2, window_seconds=60, clock=lambda: now[0])
    key = throttle.key("10.0.0.1", "Analyst")
    throttle.register_failure(key)
    throttle.register_failure(key)
    assert throttle.retry_after(key) == 60
    now[0] += 59.5
    assert throttle.retry_after(key) == 1
    now[0] += 1
    assert throttle.retry_after(key) is None
    assert throttle.key("10.0.0.1", "ANALYST") == key  # insensible a mayúsculas


def test_proxy_header_is_ignored_unless_trusted(
    login_client: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin ``TRUST_PROXY_HEADERS`` un atacante no evade el bloqueo rotando ``X-Real-IP``."""
    bad = {"username": "analyst", "password": "incorrecta-123456"}
    for index in range(5):
        login_client.post(LOGIN, json=bad, headers={"X-Real-IP": f"198.51.100.{index}"})
    locked = login_client.post(LOGIN, json=bad, headers={"X-Real-IP": "198.51.100.99"})
    assert locked.status_code == 429
