"""Repositorio PostgreSQL v3 (Hito 1).

Zona: Agente 1 — ``apps/backend/app/db/**``.

Implementa el subconjunto de ``GraphRepository`` necesario para la doble capa:
ledger append-only, grafo validado y consultas de lectura. Las escrituras pasan
siempre por funciones ``SECURITY DEFINER`` del contrato
``contracts/v3/db-contract.sql``; este módulo nunca ejecuta DML directo sobre
tablas base.

El rol de conexión determina la identidad de agente (``session_user``). Por eso
un pool de Hermes no puede escribir como Atenea: el contrato lo rechaza con
``42501``.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable, Iterator, Optional
from uuid import UUID

from app.db.postgres_driver import (
    DEFAULT_CLEARANCE,
    PSYCOPG_AVAILABLE,
    close,
    create_pool,
    transaction,
)


def _jsonb(value: Any) -> Any:
    """Adapta un dict/list a ``jsonb`` sin importar psycopg en modo offline."""
    if value is None:
        return None
    if not PSYCOPG_AVAILABLE:
        return value
    try:
        from psycopg.types.json import Jsonb  # type: ignore

        if isinstance(value, Jsonb):
            return value
        return Jsonb(value)
    except Exception:  # pragma: no cover - psycopg siempre disponible en runtime
        return value


def _uuid(value: Any) -> Optional[UUID]:
    if value is None or isinstance(value, UUID):
        return value
    return UUID(str(value))


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_iso(v) for v in value]
    if isinstance(value, dict):
        return {k: _iso(v) for k, v in value.items()}
    return value


def _public(row: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if row is None:
        return None
    data = _iso(dict(row))
    if "properties" in data and isinstance(data["properties"], dict):
        # Compatibilidad con GraphRepository: las props se exponen en la raíz.
        props = data.pop("properties") or {}
        for key, value in props.items():
            data.setdefault(key, value)
    return data


class PostgresRepository:
    """Acceso a PostgreSQL 16 + pgvector para el Hito 1.

    :param pool: pool ya creado (``psycopg_pool.ConnectionPool``).
    :param dsn: alternativa a ``pool``; crea un pool propio.
    :param clearance: clasificación máxima que la sesión puede leer.
    """

    def __init__(
        self,
        pool: Any = None,
        *,
        dsn: Optional[str] = None,
        clearance: str = DEFAULT_CLEARANCE,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self._owns_pool = pool is None
        self.pool = pool or create_pool(
            dsn, min_size=min_size, max_size=max_size
        )
        self.clearance = clearance

    # -- ciclo de vida -------------------------------------------------
    def close(self) -> None:
        if self._owns_pool:
            close(self.pool)

    def __enter__(self) -> "PostgresRepository":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    @contextmanager
    def _tx(
        self,
        *,
        clearance: Optional[str] = None,
        break_glass: bool = False,
    ) -> Iterator[Any]:
        with transaction(
            self.pool,
            clearance=clearance or self.clearance,
            break_glass=break_glass,
        ) as conn:
            yield conn

    # -- salud ---------------------------------------------------------
    def health(self) -> bool:
        with self._tx() as conn:
            conn.execute("SELECT 1")
        return True

    # -- capa 1: ledger ------------------------------------------------
    def append_evidence(
        self,
        *,
        idempotency_key: str,
        mission_pir_id: Any,
        source_platform: str,
        source_uri: str,
        raw_payload: dict[str, Any],
        raw_payload_bytes: bytes,
        payload_sha256: str,
        captured_at_utc: datetime,
        tlp_classification: str,
        collection_method: str,
        legal_basis: str,
        confidence_hint: Optional[float] = None,
        attachments: Optional[list[dict[str, Any]]] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> str:
        """Inserta evidencia y devuelve ``evidence_id`` (UUIDv7).

        El hash se valida en DB contra los bytes exactos; el agente se deriva
        de ``session_user`` y debe tener tasking vigente.
        """
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT staging.append_evidence(
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) AS evidence_id
                """,
                (
                    idempotency_key,
                    _uuid(mission_pir_id),
                    source_platform,
                    source_uri,
                    _jsonb(raw_payload),
                    raw_payload_bytes,
                    payload_sha256,
                    captured_at_utc,
                    tlp_classification,
                    collection_method,
                    legal_basis,
                    confidence_hint,
                    _jsonb(attachments or []),
                    _jsonb(metadata or {}),
                ),
            ).fetchone()
        return str(row["evidence_id"])

    def verify_evidence(self, evidence_id: Any) -> bool:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT staging.verify_evidence(%s::uuid) AS ok",
                (_uuid(evidence_id),),
            ).fetchone()
        return bool(row and row["ok"])

    def list_evidence(
        self,
        *,
        mission_pir_id: Any = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM api.v_evidence_chain
                WHERE (%s::uuid IS NULL OR mission_pir_id = %s::uuid)
                ORDER BY captured_at_utc DESC
                LIMIT %s
                """,
                (_uuid(mission_pir_id), _uuid(mission_pir_id), limit),
            ).fetchall()
        return [_iso(dict(r)) for r in rows]

    def get_evidence(self, evidence_id: Any) -> Optional[dict[str, Any]]:
        """Devuelve la fila cruda del ledger y sus adjuntos.

        Solo debe usarse desde un pool con privilegios de lectura del ledger
        (API/ATENEA); la API aplica RBAC y TLP antes de serializar.
        """
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT l.*,
                       (SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.created_at), '[]'::jsonb)
                        FROM staging.raw_intel_attachments a
                        WHERE a.evidence_id = l.evidence_id) AS attachments
                FROM staging.raw_intel_ledger l
                WHERE l.evidence_id = %s::uuid
                """,
                (_uuid(evidence_id),),
            ).fetchone()
        return _iso(dict(row)) if row else None

    # -- capa 2: grafo validado ---------------------------------------
    def upsert_entity(
        self,
        *,
        stix_type: str,
        name: str,
        admiralty_rating: str,
        tlp_classification: str,
        evidence_ids: Iterable[Any],
        aliases: Optional[Iterable[str]] = None,
        properties: Optional[dict[str, Any]] = None,
        valid_from: Optional[datetime] = None,
        valid_until: Optional[datetime] = None,
        confidence_override: Optional[int] = None,
        stix_id: Optional[str] = None,
    ) -> str:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT intel.upsert_entity(
                    %s, %s, %s, %s, %s::uuid[], %s::text[], %s,
                    %s, %s, %s, %s
                ) AS entity_id
                """,
                (
                    stix_type,
                    name,
                    admiralty_rating,
                    tlp_classification,
                    [_uuid(e) for e in evidence_ids],
                    list(aliases) if aliases is not None else None,
                    _jsonb(properties or {}),
                    valid_from,
                    valid_until,
                    confidence_override,
                    stix_id,
                ),
            ).fetchone()
        return str(row["entity_id"])

    def upsert_relationship(
        self,
        *,
        relationship_type: str,
        source_entity_id: Any,
        target_entity_id: Any,
        admiralty_rating: str,
        evidence_ids: Iterable[Any],
        properties: Optional[dict[str, Any]] = None,
        valid_from: Optional[datetime] = None,
        valid_until: Optional[datetime] = None,
        confidence_override: Optional[int] = None,
        stix_id: Optional[str] = None,
    ) -> str:
        with self._tx() as conn:
            row = conn.execute(
                """
                SELECT intel.upsert_relationship(
                    %s, %s, %s, %s, %s::uuid[], %s, %s, %s, %s, %s
                ) AS relationship_id
                """,
                (
                    relationship_type,
                    _uuid(source_entity_id),
                    _uuid(target_entity_id),
                    admiralty_rating,
                    [_uuid(e) for e in evidence_ids],
                    _jsonb(properties or {}),
                    valid_from,
                    valid_until,
                    confidence_override,
                    stix_id,
                ),
            ).fetchone()
        return str(row["relationship_id"])

    def recalculate_confidence(
        self,
        *,
        entity_id: Any = None,
        relationship_id: Any = None,
    ) -> int:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT intel.recalculate_confidence(%s::uuid, %s::uuid) AS confidence",
                (_uuid(entity_id), _uuid(relationship_id)),
            ).fetchone()
        return int(row["confidence"])

    # -- lecturas compatibles con GraphRepository ----------------------
    def list_nodes(
        self,
        label: Optional[str] = None,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM api.v_graph_nodes
                WHERE (%s::text IS NULL OR label = %s::text)
                ORDER BY name
                LIMIT %s OFFSET %s
                """,
                (label, label, limit, offset),
            ).fetchall()
        return [_public(dict(r)) for r in rows]

    def get_node(self, node_id: Any) -> Optional[dict[str, Any]]:
        with self._tx() as conn:
            row = conn.execute(
                "SELECT * FROM api.v_graph_nodes WHERE id = %s::uuid",
                (_uuid(node_id),),
            ).fetchone()
        return _public(dict(row)) if row else None

    def list_edges(
        self,
        *,
        entity_id: Any = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM api.v_graph_edges
                WHERE (%s::uuid IS NULL OR source = %s::uuid OR target = %s::uuid)
                ORDER BY id
                LIMIT %s
                """,
                (_uuid(entity_id), _uuid(entity_id), _uuid(entity_id), limit),
            ).fetchall()
        return [_iso(dict(r)) for r in rows]

    def neighborhood(self, node_id: Any, depth: int = 1) -> dict[str, Any]:
        """BFS acotado sobre las vistas de lectura."""
        root = self.get_node(node_id)
        if root is None:
            return {"nodes": [], "edges": []}
        seen = {str(node_id)}
        nodes: dict[str, dict[str, Any]] = {str(node_id): root}
        edges: dict[str, dict[str, Any]] = {}
        frontier = {str(node_id)}
        for _ in range(max(1, depth)):
            next_frontier: set[str] = set()
            for current in list(frontier):
                for edge in self.list_edges(entity_id=current, limit=500):
                    edges[str(edge["id"])] = edge
                    other = edge["target"] if edge["source"] == current else edge["source"]
                    if other not in seen:
                        seen.add(other)
                        node = self.get_node(other)
                        if node is not None:
                            nodes[other] = node
                            next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break
        return {"nodes": list(nodes.values()), "edges": list(edges.values())}

    def search_nodes(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        with self._tx() as conn:
            rows = conn.execute(
                """
                SELECT * FROM api.v_graph_nodes
                WHERE name ILIKE %s
                   OR EXISTS (
                       SELECT 1 FROM unnest(aliases) AS a
                       WHERE a ILIKE %s
                   )
                ORDER BY confidence DESC, name
                LIMIT %s
                """,
                (pattern, pattern, limit),
            ).fetchall()
        return [_public(dict(r)) for r in rows]

    def orphan_check(self) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM api.v_orphan_check").fetchall()
        return [dict(r) for r in rows]

    def mission_coverage(self) -> list[dict[str, Any]]:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM api.v_mission_coverage ORDER BY code"
            ).fetchall()
        return [_iso(dict(r)) for r in rows]


__all__ = ["PostgresRepository"]
