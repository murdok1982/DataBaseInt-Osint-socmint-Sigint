"""RBAC v2.0 — roles, matriz de 4 actores y dependencias ``require_*``.

Autorizado por: **SPEC-04 §1** (matriz de 4 actores / 28 celdas), **SPEC-03 §2**
(nombres canónicos de rol) y ``contracts/openapi.yaml`` (SELLADO v2.0.0).

Actores (SPEC-04 §1)
--------------------
- ``ADMIN``        — operador humano con control total.
- ``ANALYST``      — operador humano (sustituye al antiguo ``OPERATOR``).
- ``VIEWER``       — operador humano de solo lectura.
- ``AI_INFORMANT`` — **principal M2M** (no es un ``User``); se autentica por
  API Key (``X-API-Key``) con permisos acotados: solo ``/context/preflight`` y
  ``/ingest/informant``.

Semántica de error (SPEC-03 §1 + contrato ``Forbidden``)
--------------------------------------------------------
- **401** : no hay credenciales válidas.
- **403** : hay identidad válida pero el rol/principal es insuficiente. Cada
  celda "No" de SPEC-04 §1 devuelve 403 (SPEC-04 §4).

Jerarquía
---------
Los tres roles **humanos** son jerárquicos (``ADMIN`` ⊃ ``ANALYST`` ⊃ ``VIEWER``);
``AI_INFORMANT`` está **fuera** de esa jerarquía: solo satisface a sí mismo.
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Callable, FrozenSet, Iterable, Optional

from fastapi import Depends, HTTPException, status

from app.core.errors import CODE_FORBIDDEN, detail_from_code

if TYPE_CHECKING:  # pragma: no cover - solo tipado (evita ciclo de imports)
    from app.core.users import Principal


class Role(str, Enum):
    """Roles canónicos v2.0 (SPEC-04 §1). El valor casa con ``openapi.yaml``."""

    ADMIN = "ADMIN"
    ANALYST = "ANALYST"
    VIEWER = "VIEWER"
    #: Principal M2M (no es un ``User``); se autentica por API Key.
    AI_INFORMANT = "AI_INFORMANT"


#: Roles humanos, sujetos a la jerarquía de ``require_role``.
HUMAN_ROLES: FrozenSet[Role] = frozenset({Role.ADMIN, Role.ANALYST, Role.VIEWER})

#: Principales máquina-a-máquina.
M2M_ROLES: FrozenSet[Role] = frozenset({Role.AI_INFORMANT})

#: Ranking jerárquico *humano*: a mayor número, mayor privilegio.
ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 1,
    Role.ANALYST: 2,
    Role.ADMIN: 3,
}


class Permission(str, Enum):
    """Las 7 capacidades de la matriz de SPEC-04 §1 (una por fila)."""

    LOGIN = "LOGIN"
    GRAPH_READ = "GRAPH_READ"
    ANALYSIS_WRITE = "ANALYSIS_WRITE"
    GRAPH_LOCK = "GRAPH_LOCK"
    INGEST_OSINT = "INGEST_OSINT"
    COHERENCE_RESOLVE = "COHERENCE_RESOLVE"
    ADMIN_MANAGE = "ADMIN_MANAGE"


#: Celda a celda, la tabla de SPEC-04 §1 (7 capacidades × 4 actores = 28 celdas).
SPEC_04_MATRIX: dict[Permission, dict[Role, bool]] = {
    Permission.LOGIN: {
        Role.ADMIN: True,
        Role.ANALYST: True,
        Role.AI_INFORMANT: False,  # usa API Key M2M, no login interactivo
        Role.VIEWER: True,
    },
    Permission.GRAPH_READ: {
        Role.ADMIN: True,
        Role.ANALYST: True,
        # Solo lectura vía Pre-Flight: NO puede usar /graph/* (se evalúa aparte).
        Role.AI_INFORMANT: False,
        Role.VIEWER: True,
    },
    Permission.ANALYSIS_WRITE: {
        Role.ADMIN: True,
        Role.ANALYST: True,
        Role.AI_INFORMANT: False,
        Role.VIEWER: False,
    },
    Permission.GRAPH_LOCK: {
        Role.ADMIN: True,
        Role.ANALYST: True,
        Role.AI_INFORMANT: False,
        Role.VIEWER: False,
    },
    Permission.INGEST_OSINT: {
        Role.ADMIN: True,
        Role.ANALYST: True,
        Role.AI_INFORMANT: True,  # vía /ingest/informant con referencias
        Role.VIEWER: False,
    },
    Permission.COHERENCE_RESOLVE: {
        Role.ADMIN: True,
        Role.ANALYST: True,
        Role.AI_INFORMANT: False,
        Role.VIEWER: False,
    },
    Permission.ADMIN_MANAGE: {
        Role.ADMIN: True,
        Role.ANALYST: False,
        Role.AI_INFORMANT: False,
        Role.VIEWER: False,
    },
}

#: Texto de la fila de SPEC-04 §1 al que corresponde cada permiso (trazabilidad).
SPEC_04_MATRIX_LABEL: dict[Permission, str] = {
    Permission.LOGIN: "Login interactivo (usuario/contraseña + JWT)",
    Permission.GRAPH_READ: "Explorar grafo y abrir informes originales",
    Permission.ANALYSIS_WRITE: "Crear/editar Analysis e IntelRequirement",
    Permission.GRAPH_LOCK: "Bloquear nodos verificados (locked_by_analyst)",
    Permission.INGEST_OSINT: "Subir informes OSINT y extraer relaciones",
    Permission.COHERENCE_RESOLVE: "Resolver alertas de coherencia (CoherenceAlert)",
    Permission.ADMIN_MANAGE: "Gestionar operadores, API Keys y backups",
}


def allowed_roles_for_permission(permission: Permission) -> FrozenSet[Role]:
    """Actores que satisfacen una capacidad (celdas "Sí" de SPEC-04 §1)."""
    return frozenset(
        role for role, allowed in SPEC_04_MATRIX[permission].items() if allowed
    )


#: Rol mínimo *humano* por permiso (el menos privilegiado que satisface la fila),
#: o ``None`` si la fila no es expresable como un único rol humano (p. ej.
#: ``INGEST_OSINT`` incluye además al principal M2M ``AI_INFORMANT``).
MIN_ROLE_BY_PERMISSION: dict[Permission, Optional[Role]] = {
    Permission.LOGIN: Role.VIEWER,
    Permission.GRAPH_READ: Role.VIEWER,
    Permission.ANALYSIS_WRITE: Role.ANALYST,
    Permission.GRAPH_LOCK: Role.ANALYST,
    Permission.INGEST_OSINT: None,  # incluye AI_INFORMANT → no es solo-jerarquía
    Permission.COHERENCE_RESOLVE: Role.ANALYST,
    Permission.ADMIN_MANAGE: Role.ADMIN,
}


# =====================================================================
# Conjuntos de actores autorizados por operación (fuente de verdad RBAC)
# =====================================================================
#: Actores autorizados por ``operationId`` del contrato SELLADO v2.0.0.
#: ``None`` = endpoint público (``security: []``).
ALLOWED_ROLES_BY_OPERATION: dict[str, Optional[FrozenSet[Role]]] = {
    # --- auth (SPEC-03 §3) ---
    "authLogin": None,  # público
    "authRefresh": HUMAN_ROLES,
    "authLogout": HUMAN_ROLES,
    "authMe": HUMAN_ROLES,
    # --- graph (SPEC-03 §4) ---
    "listGraphNodes": HUMAN_ROLES,
    "getGraphNode": HUMAN_ROLES,
    "listGraphEdges": HUMAN_ROLES,
    "getGraphNeighborhood": HUMAN_ROLES,
    "updateGraphNode": frozenset({Role.ADMIN, Role.ANALYST}),
    "lockGraphNode": frozenset({Role.ADMIN, Role.ANALYST}),
    "deleteGraphNode": frozenset({Role.ADMIN}),
    # --- ingest / reports (SPEC-03 §5) ---
    "postContextPreflight": frozenset(
        {Role.ADMIN, Role.ANALYST, Role.AI_INFORMANT}
    ),
    "createIngestJob": frozenset({Role.ADMIN, Role.ANALYST}),
    "createInformantIngestJob": frozenset({Role.AI_INFORMANT}),
    "getIngestJob": HUMAN_ROLES,
    "ingestProgressWs": HUMAN_ROLES,
    "getReport": HUMAN_ROLES,
    "getReportFile": HUMAN_ROLES,
    "getReportChunks": HUMAN_ROLES,
    # --- analyses (SPEC-03 §6) ---
    "createAnalysis": frozenset({Role.ADMIN, Role.ANALYST}),
    "getAnalysis": HUMAN_ROLES,
    "updateAnalysis": frozenset({Role.ADMIN, Role.ANALYST}),
    "createIntelRequirement": frozenset({Role.ADMIN, Role.ANALYST}),
    # --- coherence (SPEC-03 §6) ---
    "listCoherenceAlerts": frozenset({Role.ADMIN, Role.ANALYST}),
    "resolveCoherenceAlert": frozenset({Role.ADMIN, Role.ANALYST}),
    # --- search (SPEC-03 §7) ---
    "searchRag": HUMAN_ROLES,
    # --- system (contrato v1.1.0) ---
    "getInfo": None,  # público
}


def _min_human_role(allowed: Optional[FrozenSet[Role]]) -> Optional[Role]:
    """Rol humano menos privilegiado del conjunto, si este es solo-humano."""
    if not allowed or allowed - HUMAN_ROLES:
        return None
    return min(allowed, key=lambda role: ROLE_RANK[role])


#: Rol mínimo por ``operationId`` (compatibilidad/documentación). ``None`` para
#: endpoints públicos, M2M o mixtos (usar :data:`ALLOWED_ROLES_BY_OPERATION`).
MIN_ROLE_BY_OPERATION: dict[str, Optional[Role]] = {
    operation_id: _min_human_role(allowed)
    for operation_id, allowed in ALLOWED_ROLES_BY_OPERATION.items()
}


#: (method, path, operationId, permiso de matriz) — trazabilidad explícita.
ENDPOINT_MATRIX: tuple[tuple[str, str, str, Optional[Permission]], ...] = (
    ("POST", "/api/v1/auth/login", "authLogin", Permission.LOGIN),
    ("POST", "/api/v1/auth/refresh", "authRefresh", None),
    ("POST", "/api/v1/auth/logout", "authLogout", None),
    ("GET", "/api/v1/auth/me", "authMe", None),
    ("GET", "/api/v1/graph/nodes", "listGraphNodes", Permission.GRAPH_READ),
    ("GET", "/api/v1/graph/nodes/{id}", "getGraphNode", Permission.GRAPH_READ),
    ("PUT", "/api/v1/graph/nodes/{id}", "updateGraphNode", Permission.ANALYSIS_WRITE),
    ("DELETE", "/api/v1/graph/nodes/{id}", "deleteGraphNode", Permission.ADMIN_MANAGE),
    (
        "POST",
        "/api/v1/graph/nodes/{id}/lock",
        "lockGraphNode",
        Permission.GRAPH_LOCK,
    ),
    ("GET", "/api/v1/graph/edges", "listGraphEdges", Permission.GRAPH_READ),
    (
        "GET",
        "/api/v1/graph/neighborhood/{id}",
        "getGraphNeighborhood",
        Permission.GRAPH_READ,
    ),
    ("POST", "/api/v1/context/preflight", "postContextPreflight", None),
    ("POST", "/api/v1/ingest", "createIngestJob", Permission.INGEST_OSINT),
    (
        "POST",
        "/api/v1/ingest/informant",
        "createInformantIngestJob",
        Permission.INGEST_OSINT,
    ),
    ("GET", "/api/v1/ingest/{job_id}", "getIngestJob", Permission.GRAPH_READ),
    ("GET", "/api/v1/ingest/{job_id}/ws", "ingestProgressWs", Permission.GRAPH_READ),
    ("GET", "/api/v1/reports/{id}", "getReport", Permission.GRAPH_READ),
    ("GET", "/api/v1/reports/{id}/file", "getReportFile", Permission.GRAPH_READ),
    ("GET", "/api/v1/reports/{id}/chunks", "getReportChunks", Permission.GRAPH_READ),
    ("POST", "/api/v1/analyses", "createAnalysis", Permission.ANALYSIS_WRITE),
    ("GET", "/api/v1/analyses/{id}", "getAnalysis", Permission.GRAPH_READ),
    ("PUT", "/api/v1/analyses/{id}", "updateAnalysis", Permission.ANALYSIS_WRITE),
    (
        "POST",
        "/api/v1/analyses/{id}/requirements",
        "createIntelRequirement",
        Permission.ANALYSIS_WRITE,
    ),
    ("GET", "/api/v1/coherence/alerts", "listCoherenceAlerts", Permission.COHERENCE_RESOLVE),
    (
        "PATCH",
        "/api/v1/coherence/alerts/{alert_id}/resolve",
        "resolveCoherenceAlert",
        Permission.COHERENCE_RESOLVE,
    ),
    ("POST", "/api/v1/search/rag", "searchRag", Permission.GRAPH_READ),
    ("GET", "/api/v1/info", "getInfo", None),
)


def has_permission(role: Role, permission: Permission) -> bool:
    """Comprueba una celda de la matriz de SPEC-04 §1 (función pura)."""
    return bool(SPEC_04_MATRIX[permission][role])


def role_satisfies(role: Role, min_role: Role) -> bool:
    """``True`` si ``role`` alcanza ``min_role``.

    Los roles humanos usan la jerarquía ``ADMIN ⊃ ANALYST ⊃ VIEWER``;
    ``AI_INFORMANT`` queda fuera de ella y solo satisface a sí mismo.
    """
    if role not in HUMAN_ROLES or min_role not in HUMAN_ROLES:
        return role is min_role
    return ROLE_RANK[role] >= ROLE_RANK[min_role]


def role_allowed(role: Role, allowed: Iterable[Role]) -> bool:
    """``True`` si ``role`` satisface alguno de los actores autorizados."""
    return any(role_satisfies(role, candidate) for candidate in allowed)


def allowed_roles_for_operation(operation_id: str) -> Optional[FrozenSet[Role]]:
    """Actores autorizados de un ``operationId`` (``None`` = público)."""
    return ALLOWED_ROLES_BY_OPERATION.get(operation_id)


def permission_for_operation(operation_id: str) -> Optional[Permission]:
    """Permiso de la matriz asociado a un ``operationId`` (o ``None``)."""
    for _method, _path, op_id, permission in ENDPOINT_MATRIX:
        if op_id == operation_id:
            return permission
    return None


def required_role_for_operation(operation_id: str) -> Optional[Role]:
    """Rol mínimo *humano* del ``operationId`` (compatibilidad)."""
    return MIN_ROLE_BY_OPERATION.get(operation_id)


def forbidden(message: str) -> HTTPException:
    """``403`` con el sobre de error del contrato (rol insuficiente)."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=detail_from_code(CODE_FORBIDDEN, message),
    )


def unauthorized(message: str) -> HTTPException:
    """``401`` con el sobre de error del contrato (sin identidad válida)."""
    from app.core.errors import CODE_UNAUTHORIZED

    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail_from_code(CODE_UNAUTHORIZED, message),
        headers={"WWW-Authenticate": "Bearer"},
    )


def require_role(min_role: Role) -> Callable[..., "Principal"]:
    """Dependencia FastAPI que exige un rol humano mínimo (SPEC-04 §1).

    Devuelve **403** si la identidad es válida pero el rol es insuficiente (un
    principal M2M ``AI_INFORMANT`` nunca alcanza un rol humano); el 401 queda
    reservado a la ausencia/invalidez de credenciales.
    """
    # Import perezoso: rompe el ciclo rbac → api.deps → users → rbac.
    from app.api.deps import get_current_principal

    async def _guard(
        principal: "Principal" = Depends(get_current_principal),
    ) -> "Principal":
        if not role_satisfies(principal.role, min_role):
            raise forbidden(
                f"Rol insuficiente: se requiere {min_role.value} (SPEC-04 §1)."
            )
        return principal

    _guard.__name__ = f"require_role_{min_role.value.lower()}"
    return _guard


def require_roles(allowed: Iterable[Role]) -> Callable[..., "Principal"]:
    """Dependencia FastAPI que exige uno de los actores autorizados.

    A diferencia de :func:`require_role`, admite conjuntos mixtos (p. ej.
    ``{ANALYST, AI_INFORMANT}``) y amplía los roles humanos por jerarquía
    (``ADMIN`` también satisface ``{ANALYST, AI_INFORMANT}``).
    """
    from app.api.deps import get_current_principal

    allowed_set: FrozenSet[Role] = frozenset(allowed)
    names = ", ".join(sorted(role.value for role in allowed_set))

    async def _guard(
        principal: "Principal" = Depends(get_current_principal),
    ) -> "Principal":
        if not role_allowed(principal.role, allowed_set):
            raise forbidden(
                f"Rol insuficiente: se requiere uno de {{{names}}} (SPEC-04 §1)."
            )
        return principal

    _guard.__name__ = "require_roles_" + "_".join(
        sorted(role.value.lower() for role in allowed_set)
    )
    return _guard


def require_operation(operation_id: str) -> Callable[..., "Principal"]:
    """Dependencia que aplica el conjunto autorizado del ``operationId``.

    ``None`` (público) no añade guardia: devuelve una dependencia que solo
    resuelve la identidad si existe (sin exigirla).
    """
    allowed = ALLOWED_ROLES_BY_OPERATION.get(operation_id)
    if allowed is None:
        from app.api.deps import get_optional_principal

        return get_optional_principal
    return require_roles(allowed)


__all__ = [
    "Role",
    "Permission",
    "HUMAN_ROLES",
    "M2M_ROLES",
    "ROLE_RANK",
    "SPEC_04_MATRIX",
    "SPEC_04_MATRIX_LABEL",
    "MIN_ROLE_BY_PERMISSION",
    "MIN_ROLE_BY_OPERATION",
    "ALLOWED_ROLES_BY_OPERATION",
    "ENDPOINT_MATRIX",
    "has_permission",
    "role_satisfies",
    "role_allowed",
    "allowed_roles_for_permission",
    "allowed_roles_for_operation",
    "permission_for_operation",
    "required_role_for_operation",
    "forbidden",
    "unauthorized",
    "require_role",
    "require_roles",
    "require_operation",
]
