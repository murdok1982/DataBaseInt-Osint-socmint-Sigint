"""Componentes de inyección de dependencias de la API (Agente 3).

Autorizado por: **SPEC-03 §1–§5** y **SPEC-04 §1/§2**.

Aquí viven las dependencias que las rutas consumen:

- Identidad: :func:`get_current_user` (JWT ``Bearer`` → :class:`Principal`), que
  devuelve **401** si las credenciales faltan o son inválidas.
- Directorio de operadores: :func:`get_user_store` (sustituible en tests con
  ``app.dependency_overrides``).
- Acceso al grafo: :func:`get_graph_gateway` (delega en ``GraphRepository``).
- Servicios del Agente 2: :func:`get_nlp_extractor` y :func:`get_graphrag_engine`
  **compuestos** (LLM + ``EntityResolver`` + repositorio). Si el grafo no está
  disponible se eleva :class:`~app.core.errors.ServiceUnavailable` (**503**),
  jamás un 500 ni un error de importación al arrancar.
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.core.apikeys import (
    ApiKeyStore,
    EnvApiKeyStore,
    authenticate_api_key,
)
from app.core.errors import (
    CODE_SERVICE_UNAVAILABLE,
    ServiceUnavailable,
    detail_from_code,
)
from app.core.rbac import Role, forbidden, role_satisfies, unauthorized
from app.core.security import TokenError, decode_access_token
from app.core.users import EnvUserStore, Principal, UserStore

#: ``auto_error=False``: la ausencia de credenciales se traduce a 401 con el
#: sobre de error del contrato (no al 403 por defecto de HTTPBearer).
bearer_scheme = HTTPBearer(auto_error=False, scheme_name="bearerAuth")

#: Esquema M2M ``X-API-Key`` (SPEC-04 §5.2; ``apiKeyAuth`` del contrato).
api_key_scheme = APIKeyHeader(
    name="X-API-Key", auto_error=False, scheme_name="apiKeyAuth"
)


# =====================================================================
# Directorio de operadores
# =====================================================================
@lru_cache(maxsize=1)
def get_user_store() -> UserStore:
    """Store de operadores (SPEC-GAP-3.1: fuente por entorno, sin defaults).

    Sustituible en tests::

        app.dependency_overrides[get_user_store] = lambda: InMemoryUserStore([...])
    """
    return EnvUserStore.from_env(dict(os.environ))


def reset_user_store_cache() -> None:
    """Invalida la caché del store (tests que cambian el entorno)."""
    get_user_store.cache_clear()


# =====================================================================
# Almacén de API Keys M2M (SPEC-04 §5.2)
# =====================================================================
@lru_cache(maxsize=1)
def get_api_key_store() -> ApiKeyStore:
    """Store de API Keys (nodo ``ApiKey``; ``EnvApiKeyStore`` como fallback).

    Sin ``OSINT_API_KEYS_JSON`` el almacén queda vacío: toda petición M2M
    responde 401 (SPEC-04 §4: no existe ninguna API Key por defecto).

    Sustituible en tests::

        app.dependency_overrides[get_api_key_store] = lambda: InMemoryApiKeyStore([...])
    """
    return EnvApiKeyStore.from_env(dict(os.environ))


def reset_api_key_store_cache() -> None:
    """Invalida la caché del store de API Keys (tests que cambian el entorno)."""
    get_api_key_store.cache_clear()


# =====================================================================
# Identidad (JWT Bearer → Principal)
# =====================================================================
def principal_from_token(token: str, store: UserStore) -> Principal:
    """Resuelve el :class:`Principal` a partir de un access token.

    :raises HTTPException: 401 si el token es inválido o el usuario no existe
        o está inactivo.
    """
    try:
        payload = decode_access_token(token)
    except TokenError as exc:
        raise unauthorized(f"Credenciales inválidas: {exc}") from exc
    subject = str(payload.get("sub") or "")
    record = store.get_by_id(subject) or store.get_by_username(subject)
    if record is None or not record.is_active:
        raise unauthorized("Operador desconocido o inactivo.")
    return record.to_principal()


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    store: UserStore = Depends(get_user_store),
) -> Principal:
    """Dependencia de identidad humana: exige ``Authorization: Bearer <access JWT>``."""
    if credentials is None or not credentials.credentials:
        raise unauthorized("Falta el encabezado Authorization: Bearer <token>.")
    if (credentials.scheme or "").lower() != "bearer":
        raise unauthorized("Esquema de autenticación no soportado; usa Bearer.")
    return principal_from_token(credentials.credentials, store)


def principal_from_api_key(raw_key: str, store: ApiKeyStore) -> Principal:
    """Resuelve un :class:`Principal` M2M ``AI_INFORMANT`` desde ``X-API-Key``.

    :raises HTTPException: 401 si la clave no coincide con ningún hash Argon2id
        activo o está revocada.
    """
    record = authenticate_api_key(raw_key, store)
    if record is None:
        raise unauthorized("API Key M2M inválida, revocada o desconocida.")
    return Principal(
        id=record.id, username=record.client_name, role=Role.AI_INFORMANT
    )


async def get_current_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key: Optional[str] = Depends(api_key_scheme),
    store: UserStore = Depends(get_user_store),
    api_keys: ApiKeyStore = Depends(get_api_key_store),
) -> Principal:
    """Identidad unificada: JWT ``Bearer`` (humanos) **o** ``X-API-Key`` (M2M).

    Precede la API Key (SPEC-04 §5.2) y cae al JWT si no hay. Sin credenciales
    ⇒ **401**. Un principal M2M resuelto aquí obtiene 403 en toda ruta que exija
    un rol humano (SPEC-04 §1/§4).
    """
    if api_key:
        return principal_from_api_key(api_key, api_keys)
    if credentials is not None and credentials.credentials:
        if (credentials.scheme or "").lower() != "bearer":
            raise unauthorized("Esquema de autenticación no soportado; usa Bearer.")
        return principal_from_token(credentials.credentials, store)
    raise unauthorized(
        "Falta autenticación: 'Authorization: Bearer <token>' o 'X-API-Key'."
    )


async def get_optional_principal(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    api_key: Optional[str] = Depends(api_key_scheme),
    store: UserStore = Depends(get_user_store),
    api_keys: ApiKeyStore = Depends(get_api_key_store),
) -> Optional[Principal]:
    """Igual que :func:`get_current_principal` pero devuelve ``None`` sin credenciales.

    Usado por operaciones públicas (``security: []``) que aun así quieren conocer
    la identidad si está presente. Una credencial **inválida** sigue dando 401.
    """
    if api_key:
        return principal_from_api_key(api_key, api_keys)
    if credentials is not None and credentials.credentials:
        if (credentials.scheme or "").lower() != "bearer":
            raise unauthorized("Esquema de autenticación no soportado; usa Bearer.")
        return principal_from_token(credentials.credentials, store)
    return None


async def authorize_websocket(
    token: str, min_role: Role, store: Optional[UserStore] = None
) -> Principal:
    """Autoriza el handshake WebSocket (el navegador no puede enviar cabeceras).

    El token se recibe en el query string (``?token=``) y se valida igual que en
    HTTP: 401 sin identidad válida, 403 con rol insuficiente.
    """
    resolved_store = store or get_user_store()
    principal = principal_from_token(token, resolved_store)
    if not role_satisfies(principal.role, min_role):
        raise forbidden(
            f"Rol insuficiente: se requiere {min_role.value} (SPEC-04 §1)."
        )
    return principal


# =====================================================================
# Acceso al grafo (delega en GraphRepository — zona Agente 1)
# =====================================================================
@lru_cache(maxsize=1)
def get_graph_driver() -> Any:
    """Driver Neo4j perezoso y cacheado por proceso (Constitución, Regla 4).

    La base de datos nunca se expone: el driver solo vive dentro del backend.
    Verifica la conectividad al **primer** acceso de grafo para degradar de
    forma controlada (**503**), en lugar de dejar que un error del driver se
    propague como **500** en la primera consulta.
    """
    try:
        from app.db.driver import create_driver, verify_connectivity

        driver = create_driver()
        verify_connectivity(driver)
        return driver
    except ServiceUnavailable:
        raise
    except Exception as exc:  # pragma: no cover - requiere Neo4j real
        raise ServiceUnavailable(f"Grafo no disponible: {exc}") from exc


def get_graph_repository() -> Any:
    """``GraphRepository`` (Agente 1) listo para usar por las rutas de grafo."""
    from app.db.repository import GraphRepository

    try:
        driver = get_graph_driver()
    except ServiceUnavailable:
        raise
    except Exception as exc:  # pragma: no cover - defensivo
        raise ServiceUnavailable(f"Grafo no disponible: {exc}") from exc
    return GraphRepository(driver)


def get_graph_gateway() -> Any:
    """Gateway de grafo/informes usado por ``app/api/v1/{graph,reports}.py``.

    Import perezoso para que el arranque no dependa de Neo4j.
    """
    from app.api.graph_gateway import GraphGateway

    return GraphGateway(get_graph_repository())


def get_job_registry() -> Any:
    """Registro de estado/progreso de los jobs de ingesta (SPEC-02 §5)."""
    from app.core.jobs import get_job_registry as _get_registry

    return _get_registry()


def get_optional_graph_gateway() -> Optional[Any]:
    """Gateway de grafo, o ``None`` si el grafo no está disponible.

    Para operaciones *best-effort* (p. ej. registrar el nodo ``Report`` de
    SPEC-02 §1.5) que no deben convertir una subida ya aceptada en un 503.
    """
    try:
        return get_graph_gateway()
    except ServiceUnavailable:
        return None


def close_graph_driver() -> None:
    """Cierra el driver cacheado (llamado desde el lifespan de la app)."""
    try:
        driver = get_graph_driver()
    except Exception:
        return
    try:
        from app.db.driver import close as _close_driver

        _close_driver(driver)
    except Exception:  # pragma: no cover - cierre best-effort
        pass
    get_graph_driver.cache_clear()


# =====================================================================
# Composition root de los servicios del Agente 2 (SPEC-02 §5 / SPEC-03 §5)
# =====================================================================
def build_entity_resolver(repository: Any = None) -> Any:
    """Compone ``EntityResolver`` (Agente 1) sobre el repositorio de grafo.

    :raises ServiceUnavailable: 503 si el grafo no está disponible.
    """
    from app.services.entity_resolver import EntityResolver

    repo = repository if repository is not None else get_graph_repository()
    return EntityResolver(repo)


def build_nlp_extractor() -> Any:
    """Compone el ``NLPExtractor`` (Agente 2): LLM + EntityResolver + repositorio.

    El LLM por defecto es ``NullLLMClient`` (``LLM_PROVIDER=null``): permite
    operar *out of the box* sin API key ni proveedor (modo recuperación).

    :raises ServiceUnavailable: 503 si el grafo no está disponible.
    """
    from app.services.llm_client import get_llm_client
    from app.services.nlp_extractor import NLPExtractor

    repository = get_graph_repository()
    resolver = build_entity_resolver(repository)
    return NLPExtractor(get_llm_client(), resolver, repository=repository)


def build_graphrag_engine() -> Any:
    """Compone el ``GraphRagEngine`` (Agente 2) sobre el repositorio de lectura.

    ``llm=None`` ⇒ modo recuperación (síntesis determinista con evidencia).

    :raises ServiceUnavailable: 503 si el grafo no está disponible.
    """
    from app.services.graphrag_engine import GraphRagEngine

    return GraphRagEngine(get_graph_repository(), llm=None)


def get_nlp_extractor() -> Any:
    """Extractor NLP compuesto (SPEC-02 §5).

    :raises ServiceUnavailable: 503 si su composición (LLM + EntityResolver +
        repositorio) no está disponible.
    """
    return build_nlp_extractor()


def get_graphrag_engine() -> Any:
    """Motor GraphRAG compuesto (SPEC-03 §5).

    :raises ServiceUnavailable: 503 si su composición (repositorio con
        ``search_nodes``/``neighborhood``) no está disponible.
    """
    return build_graphrag_engine()


# =====================================================================
# Composition root v2.0 (SPEC-02 §5 / SPEC-03 §5-§6)
# =====================================================================
def get_preflight_service() -> Any:
    """``PreflightService`` (Agente 2) sobre el repositorio de grafo.

    Emite/verifica el ``preflight_token`` de la Fase 0 (SPEC-02 §1/§5).

    :raises ServiceUnavailable: 503 si el grafo no está disponible.
    """
    from app.services.preflight import PreflightService

    return PreflightService(get_graph_repository())


def get_coherence_engine() -> Any:
    """``CoherenceEngine`` (Agente 2) sobre los escritores del repositorio.

    :raises ServiceUnavailable: 503 si el grafo no está disponible.
    """
    from app.services.coherence_engine import CoherenceEngine

    return CoherenceEngine(get_graph_repository())


def build_informant_extractor() -> Any:
    """``NLPExtractor`` compuesto **con** ``PreflightService`` (SPEC-02 §1).

    Es el extractor que exige un ``preflight_token`` vigente en la ruta M2M
    ``POST /ingest/informant`` (prohibición de ingesta ciega, Regla 3).

    :raises ServiceUnavailable: 503 si el grafo no está disponible.
    """
    from app.services.llm_client import get_llm_client
    from app.services.nlp_extractor import NLPExtractor

    repository = get_graph_repository()
    resolver = build_entity_resolver(repository)
    return NLPExtractor(
        get_llm_client(),
        resolver,
        repository=repository,
        preflight=get_preflight_service(),
    )


def get_informant_extractor() -> Any:
    """Extractor M2M compuesto (``/ingest/informant``)."""
    return build_informant_extractor()


def service_unavailable(detail: str) -> HTTPException:
    """``503`` con el sobre de error del contrato."""
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=detail_from_code(CODE_SERVICE_UNAVAILABLE, detail),
    )


__all__ = [
    "bearer_scheme",
    "api_key_scheme",
    "get_user_store",
    "reset_user_store_cache",
    "get_api_key_store",
    "reset_api_key_store_cache",
    "principal_from_token",
    "principal_from_api_key",
    "get_current_user",
    "get_current_principal",
    "get_optional_principal",
    "authorize_websocket",
    "get_graph_driver",
    "get_graph_repository",
    "get_graph_gateway",
    "get_optional_graph_gateway",
    "get_job_registry",
    "close_graph_driver",
    "build_entity_resolver",
    "build_nlp_extractor",
    "build_graphrag_engine",
    "build_informant_extractor",
    "get_nlp_extractor",
    "get_graphrag_engine",
    "get_preflight_service",
    "get_coherence_engine",
    "get_informant_extractor",
    "service_unavailable",
]
