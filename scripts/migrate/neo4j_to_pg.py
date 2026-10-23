#!/usr/bin/env python3
"""ETL Neo4j v2.2 → PostgreSQL v3 (Hito 1).

Uso:
    python scripts/migrate/neo4j_to_pg.py export --out storage/migration
    python scripts/migrate/neo4j_to_pg.py load \
        --nodes storage/migration/nodes.jsonl \
        --edges storage/migration/edges.jsonl \
        --owner-dsn "$POSTGRES_DSN_OWNER" \
        --atenea-dsn "$POSTGRES_DSN_ATENEA"
    python scripts/migrate/neo4j_to_pg.py reconcile --owner-dsn "$POSTGRES_DSN_OWNER"

El ETL es **conservador**: lo que no se puede mapear sin ambigüedad va a
``staging.migration_quarantine`` y no se inventa inteligencia. No borra Neo4j.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps" / "backend"))

LABEL_MAP = {
    "Person": "identity",
    "Organization": "identity",
    "CyberAsset": "infrastructure",
    "Location": "location",
    "Event": "x-osint-event",
}

REL_MAP = {
    "CONTROLS": "CONTROLS_ACCOUNT",
    "FINANCED": "FINANCED_BY",
    "USES_ASSET": "USES",
    "LOCATED_AT": "LOCATED_AT",
    "ASSOCIATED_WITH": "RELATED_TO",
    "PARTICIPATED_IN": "PARTICIPATED_IN",
    "MENTIONED_IN": "MENTIONS",
    "INVESTIGATES": "RELATED_TO",
    "CITES_ANALYSIS": "RELATED_TO",
    "CORROBORATES": "RELATED_TO",
    "CONTRADICTS": "RELATED_TO",
    "EXTENDS": "RELATED_TO",
    "ANSWERS_REQUIREMENT": "RELATED_TO",
    "RAISED_FOR": "RELATED_TO",
    "ABOUT": "RELATED_TO",
}

ADMIRALTY_RE = re.compile(r"^[A-F][1-6]$")


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return str(value)


def export_neo4j(out_dir: Path) -> tuple[Path, Path]:
    """Exporta nodos y aristas a JSONL (sin transformar)."""
    from neo4j import GraphDatabase

    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    nodes_path = out_dir / "nodes.jsonl"
    edges_path = out_dir / "edges.jsonl"

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        with driver.session() as session:
            with nodes_path.open("w", encoding="utf-8") as fh:
                for record in session.run(
                    """
                    MATCH (n)
                    RETURN elementId(n) AS id, labels(n) AS labels, properties(n) AS props
                    """
                ):
                    fh.write(
                        json.dumps(
                            {
                                "id": record["id"],
                                "labels": record["labels"],
                                "props": record["props"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
            with edges_path.open("w", encoding="utf-8") as fh:
                for record in session.run(
                    """
                    MATCH (a)-[r]->(b)
                    RETURN elementId(r) AS id, type(r) AS type,
                           elementId(a) AS source, elementId(b) AS target,
                           properties(r) AS props
                    """
                ):
                    fh.write(
                        json.dumps(
                            {
                                "id": record["id"],
                                "type": record["type"],
                                "source": record["source"],
                                "target": record["target"],
                                "props": record["props"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
    finally:
        driver.close()
    return nodes_path, edges_path


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                yield json.loads(line)


def _ensure_legacy_mission(conn: Any) -> tuple[str, str]:
    mission = conn.execute(
        """
        INSERT INTO intel.missions (code, name, status, legal_basis, classification)
        VALUES ('M-LEGACY', 'Migración Neo4j v2.2', 'ACTIVE',
                'migración de datos históricos', 'TLP:AMBER')
        ON CONFLICT (code) DO UPDATE SET updated_at = now()
        RETURNING mission_id
        """
    ).fetchone()["mission_id"]
    pir = conn.execute(
        """
        INSERT INTO intel.pirs (mission_id, code, question, priority, status)
        VALUES (%s, 'PIR-LEGACY', '¿Qué inteligencia histórica es válida?', 3, 'OPEN')
        ON CONFLICT (mission_id, code) DO UPDATE SET updated_at = now()
        RETURNING pir_id
        """,
        (mission,),
    ).fetchone()["pir_id"]
    return str(mission), str(pir)


def _insert_legacy_evidence(
    conn: Any,
    *,
    mission_pir_id: str,
    source_label: str,
    source_id: str,
    payload: dict[str, Any],
) -> str:
    idempotency_key = f"legacy:{source_label}:{source_id}"
    existing = conn.execute(
        """
        SELECT evidence_id FROM staging.raw_intel_ledger
        WHERE collector_agent = 'MIGRATION_LEGACY' AND idempotency_key = %s
        """,
        (idempotency_key,),
    ).fetchone()
    if existing:
        return str(existing["evidence_id"])
    raw = _json_bytes(payload)
    digest = hashlib.sha256(raw).hexdigest()
    row = conn.execute(
        """
        INSERT INTO staging.raw_intel_ledger (
            mission_pir_id, collector_agent, idempotency_key,
            source_platform, source_uri, raw_payload, raw_payload_bytes,
            payload_sha256, captured_at_utc, tlp_classification,
            collection_method, legal_basis, attachments, metadata
        ) VALUES (
            %s, 'MIGRATION_LEGACY', %s,
            'neo4j', %s, %s, %s,
            %s, now(), 'TLP:AMBER',
            'legacy_migration', 'migración de datos históricos',
            '[]'::jsonb, %s
        )
        RETURNING evidence_id
        """,
        (
            mission_pir_id,
            idempotency_key,
            f"neo4j://{source_label}/{source_id}",
            _jsonb(payload),
            raw,
            digest,
            _jsonb({"legacy_label": source_label, "legacy_id": source_id}),
        ),
    ).fetchone()
    return str(row["evidence_id"])


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb

        return Jsonb(value)
    except Exception:
        return value


def _quarantine(
    conn: Any, *, source_label: str, source_id: str, payload: dict[str, Any], reason: str
) -> None:
    conn.execute(
        """
        INSERT INTO staging.migration_quarantine
            (source_label, source_id, payload, reason)
        VALUES (%s, %s, %s, %s)
        """,
        (source_label, source_id, _jsonb(payload), reason),
    )


def _aliases(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return None


def _admiralty(value: Any) -> str:
    candidate = str(value or "").strip().upper()
    return candidate if ADMIRALTY_RE.match(candidate) else "C3"


def _confidence(value: Any) -> int | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if 0.0 <= number <= 1.0:
        number *= 100.0
    return max(0, min(100, int(round(number))))


def load(nodes_path: Path, edges_path: Path, owner_dsn: str, atenea_dsn: str) -> dict[str, int]:
    """Carga nodos/aristas y devuelve contadores de reconciliación."""
    import psycopg
    from psycopg.rows import dict_row

    from app.db.postgres_repository import PostgresRepository

    stats = {"nodes": 0, "edges": 0, "quarantined": 0}
    with psycopg.connect(owner_dsn, autocommit=True, row_factory=dict_row) as owner:
        mission_id, pir_id = _ensure_legacy_mission(owner)
        id_map: dict[str, str] = {}
        with PostgresRepository(dsn=atenea_dsn) as graph:
            for node in _iter_jsonl(nodes_path):
                stats["nodes"] += 1
                labels = node.get("labels") or []
                label = labels[0] if labels else "Unknown"
                stix_type = LABEL_MAP.get(label)
                if stix_type is None:
                    stats["quarantined"] += 1
                    _quarantine(
                        owner,
                        source_label=label,
                        source_id=str(node.get("id")),
                        payload=node,
                        reason="label sin mapeo STIX v3",
                    )
                    continue
                props = node.get("props") or {}
                name = (
                    props.get("canonical_name")
                    or props.get("name")
                    or props.get("value")
                    or str(node.get("id"))
                )
                evidence_id = _insert_legacy_evidence(
                    owner,
                    mission_pir_id=pir_id,
                    source_label=label,
                    source_id=str(node.get("id")),
                    payload=node,
                )
                try:
                    entity_id = graph.upsert_entity(
                        stix_type=stix_type,
                        name=str(name),
                        admiralty_rating=_admiralty(props.get("admiralty_rating")),
                        tlp_classification=str(props.get("tlp") or "TLP:AMBER"),
                        evidence_ids=[evidence_id],
                        aliases=_aliases(props.get("aliases")),
                        properties={
                            k: v
                            for k, v in props.items()
                            if k
                            not in {
                                "canonical_name",
                                "name",
                                "aliases",
                                "admiralty_rating",
                                "tlp",
                                "confidence",
                            }
                        },
                        confidence_override=_confidence(props.get("confidence")),
                    )
                except Exception as exc:
                    stats["quarantined"] += 1
                    _quarantine(
                        owner,
                        source_label=label,
                        source_id=str(node.get("id")),
                        payload=node,
                        reason=f"upsert_entity: {exc}",
                    )
                    continue
                id_map[str(node.get("id"))] = entity_id

            for edge in _iter_jsonl(edges_path):
                stats["edges"] += 1
                source = id_map.get(str(edge.get("source")))
                target = id_map.get(str(edge.get("target")))
                if source is None or target is None:
                    stats["quarantined"] += 1
                    _quarantine(
                        owner,
                        source_label=str(edge.get("type")),
                        source_id=str(edge.get("id")),
                        payload=edge,
                        reason="extremo no migrado o en cuarentena",
                    )
                    continue
                props = edge.get("props") or {}
                evidence_id = _insert_legacy_evidence(
                    owner,
                    mission_pir_id=pir_id,
                    source_label=str(edge.get("type")),
                    source_id=str(edge.get("id")),
                    payload=edge,
                )
                try:
                    graph.upsert_relationship(
                        relationship_type=REL_MAP.get(
                            str(edge.get("type")), "RELATED_TO"
                        ),
                        source_entity_id=source,
                        target_entity_id=target,
                        admiralty_rating=_admiralty(props.get("admiralty_rating")),
                        evidence_ids=[evidence_id],
                        properties={
                            k: v
                            for k, v in props.items()
                            if k not in {"admiralty_rating", "confidence"}
                        },
                        confidence_override=_confidence(props.get("confidence")),
                    )
                except Exception as exc:
                    stats["quarantined"] += 1
                    _quarantine(
                        owner,
                        source_label=str(edge.get("type")),
                        source_id=str(edge.get("id")),
                        payload=edge,
                        reason=f"upsert_relationship: {exc}",
                    )

        owner.execute(
            """
            INSERT INTO staging.migration_reconciliation
                (source_label, source_count, target_count, hash_mismatch, details)
            VALUES ('ALL', %s, %s, false, %s)
            """,
            (
                stats["nodes"] + stats["edges"],
                stats["nodes"] + stats["edges"] - stats["quarantined"],
                _jsonb(stats),
            ),
        )
    return stats


def reconcile(owner_dsn: str) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(owner_dsn, row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT source_label, source_count, target_count, hash_mismatch, details
            FROM staging.migration_reconciliation
            ORDER BY run_at DESC
            LIMIT 20
            """
        ).fetchall()
    return [dict(r) for r in rows]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ETL Neo4j v2.2 -> PostgreSQL v3")
    sub = parser.add_subparsers(dest="command", required=True)

    export_p = sub.add_parser("export")
    export_p.add_argument("--out", type=Path, default=Path("storage/migration"))

    load_p = sub.add_parser("load")
    load_p.add_argument("--nodes", type=Path, required=True)
    load_p.add_argument("--edges", type=Path, required=True)
    load_p.add_argument("--owner-dsn", default=os.environ.get("POSTGRES_DSN_OWNER", ""))
    load_p.add_argument("--atenea-dsn", default=os.environ.get("POSTGRES_DSN_ATENEA", ""))

    rec_p = sub.add_parser("reconcile")
    rec_p.add_argument("--owner-dsn", default=os.environ.get("POSTGRES_DSN_OWNER", ""))

    args = parser.parse_args(argv)
    if args.command == "export":
        nodes, edges = export_neo4j(args.out)
        print(json.dumps({"nodes": str(nodes), "edges": str(edges)}, ensure_ascii=False))
        return 0
    if args.command == "load":
        if not args.owner_dsn or not args.atenea_dsn:
            parser.error("load requiere --owner-dsn y --atenea-dsn")
        print(json.dumps(load(args.nodes, args.edges, args.owner_dsn, args.atenea_dsn)))
        return 0
    if args.command == "reconcile":
        if not args.owner_dsn:
            parser.error("reconcile requiere --owner-dsn")
        print(json.dumps(reconcile(args.owner_dsn), ensure_ascii=False, default=str))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
