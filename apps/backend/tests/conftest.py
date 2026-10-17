"""Fixtures compartidas — Verificación Cruzada (Constitución, Regla 5).

Autorizado por: SPEC-01 §4 · §7.
Todos los tests corren **sin base de datos real** mediante ``FakeRepository``.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

import pytest

# Garantiza que `app` sea importable aunque pytest se invoque desde otro cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.entity_resolver import (  # noqa: E402
    MERGE,
    NEW,
    PENDING_HUMAN_REVIEW,
    EntityResolver,
)


class FakeRepository:
    """Repositorio en memoria que implementa la interfaz que usa EntityResolver.

    Réplica funcional del subconjunto de ``GraphRepository`` necesario para
    SPEC-01 §4, sin Neo4j.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.merge_calls: list[tuple[str, str]] = []
        self.risk_writes: list[tuple[str, float]] = []
        self.alerts: list[dict[str, Any]] = []
        self.alert_calls: list[dict[str, Any]] = []

    # -- helpers de test ----------------------------------------------
    @staticmethod
    def _label_matches(node: dict[str, Any], label: Optional[str]) -> bool:
        return label is None or node.get("label") == label

    def _public(self, node: dict[str, Any]) -> dict[str, Any]:
        data = {
            "id": node["id"],
            "label": node["label"],
            "aliases": list(node["aliases"]),
        }
        data.update(node["props"])
        return data

    def seed_node(
        self,
        label: str,
        node_id: str,
        name: str,
        aliases: Optional[list[str]] = None,
        **props: Any,
    ) -> dict[str, Any]:
        node = {
            "id": node_id,
            "label": label,
            "aliases": list(aliases or []),
            "props": dict(props),
        }
        if label in ("Person", "Organization"):
            node["props"].setdefault("canonical_name", name)
        else:
            node["props"].setdefault("name", name)
        self.nodes[node_id] = node
        return self._public(node)

    # -- interfaz de GraphRepository ----------------------------------
    def upsert_entity(
        self,
        label: str,
        id: str,
        props: Optional[dict[str, Any]] = None,
        alias: Optional[str] = None,
    ) -> dict[str, Any]:
        node = self.nodes.get(id)
        if node is None:
            node = {"id": id, "label": label, "aliases": [], "props": {}}
            self.nodes[id] = node
        node["label"] = label
        for key, value in (props or {}).items():
            if key in ("aliases", "id"):
                continue
            node["props"][key] = value
        if alias is not None and alias not in node["aliases"]:
            node["aliases"].append(alias)
        return self._public(node)

    def find_by_unique_key(
        self, label: str, key: str, value: Any
    ) -> Optional[dict[str, Any]]:
        for node in self.nodes.values():
            if node["label"] != label:
                continue
            if key == "id" and node["id"] == value:
                return self._public(node)
            if node["props"].get(key) == value:
                return self._public(node)
        return None

    def fulltext_search(
        self, name: str, label: Optional[str] = None, limit: int = 25
    ) -> list[dict[str, Any]]:
        results = [
            self._public(node)
            for node in self.nodes.values()
            if self._label_matches(node, label)
        ]
        return results[:limit]

    def upsert_relationship(
        self, from_id: str, to_id: str, rel_type: str, props: dict[str, Any]
    ) -> dict[str, Any]:
        for edge in self.edges:
            if (
                edge["from_id"] == from_id
                and edge["to_id"] == to_id
                and edge["rel_type"] == rel_type
            ):
                edge["props"].update(props)
                return edge
        edge = {
            "from_id": from_id,
            "to_id": to_id,
            "rel_type": rel_type,
            "props": dict(props),
        }
        self.edges.append(edge)
        return edge

    def merge_duplicates(self, canonical_id: str, duplicate_id: str) -> None:
        self.merge_calls.append((canonical_id, duplicate_id))
        for edge in self.edges:
            if edge["from_id"] == duplicate_id:
                edge["from_id"] = canonical_id
            if edge["to_id"] == duplicate_id:
                edge["to_id"] = canonical_id
        # No se copian propiedades ni alias del duplicado (fiel a la Regla 2:
        # un nodo bloqueado nunca se sobrescribe).
        self.nodes.pop(duplicate_id, None)

    def create_alert(
        self,
        alert_type: str,
        description: str,
        *,
        status: str = "PENDING_ANALYST",
        analysis_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        alert_id: Optional[str] = None,
    ) -> dict[str, Any]:
        call = {
            "id": alert_id or f"alert-{len(self.alerts) + 1}",
            "alert_type": alert_type,
            "status": status,
            "description": description,
            "analysis_id": analysis_id,
            "entity_id": entity_id,
        }
        self.alert_calls.append(dict(call))
        self.alerts.append(call)
        return call

    def list_relationships(self, entity_id: str) -> list[dict[str, Any]]:
        return [
            dict(edge)
            for edge in self.edges
            if edge["from_id"] == entity_id or edge["to_id"] == entity_id
        ]

    def set_risk_score(self, entity_id: str, risk_score: float) -> Optional[float]:
        self.risk_writes.append((entity_id, risk_score))
        node = self.nodes.get(entity_id)
        if node is not None:
            node["props"]["risk_score"] = risk_score
            return risk_score
        return None


@pytest.fixture
def repo() -> FakeRepository:
    """Repositorio en memoria vacío."""
    return FakeRepository()


@pytest.fixture
def resolver(repo: FakeRepository) -> EntityResolver:
    """EntityResolver inyectado con el FakeRepository."""
    return EntityResolver(repo)


# Re-export de acciones para comodidad de los tests.
__all__ = [
    "FakeRepository",
    "repo",
    "resolver",
    "MERGE",
    "NEW",
    "PENDING_HUMAN_REVIEW",
]
