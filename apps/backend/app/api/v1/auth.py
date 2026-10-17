"""Rutas de ``/auth`` (tag ``auth`` del contrato).

Autorizado por: **SPEC-03 §2** y **SPEC-04 §2**.

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

from typing import Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field

from app.api.deps import bearer_scheme, get_current_user, get_user_store
from app.core.config import get_settings
from app.core.rbac import Role, unauthorized
from app.core.security import (
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
    TokenError,
    access_token_expires_in,
    create_access_token,
    create_refresh_token,
    decode_refresh_token,
)
from app.core.users import Principal, UserStore, authenticate

router = APIRouter(prefix="/auth", tags=["auth"])


# =====================================================================
# Esquemas del contrato (openapi.yaml: LoginRequest / TokenResponse / User)
# =====================================================================
class LoginRequest(BaseModel):
    """``LoginRequest`` del contrato."""

    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    """``TokenResponse`` del contrato (``expires_in`` = 1800 s)."""

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
    responses={401: {"description": "Credenciales inválidas"}},
)
async def auth_login(
    payload: LoginRequest,
    response: Response,
    store: UserStore = Depends(get_user_store),
) -> TokenResponse:
    """Autentica al operador y emite access JWT + refresh cookie."""
    record = authenticate(payload.username, payload.password, store)
    if record is None:
        raise unauthorized("Usuario o contraseña incorrectos.")
    principal = record.to_principal()
    _set_refresh_cookie(response, create_refresh_token(principal.id, principal.role.value))
    return _token_response(principal)


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
