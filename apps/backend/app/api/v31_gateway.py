"""Gateway de las consultas analíticas v3.1 (SPEC-03/SPEC-02 §11.5).

Zona: Agente 3 — ``apps/backend/app/api/**``.

Las rutas HTTP no construyen SQL: delegan en este puerto. La implementación de
producción es ``app.db.postgres_v31_repository.PostgresV31Repository``; los
tests inyectan un doble con ``app.dependency_overrides[get_v31_gateway]``.
"""

from __future__ import annotations

from typing import Any, Optional, Protocol, runtime_checkable

from app.core.errors import ServiceUnavailable


@runtime_checkable
class V31AccessPort(Protocol):
    """Operaciones que las rutas v3.1 necesitan del repositorio PostgreSQL."""

    def find_colocation(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_subgraph(self, **kwargs: Any) -> dict[str, Any]: ...

    def detect_alias_evasion(self, **kwargs: Any) -> dict[str, Any]: ...

    def list_proposals(self, **kwargs: Any) -> dict[str, Any]: ...

    def get_proposal(self, proposal_id: str, **kwargs: Any) -> Optional[dict[str, Any]]: ...

    def approve_proposal(
        self, proposal_id: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]: ...

    def reject_proposal(
        self, proposal_id: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]: ...

    def get_evidence(self, evidence_id: str, **kwargs: Any) -> Optional[dict[str, Any]]: ...

    def verify_evidence(self, evidence_id: str, **kwargs: Any) -> Optional[dict[str, Any]]: ...


class V31Gateway:
    """Traduce peticiones HTTP a capacidades del repositorio v3.1."""

    def __init__(self, repository: Any) -> None:
        self._repository = repository

    def _invoke(self, capability: str, *args: Any, **kwargs: Any) -> Any:
        method = getattr(self._repository, capability, None)
        if method is None:
            raise ServiceUnavailable(
                "Consultas v3.1 no disponibles: el repositorio no expone "
                f"'{capability}' todavía."
            )
        try:
            return method(*args, **kwargs)
        except ServiceUnavailable:
            raise
        except Exception as exc:
            name = type(exc).__name__
            if name in {
                "ProposalVersionConflict",
                "EvidenceConflict",
                "EvidenceHashMismatch",
            }:
                raise
            raise ServiceUnavailable(
                f"Consultas v3.1 no disponibles ({capability}): {exc}"
            ) from exc

    def find_colocation(self, **kwargs: Any) -> dict[str, Any]:
        return dict(self._invoke("find_colocation", **kwargs))

    def get_subgraph(self, **kwargs: Any) -> dict[str, Any]:
        return dict(self._invoke("get_subgraph", **kwargs))

    def detect_alias_evasion(self, **kwargs: Any) -> dict[str, Any]:
        return dict(self._invoke("detect_alias_evasion", **kwargs))

    def list_proposals(self, **kwargs: Any) -> dict[str, Any]:
        return dict(self._invoke("list_proposals", **kwargs))

    def get_proposal(
        self, proposal_id: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        result = self._invoke("get_proposal", proposal_id, **kwargs)
        return None if result is None else dict(result)

    def approve_proposal(
        self, proposal_id: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        result = self._invoke("approve_proposal", proposal_id, **kwargs)
        return None if result is None else dict(result)

    def reject_proposal(
        self, proposal_id: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        result = self._invoke("reject_proposal", proposal_id, **kwargs)
        return None if result is None else dict(result)

    def get_evidence(
        self, evidence_id: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        result = self._invoke("get_evidence", evidence_id, **kwargs)
        return None if result is None else dict(result)

    def verify_evidence(
        self, evidence_id: str, **kwargs: Any
    ) -> Optional[dict[str, Any]]:
        result = self._invoke("verify_evidence", evidence_id, **kwargs)
        return None if result is None else dict(result)


__all__ = ["V31AccessPort", "V31Gateway"]
