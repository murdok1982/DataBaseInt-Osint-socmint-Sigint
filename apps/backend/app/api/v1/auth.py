"""Rutas de ``/auth`` (tag ``auth`` del contrato).

Autorizado por: **SPEC-03 §2**, **SPEC-04 §2/§2.1** (v2.2: JWT 15 min, 2FA TOTP
obligatorio para ADMIN con anti-repetición, bloqueo por fuerza bruta → 429) y
**SPEC-06 §1.4** (evento de auditoría ``auth``).

| operationId   | Método | Ruta              | Rol mínimo  |
| :------------ | :----- | :---------------- | :---------- |
| ``authLogin`` | POST   | ``/auth/login``   | público     |
| ``authRefresh``| POST  | ``/auth/refresh`` | autenticado |
| ``authLogout``| POST   | ``/auth/logout``  | autenticado |
| ``authMe``    | GET    | ``/auth/me``      | autenticado |

El refresh token viaja **solo** en cookie ``HttpOnly`` + ``Secure``
(SPEC-04 §2), con ``path=/api/v1/auth`` para minimizar su exposición.
"""

from __future__ import annotations

from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import bearer_scheme, get_current_user, get_user_store
from app.core.audit import audit_auth_event, client_ip
from app.core.config import get_settings
from app.core.errors import detail_from_code
from app.core.login_guard import get_login_throttle, get_totp_replay_guard
from app.core.rbac import Role, unauthorized
from app.core.security import (
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    TokenError,
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
    match_totp_counter,
)
from app.core.users import Principal, UserRecord, UserStore, authenticate

#: Mensaje único para cualquier fallo de credenciales o 2FA (SPEC-04 §2: no
#: revela qué factor falló).
INVALID_CREDENTIALS = "Credenciales o código 2FA incorrectos."
CODE_TOO_MANY_REQUESTS = "TOO_MANY_REQUESTS"

router = APIRouter(prefix="/auth", tags=["auth"])


# =====================================================================
# Esquemas del contrato (openapi.yaml: LoginRequest / TokenResponse / User)
# =====================================================================
class LoginRequest(BaseModel):
    """``LoginRequest`` del contrato."""

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)
    totp_code: Optional[str] = Field(default=None, pattern=r"^[0-9]{6}$")


class TokenResponse(BaseModel):
    """``TokenResponse`` del contrato (``expires_in`` ≤ 900 s)."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserOut(BaseModel):
    """``User`` del contrato."""

    id: str
    username: str
    role: Role


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """Cookie ``HttpOnly``/``Secure`` con el refresh token (SPEC-04 §2)."""
    settings = get_settings()
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=int(settings.refresh_token_expire_seconds),
        httponly=True,
        secure=bool(settings.cookie_secure),
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def _token_response(principal: Principal) -> TokenResponse:
    return TokenResponse(
        access_token=create_access_token(principal.id, principal.role.value),
        token_type="bearer",
        expires_in=access_token_expires_in(),
    )


# =====================================================================
# POST /auth/login — authLogin (público)
# =====================================================================
@router.post(
    "/login",
    operation_id="authLogin",
    response_model=TokenResponse,
    summary="Login del operador",
    responses={
        401: {"description": "Credenciales o 2FA inválidos"},
        429: {"description": "Demasiados intentos fallidos (SPEC-04 §2.1)"},
    },
)
async def auth_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    store: UserStore = Depends(get_user_store),
) -> TokenResponse:
    """Autentica al operador y emite access JWT + refresh cookie.

    Orden (SPEC-04 §2.1): el bloqueo se evalúa **antes** de verificar la
    contraseña, de modo que un atacante bloqueado no obtiene señal alguna.
    """
    throttle = get_login_throttle()
    key = throttle.key(client_ip(request), payload.username)
    retry_after = throttle.retry_after(key)
    if retry_after is not None:
        audit_auth_event(request, "LOCKED", username=payload.username)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=detail_from_code(
                CODE_TOO_MANY_REQUESTS,
                "Demasiados intentos fallidos. Vuelve a intentarlo más tarde.",
            ),
            headers={"Retry-After": str(retry_after)},
        )

    record = authenticate(payload.username, payload.password, store)
    if record is None or not _second_factor_ok(record, payload.totp_code):
        throttle.register_failure(key)
        audit_auth_event(request, "FAILURE", username=payload.username)
        raise unauthorized(INVALID_CREDENTIALS)

    throttle.reset(key)
    principal = record.to_principal()
    audit_auth_event(request, "SUCCESS", principal=principal)
    _set_refresh_cookie(response, create_refresh_token(principal.id, principal.role.value))
    return _token_response(principal)


def _second_factor_ok(record: UserRecord, totp_code: Optional[str]) -> bool:
    """2FA TOTP (SPEC-04 §2 v2.2).

    - Operador con ``totp_secret`` ⇒ código válido y no reutilizado.
    - ``ADMIN`` sin ``totp_secret`` ⇒ rechazado salvo ``ADMIN_TOTP_REQUIRED=false``
      (solo desarrollo; la configuración lo prohíbe en producción).
    - Resto de roles sin secreto ⇒ basta la contraseña.
    """
    if not record.totp_enabled:
        return not (record.role is Role.ADMIN and get_settings().admin_totp_required)
    counter = match_totp_counter(str(record.totp_secret), totp_code or "")
    if counter is None:
        return False
    return get_totp_replay_guard().accept(record.id, counter)


# =====================================================================
# POST /auth/refresh — authRefresh (autenticado por cookie)
# =====================================================================
@router.post(
    "/refresh",
    operation_id="authRefresh",
    response_model=TokenResponse,
    summary="Renueva el access token (cookie refresh)",
    # El contrato declara ``bearerAuth`` (seguridad global); la credencial
    # efectiva es la cookie HttpOnly/Secure (SPEC-04 §2). ``bearer_scheme`` con
    # ``auto_error=False`` documenta el esquema sin exigir cabecera.
    dependencies=[Depends(bearer_scheme)],
    responses={401: {"description": "Refresh token ausente o inválido"}},
)
async def auth_refresh(
    request: Request,
    response: Response,
    store: UserStore = Depends(get_user_store),
) -> TokenResponse:
    """Renueva el access token a partir de la cookie ``HttpOnly``."""
    refresh_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise unauthorized("Falta la cookie de refresh token.")
    try:
        payload = decode_refresh_token(refresh_token)
    except TokenError as exc:
        raise unauthorized(f"Refresh token inválido: {exc}") from exc
    subject = str(payload.get("sub") or "")
    record = store.get_by_id(subject)
    if record is None or not record.is_active:
        raise unauthorized("Operador desconocido o inactivo.")
    principal = record.to_principal()
    _set_refresh_cookie(response, refresh_token)
    return _token_response(principal)


# =====================================================================
# POST /auth/logout — authLogout (autenticado)
# =====================================================================
@router.post(
    "/logout",
    operation_id="authLogout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalida el refresh token",
)
async def auth_logout(
    principal: Principal = Depends(get_current_user),
) -> Response:
    """Invalida la cookie de refresh del operador autenticado."""
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(
        key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH, httponly=True, secure=True
    )
    return response


# =====================================================================
# GET /auth/me — authMe (autenticado)
# =====================================================================
@router.get(
    "/me",
    operation_id="authMe",
    response_model=UserOut,
    summary="Perfil y rol del operador",
    responses={401: {"description": "No autenticado"}},
)
async def auth_me(
    principal: Principal = Depends(get_current_user),
) -> UserOut:
    """Perfil y rol del operador autenticado (SPEC-03 §2)."""
    return UserOut(**principal.to_contract())


__all__ = ["router", "LoginRequest", "TokenResponse", "UserOut"]
