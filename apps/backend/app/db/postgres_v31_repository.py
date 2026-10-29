"""Repositorio PostgreSQL/PostGIS v3.1 (Agente 1).

Zona: Agente 1 — ``apps/backend/app/db/**``.

Implementa las consultas de SPEC-03 y la bandeja de propuestas de SPEC-02 §11.5
sobre el contrato ``contracts/v3/db-contract-v3.1.sql``. No expone la base: el
pool se inyecta desde ``app.api.deps`` y RLS se activa en cada transacción.

Las consultas son de **sólo lectura** salvo ``approve_proposal`` y
``reject_proposal``, que actualizan la propuesta (nunca el grafo). La aplicación
de una propuesta aprobada corresponde a ATENEA y no se implementa aquí.

Este módulo es importable sin ``psycopg`` instalado; los métodos fallan con un
error explícito si el pool no es válido.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from app.db.postgres_driver import transaction
from app.services.correlation import (
    AliasObservation,
    ColocationEvent,
    build_alias_signals,
    build_pairs,
    cluster_pairs,
    mask_events,
    mask_selector,
)


class ProposalVersionConflict(RuntimeError):
    """La propuesta cambió desde que el analista la abrió (409)."""


class EvidenceConflict(RuntimeError):
    """La evidencia no es verificable o la cadena está rota."""


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _string_list(values: Optional[Iterable[Any]]) -> list[str]:
    return [str(v) for v in (values or [])]


def _row_to_event(row: dict[str, Any]) -> ColocationEvent:
    selector = row.get("normalized_value")
    kind = row.get("observable_type")
    return ColocationEvent(
        event_id=str(row["event_id"]),
        discipline=str(row["discipline"]),
        event_type=str(row["event_type"]),
        event_time=row["event_time"],
        lat=float(row["lat"]),
        lon=float(row["lon"]),
        confidence=int(row["confidence"]),
        tlp=str(row["tlp_classification"]),
        entity_id=str(row["entity_id"]) if row.get("entity_id") else None,
        observable_id=str(row["observable_id"]) if row.get("observable_id") else None,
        masked_selector=(
            mask_selector(str(kind), str(selector)) if selector and kind else None
        ),
        evidence_ids=(str(row["source_evidence_id"]),)
        if row.get("source_evidence_id")
        else (),
    )


class PostgresV31Repository:
    """Consultas v3.1 sobre un pool ``psycopg_pool.ConnectionPool``."""

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _rows(self, sql: str, params: Iterable[Any], *, clearance: str) -> list[dict[str, Any]]:
        with transaction(self._pool, clearance=clearance) as conn:
            return list(conn.execute(sql, tuple(params)).fetchall())

    def _one(
        self, sql: str, params: Iterable[Any], *, clearance: str
    ) -> Optional[dict[str, Any]]:
        rows = self._rows(sql, params, clearance=clearance)
        return rows[0] if rows else None

    # ------------------------------------------------------------------
    # Consulta 1 — colocalización
    # ------------------------------------------------------------------
    def find_colocation(
        self,
        *,
        mission_id: str,
        center: dict[str, float],
        radius_m: float,
        window: dict[str, str],
        disciplines: list[str],
        min_confidence: int,
        max_tlp: str,
        max_time_delta_s: int,
        limit: int,
        clearance: str,
        viewer_role: str = "VIEWER",
    ) -> dict[str, Any]:
        allowed = [d for d in disciplines if d in {"SIGINT", "SOCMINT", "OSINT"}]
        if "SIGINT" not in allowed:
            allowed.append("SIGINT")
        sql = """
            WITH center AS (
                SELECT ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography AS g
            )
            SELECT e.event_id::text, e.event_type::text, e.discipline::text,
                   e.event_time, e.confidence, e.tlp_classification::text,
                   e.entity_id::text, e.observable_id::text,
                   e.source_evidence_id::text,
                   ST_Y(e.location::geometry) AS lat,
                   ST_X(e.location::geometry) AS lon,
                   o.observable_type::text, o.normalized_value
            FROM intel.intelligence_events e
            CROSS JOIN center c
            LEFT JOIN intel.observables o ON o.observable_id = e.observable_id
            WHERE e.mission_id = %s
              AND e.discipline::text = ANY(%s)
              AND e.revoked = false
              AND e.event_time >= %s AND e.event_time < %s
              AND e.confidence >= %s
              AND e.location IS NOT NULL
              AND intel.tlp_rank(e.tlp_classification::text) <= intel.tlp_rank(%s)
              AND ST_DWithin(e.location, c.g, %s)
            ORDER BY e.event_time ASC
            LIMIT %s
        """
        rows = self._rows(
            sql,
            (
                center["lon"],
                center["lat"],
                mission_id,
                allowed,
                window["from"],
                window["to"],
                min_confidence,
                max_tlp,
                radius_m,
                min(5000, max(limit * 5, limit)),
            ),
            clearance=clearance,
        )
        events = [_row_to_event(row) for row in rows]
        pairs = build_pairs(
            events, radius_m=radius_m, max_delta_s=max_time_delta_s
        )
        clusters = cluster_pairs(
            pairs, radius_m=radius_m, max_delta_s=max_time_delta_s
        )
        clusters = clusters[:limit]
        cluster_payload = []
        for cluster in clusters:
            cluster_payload.append(
                {
                    "cluster_id": cluster.cluster_id,
                    "center": {"lat": cluster.center[0], "lon": cluster.center[1]},
                    "radius_m": cluster.radius_m,
                    "score": cluster.score,
                    "disciplines": sorted(
                        {e.discipline for e in cluster.events}
                    ),
                    "events": mask_events(cluster.events),
                    "time_delta_s": cluster.time_delta_s,
                    "evidence_ids": list(cluster.evidence_ids),
                }
            )
        return {
            "query": {
                "mission_id": mission_id,
                "center": center,
                "radius_m": radius_m,
                "window": window,
            },
            "clusters": cluster_payload,
            "truncated": len(pairs) > len(clusters),
            "next_cursor": None,
            "suppressed": 0,
        }

    # ------------------------------------------------------------------
    # Consulta 2 — subgrafo multicapa
    # ------------------------------------------------------------------
    def get_subgraph(
        self,
        *,
        mission_id: str,
        root_ids: list[str],
        depth: int,
        direction: str,
        disciplines: list[str],
        admiralty_min: Optional[str],
        admiralty_max: Optional[str],
        min_confidence: int,
        max_tlp: str,
        as_of: str,
        include_staged: bool,
        max_nodes: int,
        max_edges: int,
        cursor: Optional[str],
        clearance: str,
    ) -> dict[str, Any]:
        node_sql = """
            WITH RECURSIVE params AS (
                SELECT %s::uuid[] AS roots, %s::int AS max_depth,
                       %s::text AS direction, %s::text[] AS disciplines,
                       %s::smallint AS min_confidence, %s::text AS clearance,
                       %s::timestamptz AS as_of
            ),
            walk AS (
                SELECT r.entity_id, 0 AS depth, ARRAY[r.entity_id] AS path
                FROM unnest((SELECT roots FROM params)) AS r(entity_id)
                UNION ALL
                SELECT CASE WHEN e.source_entity_id = w.entity_id
                            THEN e.target_entity_id ELSE e.source_entity_id END,
                       w.depth + 1,
                       w.path || CASE WHEN e.source_entity_id = w.entity_id
                                      THEN e.target_entity_id ELSE e.source_entity_id END
                FROM walk w
                JOIN intel.intelligence_relationships e
                  ON ((e.source_entity_id = w.entity_id
                       AND (SELECT direction FROM params) IN ('OUT','BOTH'))
                   OR (e.target_entity_id = w.entity_id
                       AND (SELECT direction FROM params) IN ('IN','BOTH')))
                WHERE w.depth < (SELECT max_depth FROM params)
                  AND e.revoked = false
                  AND e.validation_state = 'ANALYST_VERIFIED'
                  AND e.valid_from <= (SELECT as_of FROM params)
                  AND (e.valid_until IS NULL OR e.valid_until > (SELECT as_of FROM params))
                  AND e.confidence_score >= (SELECT min_confidence FROM params)
                  AND (cardinality((SELECT disciplines FROM params)) = 0
                       OR e.discipline::text = ANY((SELECT disciplines FROM params)::text[]))
                  AND intel.tlp_rank(e.tlp_classification::text)
                      <= intel.tlp_rank((SELECT clearance FROM params))
                  AND NOT (CASE WHEN e.source_entity_id = w.entity_id
                                THEN e.target_entity_id ELSE e.source_entity_id END
                           = ANY(w.path))
            ),
            reached AS (
                SELECT entity_id, min(depth) AS depth
                FROM walk
                GROUP BY entity_id
                ORDER BY depth ASC
                LIMIT %s
            )
            SELECT e.entity_id::text, e.stix_id, e.stix_type, e.canonical_name,
                   e.primary_discipline::text, e.confidence,
                   e.tlp_classification::text, e.valid_from, e.valid_until,
                   e.admiralty_rating::text, r.depth
            FROM reached r
            JOIN intel.core_entities e ON e.entity_id = r.entity_id
            WHERE e.revoked = false
              AND e.valid_from <= %s::timestamptz
              AND (e.valid_until IS NULL OR e.valid_until > %s::timestamptz)
              AND intel.tlp_rank(e.tlp_classification::text) <= intel.tlp_rank(%s)
            ORDER BY r.depth ASC, e.canonical_name ASC
        """
        node_params = [
            root_ids,
            depth,
            direction,
            disciplines,
            min_confidence,
            clearance,
            as_of,
            max_nodes,
            as_of,
            as_of,
            max_tlp,
        ]
        nodes = self._rows(
            node_sql, node_params, clearance=clearance
        )
        node_ids = [row["entity_id"] for row in nodes]
        edges: list[dict[str, Any]] = []
        if node_ids:
            edge_sql = """
                SELECT e.relationship_id::text, e.source_entity_id::text,
                       e.target_entity_id::text, e.relationship_type::text,
                       e.discipline::text, e.confidence_score,
                       e.tlp_classification::text, e.valid_from, e.valid_until,
                       e.event_id::text, e.admiralty_rating::text
                FROM intel.intelligence_relationships e
                WHERE e.source_entity_id = ANY(%s::uuid[])
                  AND e.target_entity_id = ANY(%s::uuid[])
                  AND e.revoked = false
                  AND e.validation_state = 'ANALYST_VERIFIED'
                  AND e.valid_from <= %s::timestamptz
                  AND (e.valid_until IS NULL OR e.valid_until > %s::timestamptz)
                  AND e.confidence_score >= %s
                  AND (cardinality(%s::text[]) = 0
                       OR e.discipline::text = ANY(%s::text[]))
                  AND intel.tlp_rank(e.tlp_classification::text) <= intel.tlp_rank(%s)
                ORDER BY e.confidence_score DESC
                LIMIT %s
            """
            edge_rows = self._rows(
                edge_sql,
                (
                    node_ids,
                    node_ids,
                    as_of,
                    as_of,
                    min_confidence,
                    disciplines,
                    disciplines,
                    max_tlp,
                    max_edges,
                ),
                clearance=clearance,
            )
            edges = [
                {
                    "relationship_id": row["relationship_id"],
                    "source_entity_id": row["source_entity_id"],
                    "target_entity_id": row["target_entity_id"],
                    "relationship_type": row["relationship_type"],
                    "discipline": row["discipline"],
                    "confidence": int(row["confidence_score"]),
                    "admiralty": row["admiralty_rating"],
                    "tlp": row["tlp_classification"],
                    "valid_from": _iso(row["valid_from"]),
                    "valid_until": _iso(row["valid_until"]),
                    "event_id": row["event_id"],
                    "evidence_ids": [],
                }
                for row in edge_rows
            ]

        node_payload = [
            {
                "entity_id": row["entity_id"],
                "stix_id": row["stix_id"],
                "stix_type": row["stix_type"],
                "canonical_name": row["canonical_name"],
                "primary_discipline": row["primary_discipline"],
                "admiralty": row["admiralty_rating"],
                "confidence": int(row["confidence"]),
                "tlp": row["tlp_classification"],
                "valid_from": _iso(row["valid_from"]),
                "valid_until": _iso(row["valid_until"]),
                "depth": int(row["depth"]),
                "observables": [],
                "evidence_ids": [],
            }
            for row in nodes
        ]
        return {
            "nodes": node_payload,
            "edges": edges,
            "truncated": len(nodes) >= max_nodes,
            "next_cursor": None,
            "as_of": as_of,
        }

    # ------------------------------------------------------------------
    # Consulta 3 — evasión de alias
    # ------------------------------------------------------------------
    def detect_alias_evasion(
        self,
        *,
        mission_id: str,
        entity_id: Optional[str],
        window: dict[str, str],
        min_aliases: int,
        min_platforms: int,
        max_gap_hours: float,
        similarity_threshold: float,
        max_tlp: str,
        limit: int,
        clearance: str,
    ) -> dict[str, Any]:
        sql = """
            SELECT o.entity_id::text, o.observable_id::text,
                   a.alias_type::text, a.normalized_value, a.platform,
                   a.observed_from, a.observed_until,
                   a.source_evidence_id::text, a.confidence
            FROM intel.observable_alias_history a
            JOIN intel.observables o ON o.observable_id = a.observable_id
            JOIN staging.raw_evidence_vault v
              ON v.evidence_id = a.source_evidence_id
            WHERE v.mission_id = %s::uuid
              AND a.revoked = false
              AND a.observed_from >= %s AND a.observed_from < %s
              AND (%s::uuid IS NULL OR o.entity_id = %s::uuid)
              AND intel.tlp_rank(a.tlp_classification::text) <= intel.tlp_rank(%s)
            ORDER BY o.entity_id, a.observed_from
            LIMIT 10000
        """
        rows = self._rows(
            sql,
            (
                mission_id,
                window["from"],
                window["to"],
                entity_id,
                entity_id,
                max_tlp,
            ),
            clearance=clearance,
        )
        observations = [
            AliasObservation(
                entity_id=row["entity_id"],
                observable_id=row["observable_id"],
                alias_type=row["alias_type"],
                normalized_value=row["normalized_value"],
                platform=row["platform"],
                observed_from=row["observed_from"],
                observed_until=row["observed_until"],
                source_evidence_id=row["source_evidence_id"],
                confidence=int(row["confidence"]),
            )
            for row in rows
        ]
        signals = build_alias_signals(
            observations,
            min_aliases=min_aliases,
            min_platforms=min_platforms,
            max_gap_hours=max_gap_hours,
            limit=limit,
        )
        payload = []
        for signal in signals:
            digest = hashlib.sha256(
                (
                    signal.signal_type
                    + (signal.entity_id or "")
                    + (signal.observable_id or "")
                    + "|".join(signal.aliases)
                ).encode("utf-8")
            ).hexdigest()[:16]
            payload.append(
                {
                    "signal_id": f"alias-{digest}",
                    "signal_type": signal.signal_type,
                    "entity_id": signal.entity_id,
                    "observable_id": signal.observable_id,
                    "score": signal.score,
                    "occurrences": signal.occurrences,
                    "max_gap_hours": signal.max_gap_hours,
                    "first_seen": _iso(signal.first_seen),
                    "last_seen": _iso(signal.last_seen),
                    "aliases": list(signal.aliases),
                    "platforms": list(signal.platforms),
                    "evidence_ids": list(signal.evidence_ids),
                    "explanation": signal.explanation,
                }
            )
        return {
            "signals": payload,
            "truncated": len(payload) >= limit,
            "suppressed": 0,
        }

    # ------------------------------------------------------------------
    # Propuestas
    # ------------------------------------------------------------------
    _PROPOSAL_FIELDS = """
        p.proposal_id::text, p.proposal_type::text, p.status::text,
        p.mission_id::text, p.agent_type::text, p.target_entity_id::text,
        p.payload, p.evidence_ids::text[], p.confidence, p.version,
        p.created_at, p.reviewed_by, p.reviewed_at, p.decision_reason,
        p.pir_id::text, p.payload_sha256, p.expires_at
    """

    @staticmethod
    def _proposal(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "proposal_id": row["proposal_id"],
            "proposal_type": row["proposal_type"],
            "status": row["status"],
            "mission_id": row["mission_id"],
            "agent_type": row["agent_type"],
            "target_entity_id": row["target_entity_id"],
            "payload": row["payload"] or {},
            "evidence_ids": _string_list(row["evidence_ids"]),
            "confidence": int(row["confidence"] or 0),
            "version": int(row["version"] or 1),
            "created_at": _iso(row["created_at"]),
            "reviewed_by": row["reviewed_by"],
            "reviewed_at": _iso(row["reviewed_at"]),
            "decision_reason": row["decision_reason"],
            "pir_id": row["pir_id"],
            "payload_sha256": row["payload_sha256"],
            "expires_at": _iso(row["expires_at"]),
        }

    def list_proposals(
        self,
        *,
        status: Optional[str],
        mission_id: Optional[str],
        limit: int,
        cursor: Optional[str],
        clearance: str,
        reviewer_role: str = "ANALYST",
    ) -> dict[str, Any]:
        sql = f"""
            SELECT {self._PROPOSAL_FIELDS}
            FROM staging.agent_proposals p
            WHERE (%s::text IS NULL OR p.status::text = %s)
              AND (%s::uuid IS NULL OR p.mission_id = %s::uuid)
              AND intel.tlp_rank(p.tlp_classification::text) <= intel.tlp_rank(%s)
            ORDER BY p.created_at DESC
            LIMIT %s
        """
        rows = self._rows(
            sql,
            (status, status, mission_id, mission_id, clearance, limit + 1),
            clearance=clearance,
        )
        truncated = len(rows) > limit
        return {
            "items": [self._proposal(row) for row in rows[:limit]],
            "truncated": truncated,
            "next_cursor": None,
        }

    def get_proposal(
        self,
        proposal_id: str,
        *,
        clearance: str,
        reviewer_role: str = "ANALYST",
    ) -> Optional[dict[str, Any]]:
        sql = f"""
            SELECT {self._PROPOSAL_FIELDS}
            FROM staging.agent_proposals p
            WHERE p.proposal_id = %s::uuid
              AND intel.tlp_rank(p.tlp_classification::text) <= intel.tlp_rank(%s)
        """
        row = self._one(sql, (proposal_id, clearance), clearance=clearance)
        return self._proposal(row) if row else None

    def _decide(
        self,
        proposal_id: str,
        *,
        status: str,
        reviewer_id: str,
        reason: str,
        version: int,
        clearance: str,
    ) -> Optional[dict[str, Any]]:
        # La API no tiene UPDATE directo: la función SECURITY DEFINER valida
        # transición, motivo, versión y TLP. Revisor y clearance viajan en la
        # transacción (osint.actor / osint.clearance), no como parámetros.
        # Si no actualiza, se distingue 404 de conflicto de versión.
        sql = f"""
            SELECT {self._PROPOSAL_FIELDS}
            FROM staging.fn_decide_proposal(
                %s::uuid, %s::intel.proposal_status_enum, %s, %s
            ) p
        """
        with transaction(
            self._pool, clearance=clearance, actor=reviewer_id
        ) as conn:
            row = conn.execute(
                sql,
                (proposal_id, status, reason, version),
            ).fetchone()
        if row is None:
            current = self.get_proposal(
                proposal_id, clearance=clearance
            )
            if current is None:
                return None
            raise ProposalVersionConflict(
                "La propuesta cambió o ya no está en AGENT_STAGED "
                f"(version actual={current['version']})."
            )
        return self._proposal(row)

    def approve_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_id: str,
        reason: str,
        version: int,
        clearance: str,
    ) -> Optional[dict[str, Any]]:
        return self._decide(
            proposal_id,
            status="ANALYST_VERIFIED",
            reviewer_id=reviewer_id,
            reason=reason,
            version=version,
            clearance=clearance,
        )

    def reject_proposal(
        self,
        proposal_id: str,
        *,
        reviewer_id: str,
        reason: str,
        version: int,
        clearance: str,
    ) -> Optional[dict[str, Any]]:
        return self._decide(
            proposal_id,
            status="REJECTED",
            reviewer_id=reviewer_id,
            reason=reason,
            version=version,
            clearance=clearance,
        )

    # ------------------------------------------------------------------
    # Evidencia
    # ------------------------------------------------------------------
    def get_evidence(
        self,
        evidence_id: str,
        *,
        clearance: str,
        viewer_role: str = "VIEWER",
    ) -> Optional[dict[str, Any]]:
        sql = """
            SELECT evidence_id::text, payload_sha256, tlp_classification::text,
                   collector_agent::text, source_type::text, source_uri,
                   captured_at_utc, ingested_at, raw_payload, metadata
            FROM staging.raw_evidence_vault
            WHERE evidence_id = %s::uuid
              AND intel.tlp_rank(tlp_classification::text) <= intel.tlp_rank(%s)
        """
        row = self._one(sql, (evidence_id, clearance), clearance=clearance)
        if row is None:
            return None
        return {
            "evidence_id": row["evidence_id"],
            "payload_sha256": row["payload_sha256"],
            "tlp": row["tlp_classification"],
            "collector_agent": row["collector_agent"],
            "source_type": row["source_type"],
            "source_uri": row["source_uri"],
            "captured_at": _iso(row["captured_at_utc"]),
            "received_at": _iso(row["ingested_at"]),
            "payload": row["raw_payload"] or {},
            "metadata": row["metadata"] or {},
        }

    def verify_evidence(
        self,
        evidence_id: str,
        *,
        clearance: str,
        reviewer_role: str = "ANALYST",
    ) -> Optional[dict[str, Any]]:
        sql = """
            SELECT evidence_id::text, ledger_seq, payload_sha256,
                   raw_payload_bytes, prev_hash, record_hash,
                   captured_at_utc, collector_agent::text,
                   staging.verify_evidence_chain(1, ledger_seq) AS chain_ok
            FROM staging.raw_evidence_vault
            WHERE evidence_id = %s::uuid
              AND intel.tlp_rank(tlp_classification::text) <= intel.tlp_rank(%s)
        """
        row = self._one(sql, (evidence_id, clearance), clearance=clearance)
        if row is None:
            return None
        raw = bytes(row["raw_payload_bytes"] or b"")
        computed = hashlib.sha256(raw).hexdigest()
        payload_valid = computed == row["payload_sha256"]
        expected_record = hashlib.sha256(
            (
                (row["prev_hash"] or "")
                + row["evidence_id"]
                + row["payload_sha256"]
                + row["captured_at_utc"].astimezone(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%S.%fZ"
                )
                + row["collector_agent"]
            ).encode("utf-8")
        ).hexdigest()
        chain_valid = bool(
            payload_valid
            and row["record_hash"] == expected_record
            and row["chain_ok"] is True
        )
        return {
            "evidence_id": row["evidence_id"],
            "valid": bool(payload_valid and chain_valid),
            "stored_sha256": row["payload_sha256"],
            "computed_sha256": computed,
            "chain_valid": chain_valid,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }


__all__ = [
    "EvidenceConflict",
    "PostgresV31Repository",
    "ProposalVersionConflict",
]
