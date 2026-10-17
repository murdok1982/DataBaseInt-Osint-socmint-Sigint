"""Gateway de acceso al grafo e informes para las rutas HTTP.

Autorizado por: **SPEC-03 §3–§4** (rutas de grafo e informes) y
``contracts/openapi.yaml`` (SELLADO v2.0.0).

Rol de este módulo
------------------
Las rutas de ``app/api/v1/graph.py`` y ``app/api/v1/reports.py`` **no hablan con
Neo4j ni generan Cypher**: eso es propiedad del Agente 1 (``app/db/**``). El
gateway es una capa de traducción fina que delega por nombre en las
capacidades de ``GraphRepository`` y:

- si el repositorio expone la capacidad, la usa tal cual;
- si **aún no la expone**, eleva :class:`ServiceUnavailable` → **503**
  ``{"error": {"code": "SERVICE_UNAVAILABLE"}}`` (nunca un 500 ni un fallo de
  importación). Ver **SPEC-GAP-3.2** en la respuesta al Orquestador: se
  solicitan al Agente 1 los métodos ``list_nodes``, ``list_edges``,
  ``get_node``, ``update_node``, ``delete_node``, ``neighborhood``,
  ``get_report``, ``get_report_file_path`` y ``get_report_chunks``.

El comportamiento se verifica con un repositorio en memoria inyectado
(``app.dependency_overrides[get_graph_gateway]``), de modo que la matriz RBAC sea
comprobable sin base de datos.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from app.core.errors import ConflictError, ServiceUnavailable

#: Capacidades que las rutas necesitan del repositorio (Agente 1).
REQUESTED_CAPABILITIES: tuple[str, ...] = (
    "list_nodes",
    "get_node",
    "update_node",
    "delete_node",
    "set_node_locked",
    "list_edges",
    "neighborhood",
    "get_report",
    "get_report_file_path",
    "get_report_chunks",
    "create_analysis",
    "get_analysis",
    "list_analyses",
    "update_analysis",
    "create_requirement",
    "list_requirements",
    "list_alerts",
    "resolve_alert",
)


@runtime_checkable
class GraphAccessPort(Protocol):
    """Puerto de acceso a grafo/informes consumido por las rutas."""

    def list_nodes(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_node(self, node_id: str) -> Optional[dict[str, Any]]: ...

    def update_node(
        self, node_id: str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]: ...

    def delete_node(self, node_id: str) -> None: ...

    def list_edges(self, **kwargs: Any) -> dict[str, Any]: ...

    def neighborhood(self, node_id: str, depth: int) -> dict[str, Any]: ...

    def get_report(self, report_id: str) -> Optional[dict[str, Any]]: ...

    def get_report_file_path(self, report_id: str) -> str: ...

    def get_report_chunks(self, report_id: str) -> dict[str, Any]: ...


class GraphGateway:
    """Traduce llamadas de ruta → :class:`app.db.repository.GraphRepository`."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    # -----------------------------------------------------------------
    # Interno
    # -----------------------------------------------------------------
    def _invoke(self, capability: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self._repository, capability, None)
        if method is None:
            raise ServiceUnavailable(
                "Grafo/informes no disponibles: el repositorio no expone "
                f"'{capability}' todavía (solicitado al Agente 1)."
            )
        try:
            return method(*args, **kwargs)
        except ServiceUnavailable:
            raise
        except Exception as exc:  # pragma: no cover - requiere Neo4j real
            # Inmutabilidad analítica (Constitución Regla 2): el nodo bloqueado
            # se traduce a 409 (contrato ``Conflict``), nunca a 503 ni 500.
            if type(exc).__name__ == "NodeLockedError":
                raise ConflictError(str(exc)) from exc
            raise ServiceUnavailable(
                f"Grafo/informes no disponibles ({capability}): {exc}"
            ) from exc

    def register_report(
        self, report_id: str, metadata: dict[str, Any]
    ) -> bool:
        """Registra el nodo ``Report`` con sus metadatos (SPEC-02 §1.5).

        *Best-effort*: si el grafo no está disponible, la subida ya aceptada no
        se degrada a error (el informe está almacenado y el job encolado).
        Devuelve ``True`` si el registro se realizó.
        """
        register = getattr(self._repository, "register_report", None)
        upsert = getattr(self._repository, "upsert_entity", None)
        props = dict(metadata)
        props.pop("report_id", None)
        try:
            if callable(register):
                register(report_id, props)
                return True
            if callable(upsert):
                upsert("Report", report_id, props)
                return True
        except Exception:  # pragma: no cover - grafo no disponible
            return False
        return False

    @staticmethod
    def _as_dict(result: Any, capability: str) -> dict[str, Any]:
        if result is None:
            return {}
        if isinstance(result, dict):
            return result
        return {"items": list(result)}

    # -----------------------------------------------------------------
    # Grafo (SPEC-03 §3)
    # -----------------------------------------------------------------
    def list_nodes(
        self,
        label: Optional[str],
        q: Optional[str],
        from_: Optional[str],
        to: Optional[str],
        min_confidence: Optional[float],
        limit: int,
        cursor: Optional[str],
    ) -> dict[str, Any]:
        """``GET /graph/nodes`` — lista/filtra nodos (VIEWER+)."""
        result = self._invoke(
            "list_nodes",
            label=label,
            q=q,
            from_=from_,
            to=to,
            min_confidence=min_confidence,
            limit=limit,
            cursor=cursor,
        )
        payload = self._as_dict(result, "list_nodes")
        payload.setdefault("items", [])
        payload.setdefault("next_cursor", None)
        return payload

    def get_node(self, node_id: str) -> Optional[dict[str, Any]]:
        """``GET /graph/nodes/{id}`` — ficha completa de nodo (VIEWER+).

        Devuelve ``None`` (→ 404 en la ruta) si el nodo no existe.
        """
        result = self._invoke("get_node", node_id)
        return None if result is None else self._as_dict(result, "get_node")

    def update_node(
        self, node_id: str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """``PUT /graph/nodes/{id}`` — edita/fusiona nodo (ANALYST+)."""
        result = self._invoke("update_node", node_id, payload)
        return None if result is None else self._as_dict(result, "update_node")

    def delete_node(self, node_id: str) -> None:
        """``DELETE /graph/nodes/{id}`` — borra nodo (ADMIN)."""
        self._invoke("delete_node", node_id)

    def list_edges(
        self, min_confidence: Optional[float], limit: int
    ) -> dict[str, Any]:
        """``GET /graph/edges`` — lista/filtra aristas (VIEWER+)."""
        result = self._invoke(
            "list_edges", min_confidence=min_confidence, limit=limit
        )
        payload = self._as_dict(result, "list_edges")
        payload.setdefault("items", [])
        return payload

    def neighborhood(self, node_id: str, depth: int) -> dict[str, Any]:
        """``GET /graph/neighborhood/{id}`` — vecindad a N saltos (VIEWER+).

        ``depth`` está acotado a 1..3 por el contrato (SPEC-03 §6).
        """
        payload = self._as_dict(
            self._invoke("neighborhood", node_id, depth), "neighborhood"
        )
        payload.setdefault("nodes", [])
        payload.setdefault("edges", [])
        return payload

    # -----------------------------------------------------------------
    # Informes (SPEC-03 §4)
    # -----------------------------------------------------------------
    def get_report(self, report_id: str) -> Optional[dict[str, Any]]:
        """``GET /reports/{id}`` — metadatos del informe (VIEWER+).

        Devuelve ``None`` (→ 404) si el informe no existe.
        """
        result = self._invoke("get_report", report_id)
        return None if result is None else self._as_dict(result, "get_report")

    def get_report_file_path(self, report_id: str) -> str:
        """``GET /reports/{id}/file`` — ruta del PDF validada dentro del storage.

        La base de datos **nunca** sirve el archivo: el backend resuelve la ruta
        y la valida contra ``STORAGE_RAW_DIR`` antes de devolverlo (SPEC-04 §4).

        Devuelve ``""`` si el informe no existe o no tiene archivo asociado; la
        ruta lo traduce a **404** (no es un servicio caído, es un recurso ausente).
        """
        path = self._invoke("get_report_file_path", report_id)
        return str(path) if path else ""

    def get_report_chunks(self, report_id: str) -> dict[str, Any]:
        """``GET /reports/{id}/chunks`` — chunks con coordenadas (VIEWER+)."""
        payload = self._as_dict(
            self._invoke("get_report_chunks", report_id), "get_report_chunks"
        )
        payload.setdefault("items", [])
        return payload

    # -----------------------------------------------------------------
    # Inmutabilidad analítica (SPEC-04 §1 · SPEC-03 §4)
    # -----------------------------------------------------------------
    def lock_node(
        self, node_id: str, locked: bool = True
    ) -> Optional[dict[str, Any]]:
        """``POST /graph/nodes/{id}/lock`` — bloquea/desbloquea (ANALYST+).

        Devuelve ``None`` (→ 404) si el nodo no existe.
        """
        result = self._invoke("set_node_locked", node_id, locked)
        return None if result is None else self._as_dict(result, "set_node_locked")

    # -----------------------------------------------------------------
    # Expedientes analíticos (SPEC-03 §6)
    # -----------------------------------------------------------------
    def create_analysis(
        self,
        *,
        title: str,
        thesis_summary: str,
        code: Optional[str] = None,
        author_analyst_id: Optional[str] = None,
        conclusion_body: Optional[str] = None,
    ) -> dict[str, Any]:
        """``POST /analyses`` — crea un expediente analítico (ANALYST+)."""
        return self._as_dict(
            self._invoke(
                "create_analysis",
                title,
                thesis_summary,
                code=code,
                author_analyst_id=author_analyst_id,
                conclusion_body=conclusion_body,
            ),
            "create_analysis",
        )

    def get_analysis(self, analysis_id: str) -> Optional[dict[str, Any]]:
        """``GET /analyses/{id}`` — ficha del expediente (VIEWER+)."""
        result = self._invoke("get_analysis", analysis_id)
        return None if result is None else self._as_dict(result, "get_analysis")

    def list_analyses(self, status: Optional[str] = None) -> dict[str, Any]:
        """``GET /analyses`` — lista expedientes, filtrable por estado (VIEWER+)."""
        payload = self._as_dict(
            self._invoke("list_analyses", status=status), "list_analyses"
        )
        payload.setdefault("items", [])
        return payload

    def update_analysis(
        self, analysis_id: str, patch: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """``PUT /analyses/{id}`` — edita tesis/conclusión/estado (ANALYST+)."""
        result = self._invoke("update_analysis", analysis_id, patch)
        return None if result is None else self._as_dict(result, "update_analysis")

    def create_requirement(
        self,
        *,
        question: str,
        priority: str,
        parent_analysis_id: Optional[str] = None,
        target_entity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """``POST /analyses/{id}/requirements`` — crea un RFI (ANALYST+)."""
        return self._as_dict(
            self._invoke(
                "create_requirement",
                question,
                priority=priority,
                parent_analysis_id=parent_analysis_id,
                target_entity_id=target_entity_id,
            ),
            "create_requirement",
        )

    def list_requirements(self, analysis_id: str) -> dict[str, Any]:
        """``GET /analyses/{id}/requirements`` — RFI del expediente (VIEWER+)."""
        payload = self._as_dict(
            self._invoke("list_requirements", analysis_id), "list_requirements"
        )
        payload.setdefault("items", [])
        return payload

    # -----------------------------------------------------------------
    # Coherencia (SPEC-03 §6)
    # -----------------------------------------------------------------
    def list_alerts(self, status: Optional[str] = None) -> dict[str, Any]:
        """``GET /coherence/alerts`` — bandeja de coherencia (ANALYST+)."""
        payload = self._as_dict(self._invoke("list_alerts", status=status), "list_alerts")
        payload.setdefault("items", [])
        return payload

    def resolve_alert(
        self, alert_id: str, status: str, note: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """``PATCH /coherence/alerts/{alert_id}/resolve`` (ANALYST+)."""
        result = self._invoke("resolve_alert", alert_id, status, note)
        return None if result is None else self._as_dict(result, "resolve_alert")


__all__ = ["GraphGateway", "GraphAccessPort", "REQUESTED_CAPABILITIES"]
