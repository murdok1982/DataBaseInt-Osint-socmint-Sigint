"""Repositorio Cypher parametrizado para el grafo OSINT Nexus (v2.0).

Autorizado por: SPEC-01 (Ontología del Grafo, Coherencia y Entity Resolution),
SPEC-00 §2 (grafo Neo4j) y SPEC-02 §1 (Pre-Flight) / §5 (escritores de coherencia).
Zona: Agente 1 — ``apps/backend/app/db/**``.
Contrato consumido: ``contracts/graph-constraints.cypher`` (SELLADO v2.0.0).

Reglas aplicadas
----------------
- **Cypher siempre parametrizado** (Constitución, Regla 7 y prompt maestro):
  ningún *valor* de dato se concatena en la cadena Cypher; se pasan como
  ``$param``. *Labels* y *relationship types* NO admiten parámetros en Cypher 5,
  por eso se validan contra la *permit-list* derivada de SPEC-01 §1/§2/§3 antes
  de interpolarse (lista cerrada, jamás entrada de usuario arbitraria).
- **Trazabilidad doble** (Constitución, Regla 4): toda arista OSINT lleva
  ``confidence``, ``source_report_id``, ``exact_quote``, ``text_evidence_span``,
  ``page_number``, ``first_seen``, ``last_seen`` y ``locked_by_analyst``.
- **Inmutabilidad analítica** (Constitución, Regla 2 / SPEC-01 §1, §4): ningún
  proceso automático altera ni borra un nodo/arista con ``locked_by_analyst``.
  :class:`NodeLockedError` la señala (la API la traduce a ``409``).
- **Sin MERGE sobre propiedades mutables** (SPEC-01 §6): el ``MERGE`` es solo
  por ``id``; ``aliases``/``risk_score`` se actualizan con ``SET``.
"""

from __future__ import annotations

import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

# ---------------------------------------------------------------------
# Permit-lists derivadas del contrato (SPEC-01 §1, §2 y §3). Orden canónico.
# ---------------------------------------------------------------------
#: Los 9 labels de la ontología (SPEC-01 §1-2). Coincide con el enum
#: ``EntityLabel`` de ``contracts/openapi.yaml``. ``User``/``ApiKey`` son nodos
#: de plataforma (SPEC-04 §5) y se gestionan fuera de este repositorio.
ALLOWED_LABELS: tuple[str, ...] = (
    "Person",
    "Organization",
    "CyberAsset",
    "Location",
    "Event",
    "Report",
    "Analysis",
    "IntelRequirement",
    "CoherenceAlert",
)

#: Entidades OSINT (SPEC-01 §1). ``CyberAsset`` absorbe Device/FinancialAccount.
OSINT_ENTITY_LABELS: tuple[str, ...] = (
    "Person",
    "Organization",
    "CyberAsset",
    "Location",
    "Event",
)

#: Red OSINT (SPEC-01 §3.1) — 7 relaciones.
OSINT_RELATION_TYPES: tuple[str, ...] = (
    "CONTROLS",
    "FINANCED",
    "USES_ASSET",
    "LOCATED_AT",
    "ASSOCIATED_WITH",
    "PARTICIPATED_IN",
    "MENTIONED_IN",
)

#: Coherencia y análisis (SPEC-01 §3.2) — 9 relaciones.
COHERENCE_RELATION_TYPES: tuple[str, ...] = (
    "INVESTIGATES",
    "HAS_REQUIREMENT",
    "CITES_ANALYSIS",
    "CORROBORATES",
    "CONTRADICTS",
    "EXTENDS",
    "ANSWERS_REQUIREMENT",
    "RAISED_FOR",
    "ABOUT",
)

#: Las 16 relaciones del contrato v2.0.0.
ALLOWED_RELATION_TYPES: tuple[str, ...] = (
    OSINT_RELATION_TYPES + COHERENCE_RELATION_TYPES
)

#: Claves únicas de match exacto (SPEC-01 §4.2).
ALLOWED_UNIQUE_KEYS: tuple[str, ...] = ("id", "value", "registration_id", "code")

#: Propiedades obligatorias de toda arista OSINT (SPEC-01 §3.1 · contrato §5)
#: + Constitución Regla 4. Incluye ``text_evidence_span`` (citas de GraphRAG).
REQUIRED_RELATION_PROPS: tuple[str, ...] = (
    "confidence",
    "source_report_id",
    "exact_quote",
    "text_evidence_span",
    "page_number",
    "first_seen",
    "last_seen",
    "locked_by_analyst",
)

#: Dominios cerrados de los nodos analíticos (SPEC-01 §2).
ANALYSIS_STATUSES: tuple[str, ...] = ("ACTIVE", "UNDER_REVIEW", "CLOSED")
REQUIREMENT_PRIORITIES: tuple[str, ...] = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
REQUIREMENT_STATUSES: tuple[str, ...] = ("OPEN", "ANSWERED")
ALERT_TYPES: tuple[str, ...] = (
    "CONTRADICTION",
    "HIGH_VALUE_CORROBORATION",
    "NEW_ALIAS_MERGE",
)
ALERT_STATUSES: tuple[str, ...] = ("PENDING_ANALYST", "RESOLVED", "DISMISSED")
SEVERITIES: tuple[str, ...] = ("LOW", "MEDIUM", "CRITICAL")

#: Índice full-text del contrato §4 (SPEC-01 §6).
FULLTEXT_INDEX = "entity_names_ft"

#: Relaciones usadas por las rutas de influencia (SPEC-01 §5.1).
INFLUENCE_RELATION_TYPES: tuple[str, ...] = ("CONTROLS", "FINANCED")

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[4] / "contracts" / "graph-constraints.cypher"
)


class NodeLockedError(Exception):
    """El nodo/arista está protegido por ``locked_by_analyst`` (Regla 2).

    Es una excepción de dominio: la capa API (Agente 3) la traduce a ``409``
    (``contracts/openapi.yaml`` → response ``Conflict``).
    """

    def __init__(self, node_id: str, message: Optional[str] = None) -> None:
        self.node_id = node_id
        super().__init__(
            message
            or (
                f"El nodo '{node_id}' está bloqueado por el analista "
                "(locked_by_analyst=true); solo se admiten nuevas evidencias "
                "o alertas (SPEC-01 §1/§4, Constitución Regla 2)."
            )
        )


# DDL embebido de respaldo (copia fiel del núcleo del contrato SELLADO v2.0.0).
# Solo se usa si el archivo del contrato no está disponible en tiempo de ejecución.
_FALLBACK_DDL: tuple[str, ...] = (
    "CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (n:Person) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT organization_id_unique IF NOT EXISTS FOR (n:Organization) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT cyberasset_id_unique IF NOT EXISTS FOR (n:CyberAsset) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT location_id_unique IF NOT EXISTS FOR (n:Location) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT event_id_unique IF NOT EXISTS FOR (n:Event) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT report_id_unique IF NOT EXISTS FOR (n:Report) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT analysis_id_unique IF NOT EXISTS FOR (n:Analysis) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT requirement_id_unique IF NOT EXISTS FOR (n:IntelRequirement) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT alert_id_unique IF NOT EXISTS FOR (n:CoherenceAlert) REQUIRE n.id IS UNIQUE",
    "CREATE CONSTRAINT organization_regid_unique IF NOT EXISTS FOR (n:Organization) REQUIRE n.registration_id IS UNIQUE",
    "CREATE CONSTRAINT cyberasset_value_unique IF NOT EXISTS FOR (n:CyberAsset) REQUIRE n.value IS UNIQUE",
    "CREATE CONSTRAINT analysis_code_unique IF NOT EXISTS FOR (n:Analysis) REQUIRE n.code IS UNIQUE",
    "CREATE CONSTRAINT user_id_unique IF NOT EXISTS FOR (u:User) REQUIRE u.id IS UNIQUE",
    "CREATE CONSTRAINT user_username_unique IF NOT EXISTS FOR (u:User) REQUIRE u.username IS UNIQUE",
    "CREATE CONSTRAINT apikey_id_unique IF NOT EXISTS FOR (k:ApiKey) REQUIRE k.id IS UNIQUE",
    "CREATE CONSTRAINT apikey_key_hash_unique IF NOT EXISTS FOR (k:ApiKey) REQUIRE k.key_hash IS UNIQUE",
    "CREATE CONSTRAINT person_required IF NOT EXISTS FOR (n:Person) REQUIRE (n.canonical_name, n.risk_score) IS NOT NULL",
    "CREATE CONSTRAINT organization_required IF NOT EXISTS FOR (n:Organization) REQUIRE n.canonical_name IS NOT NULL",
    "CREATE CONSTRAINT cyberasset_required IF NOT EXISTS FOR (n:CyberAsset) REQUIRE (n.asset_type, n.value) IS NOT NULL",
    "CREATE CONSTRAINT location_required IF NOT EXISTS FOR (n:Location) REQUIRE n.name IS NOT NULL",
    "CREATE CONSTRAINT event_required IF NOT EXISTS FOR (n:Event) REQUIRE (n.title, n.event_date) IS NOT NULL",
    "CREATE CONSTRAINT report_required IF NOT EXISTS FOR (n:Report) REQUIRE (n.file_path, n.ingested_at) IS NOT NULL",
    "CREATE CONSTRAINT analysis_required IF NOT EXISTS FOR (n:Analysis) REQUIRE (n.thesis_summary, n.status) IS NOT NULL",
    "CREATE CONSTRAINT requirement_required IF NOT EXISTS FOR (n:IntelRequirement) REQUIRE (n.question, n.status) IS NOT NULL",
    "CREATE CONSTRAINT alert_required IF NOT EXISTS FOR (n:CoherenceAlert) REQUIRE (n.alert_type, n.status) IS NOT NULL",
    "CREATE INDEX person_name_idx IF NOT EXISTS FOR (n:Person) ON (n.canonical_name)",
    "CREATE INDEX organization_regid_idx IF NOT EXISTS FOR (n:Organization) ON (n.registration_id)",
    "CREATE INDEX cyberasset_value_idx IF NOT EXISTS FOR (n:CyberAsset) ON (n.value)",
    "CREATE INDEX location_name_idx IF NOT EXISTS FOR (n:Location) ON (n.name)",
    "CREATE INDEX event_date_idx IF NOT EXISTS FOR (n:Event) ON (n.event_date)",
    "CREATE INDEX report_ingested_idx IF NOT EXISTS FOR (n:Report) ON (n.ingested_at)",
    "CREATE INDEX analysis_status_idx IF NOT EXISTS FOR (n:Analysis) ON (n.status)",
    "CREATE INDEX requirement_status_idx IF NOT EXISTS FOR (n:IntelRequirement) ON (n.status)",
    "CREATE INDEX alert_status_idx IF NOT EXISTS FOR (n:CoherenceAlert) ON (n.status)",
    "CREATE FULLTEXT INDEX entity_names_ft IF NOT EXISTS "
    "FOR (n:Person|Organization|Location|CyberAsset|Event) "
    "ON EACH [n.canonical_name, n.aliases, n.value, n.name] "
    "OPTIONS { indexConfig: { `fulltext.analyzer`: 'standard-no-stop-words' } }",
)

# Caracteres con significado en la sintaxis Lucene del full-text index.
_LUCENE_SPECIAL = re.compile(r'([+\-!(){}\[\]^"~*?:\\/])')


def _escape_lucene(term: str) -> str:
    """Escapa un término para ``db.index.fulltext.queryNodes`` (evita inyección).

    El valor se pasa igualmente como ``$query``; este escape protege la sintaxis
    Lucene, no sustituye a la parametrización.
    """
    return _LUCENE_SPECIAL.sub(r"\\\1", term.strip())


def _parse_cypher_statements(text: str) -> list[str]:
    """Extrae sentencias ``CREATE ...`` del contrato, ignorando comentarios.

    Las sentencias pueden ocupar varias líneas y terminan en ``;``.
    """
    statements: list[str] = []
    buffer: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        buffer.append(line)
        if line.endswith(";"):
            statement = " ".join(buffer).rstrip(";").strip()
            statements.append(re.sub(r"\s+", " ", statement))
            buffer = []
    if buffer:
        statements.append(re.sub(r"\s+", " ", " ".join(buffer)).strip())
    return statements


class GraphRepository:
    """Acceso parametrizado al grafo Neo4j (SPEC-01).

    Inyecta el driver Neo4j (de :mod:`app.db.driver`). Los métodos que validan
    entrada lo hacen *antes* de tocar la base de datos, de modo que la clase es
    utilizable sin conexión para comprobar contratos.
    """

    # --- Plantillas Cypher (constantes, inspeccionables por los tests) ---
    # SPEC-01 §4 + contrato §5.1: MERGE solo por id; ``locked_by_analyst``
    # impide sobrescribir props/aliases de un nodo verificado por el analista.
    UPSERT_ENTITY_TEMPLATE = (
        "MERGE (n:{label} {{id: $id}})\n"
        "ON CREATE SET n += $props, n.aliases = $aliases, "
        "n.locked_by_analyst = coalesce(n.locked_by_analyst, false)\n"
        "ON MATCH SET n += CASE WHEN n.locked_by_analyst THEN {{}} ELSE $props END,\n"
        "  n.aliases = CASE WHEN n.locked_by_analyst THEN coalesce(n.aliases, [])\n"
        "    ELSE coalesce(n.aliases, []) + "
        "[a IN $aliases WHERE NOT a IN coalesce(n.aliases, [])] END\n"
        "RETURN n"
    )

    # SPEC-01 §3.1 + contrato §5.2: arista OSINT con toda la procedencia.
    UPSERT_RELATIONSHIP_TEMPLATE = (
        "MATCH (a {{id: $from_id}})\n"
        "MATCH (b {{id: $to_id}})\n"
        "MERGE (a)-[r:{rel_type}]->(b)\n"
        "ON CREATE SET r.first_seen = $first_seen, r.last_seen = $last_seen,\n"
        "  r.confidence = $confidence, r.source_report_id = $source_report_id,\n"
        "  r.exact_quote = $exact_quote, r.text_evidence_span = $text_evidence_span,\n"
        "  r.page_number = $page_number, r.locked_by_analyst = $locked_by_analyst\n"
        "ON MATCH SET r.last_seen = $last_seen,\n"
        "  r.confidence = CASE WHEN r.locked_by_analyst THEN r.confidence "
        "WHEN $confidence > r.confidence THEN $confidence ELSE r.confidence END\n"
        "RETURN r"
    )

    # Fallback genérico de arista (tipos de coherencia escritos por ``upsert_relationship``).
    UPSERT_COHERENCE_EDGE_TEMPLATE = (
        "MATCH (a {{id: $from_id}})\n"
        "MATCH (b {{id: $to_id}})\n"
        "MERGE (a)-[r:{rel_type}]->(b)\n"
        "ON CREATE SET r += $props\n"
        "ON MATCH SET r += $props\n"
        "RETURN r"
    )

    # SPEC-01 §4: match exacto por clave única.
    FIND_BY_UNIQUE_KEY_TEMPLATE = (
        "MATCH (n:{label} {{{key}: $value}}) RETURN n LIMIT 1"
    )

    # SPEC-01 §6: búsqueda difusa sobre el full-text index del contrato.
    FULLTEXT_SEARCH_CYPHER = (
        "CALL db.index.fulltext.queryNodes('"
        + FULLTEXT_INDEX
        + "', $query)\n"
        "YIELD node, score\n"
        "RETURN node, labels(node) AS labels, score\n"
        "ORDER BY score DESC\n"
        "LIMIT $limit"
    )

    # SPEC-01 §5.1: rutas de influencia a N saltos (el rango variable no admite
    # parámetros en Cypher 5 → {max_hops} se valida como entero en Python).
    PATHS_OF_INFLUENCE_TEMPLATE = (
        "MATCH path = (a {{id: $origin_id}})-[r:CONTROLS|FINANCED*1..{max_hops}]->(b)\n"
        "WHERE all(rel IN relationships(path) WHERE rel.confidence >= $min_conf)\n"
        "RETURN path\n"
        "ORDER BY length(path) ASC\n"
        "LIMIT 100"
    )

    # SPEC-01 §5.2: intermediarios críticos (reach por ASSOCIATED_WITH).
    CRITICAL_BROKERS_TEMPLATE = (
        "MATCH (a:Person)-[:ASSOCIATED_WITH*{min_hops}..{max_hops}]-(b:Person)\n"
        "WHERE a.id <> b.id\n"
        "WITH a, count(DISTINCT b) AS reach\n"
        "RETURN a.canonical_name AS broker, reach\n"
        "ORDER BY reach DESC\n"
        "LIMIT $limit"
    )

    # SPEC-01 §5.3: búsqueda por metadatos con evidencia (sin modificar columnas).
    ASSETS_WITH_EVIDENCE_CYPHER = (
        "MATCH (x)-[r:USES_ASSET]->(ca:CyberAsset {{value: $asset_value}})\n"
        "RETURN labels(x)[0] AS tipo, x.canonical_name AS entidad,\n"
        "  r.confidence AS confianza, r.exact_quote AS evidencia,\n"
        "  r.source_report_id AS informe\n"
        "ORDER BY r.confidence DESC"
    )

    # --- Apoyo a Entity Resolution / riesgo (SPEC-01 §4) ---
    OUTGOING_RELS_CYPHER = (
        "MATCH (d {{id: $duplicate_id}})-[r]->(x)\n"
        "RETURN type(r) AS rel_type, x.id AS target_id, properties(r) AS props"
    )
    INCOMING_RELS_CYPHER = (
        "MATCH (d {{id: $duplicate_id}})<-[r]-(x)\n"
        "RETURN type(r) AS rel_type, x.id AS source_id, properties(r) AS props"
    )
    DELETE_NODE_CYPHER = "MATCH (d {id: $duplicate_id}) DETACH DELETE d"
    LIST_RELATIONSHIPS_CYPHER = (
        "MATCH (n {{id: $id}})-[r]->(m) "
        "RETURN type(r) AS rel_type, properties(r) AS props, 'OUT' AS direction\n"
        "UNION\n"
        "MATCH (n {{id: $id}})<-[r]-(m) "
        "RETURN type(r) AS rel_type, properties(r) AS props, 'IN' AS direction"
    )
    SET_RISK_SCORE_CYPHER = (
        "MATCH (n {id: $id}) SET n.risk_score = $risk_score RETURN n.risk_score AS risk_score"
    )

    # --- Inmutabilidad analítica (SPEC-01 §1/§4 · Constitución Regla 2) ---
    NODE_LOCK_STATE_CYPHER = (
        "MATCH (n {id: $node_id}) WHERE NOT n:User\n"
        "RETURN coalesce(n.locked_by_analyst, false) AS locked\n"
        "LIMIT 1"
    )
    SET_NODE_LOCKED_CYPHER = (
        "MATCH (n {id: $node_id}) WHERE NOT n:User\n"
        "SET n.locked_by_analyst = $locked\n"
        "RETURN n AS node, labels(n) AS labels"
    )
    LOCK_EDGE_TEMPLATE = (
        "MATCH (a {{id: $from_id}})-[r:{rel_type}]->(b {{id: $to_id}})\n"
        "SET r.locked_by_analyst = $locked\n"
        "RETURN r"
    )

    # --- Lectura del grafo y de informes (SPEC-03 §3/§4) --------------
    # Todas las lecturas son Cypher parametrizado; los *labels* y los rangos de
    # salto se validan contra la permit-list antes de interpolarse (lista cerrada).
    LIST_NODES_CYPHER = (
        "MATCH (n)\n"
        "WHERE NOT n:User\n"
        "  AND ($label IS NULL OR $label IN labels(n))\n"
        "  AND ($cursor IS NULL OR n.id > $cursor)\n"
        "OPTIONAL MATCH (n)-[r]-()\n"
        "WITH n, collect(r) AS rels\n"
        "WHERE ($min_confidence IS NULL OR any(rel IN rels "
        "WHERE rel.confidence >= $min_confidence))\n"
        "  AND ($from_time IS NULL OR any(rel IN rels "
        "WHERE rel.first_seen >= $from_time))\n"
        "  AND ($to_time IS NULL OR any(rel IN rels "
        "WHERE rel.first_seen <= $to_time))\n"
        "RETURN n AS node, labels(n) AS labels\n"
        "ORDER BY n.id ASC\n"
        "LIMIT $limit"
    )

    LIST_NODES_FULLTEXT_CYPHER = (
        "CALL db.index.fulltext.queryNodes('"
        + FULLTEXT_INDEX
        + "', $query)\n"
        "YIELD node, score\n"
        "WHERE NOT node:User\n"
        "  AND ($label IS NULL OR $label IN labels(node))\n"
        "RETURN node, labels(node) AS labels, score\n"
        "ORDER BY score DESC\n"
        "LIMIT $limit"
    )

    GET_NODE_CYPHER = (
        "MATCH (n {id: $node_id}) WHERE NOT n:User\n"
        "RETURN n AS node, labels(n) AS labels\n"
        "LIMIT 1"
    )

    UPDATE_NODE_CYPHER = (
        "MATCH (n {id: $node_id}) WHERE NOT n:User\n"
        "  AND coalesce(n.locked_by_analyst, false) = false\n"
        "SET n.canonical_name = coalesce($canonical_name, n.canonical_name),\n"
        "  n.aliases = coalesce(n.aliases, []) + "
        "[a IN $aliases WHERE NOT a IN coalesce(n.aliases, [])],\n"
        "  n += $attributes\n"
        "RETURN n AS node, labels(n) AS labels"
    )

    DELETE_NODE_BY_ID_CYPHER = (
        "MATCH (n {id: $node_id}) WHERE NOT n:User\n"
        "  AND coalesce(n.locked_by_analyst, false) = false\n"
        "DETACH DELETE n"
    )

    LIST_EDGES_CYPHER = (
        "MATCH (a)-[r]->(b)\n"
        "WHERE ($min_confidence IS NULL OR r.confidence >= $min_confidence)\n"
        "RETURN elementId(r) AS id, a.id AS from_id, b.id AS to_id,\n"
        "  type(r) AS rel_type, properties(r) AS props\n"
        "ORDER BY r.last_seen DESC\n"
        "LIMIT $limit"
    )

    #: Vecindad a N saltos (SPEC-03 §6): nodos alcanzados + el centro.
    NEIGHBORHOOD_NODES_TEMPLATE = (
        "MATCH (start {{id: $node_id}})\n"
        "OPTIONAL MATCH (start)-[*1..{depth}]-(other)\n"
        "WITH start, collect(DISTINCT other) AS others\n"
        "UNWIND ([start] + [o IN others WHERE o IS NOT NULL]) AS nnode\n"
        "RETURN DISTINCT nnode.id AS id, labels(nnode)[0] AS label,\n"
        "  properties(nnode) AS props"
    )

    #: Vecindad a N saltos (SPEC-03 §6): aristas alcanzadas.
    NEIGHBORHOOD_EDGES_TEMPLATE = (
        "MATCH path = (start {{id: $node_id}})-[*1..{depth}]-(other)\n"
        "UNWIND relationships(path) AS rel\n"
        "RETURN DISTINCT elementId(rel) AS id, type(rel) AS rel_type,\n"
        "  startNode(rel).id AS from_id, endNode(rel).id AS to_id,\n"
        "  properties(rel) AS props"
    )

    #: Adaptador a la interfaz de lectura del motor GraphRAG (SPEC-03 §5, B5).
    SEARCH_NODES_CYPHER = (
        "CALL db.index.fulltext.queryNodes('"
        + FULLTEXT_INDEX
        + "', $query)\n"
        "YIELD node, score\n"
        "WHERE NOT node:User\n"
        "RETURN node, labels(node) AS labels, score\n"
        "ORDER BY score DESC\n"
        "LIMIT $limit"
    )

    GET_REPORT_CYPHER = (
        "MATCH (r:Report {id: $report_id})\n"
        "RETURN r.id AS id, r.title AS title, r.file_path AS file_path,\n"
        "  r.ingested_at AS ingested_at, r.author_id AS author_id\n"
        "LIMIT 1"
    )

    GET_REPORT_FILE_PATH_CYPHER = (
        "MATCH (r:Report {id: $report_id}) "
        "RETURN r.file_path AS file_path LIMIT 1"
    )

    GET_REPORT_CHUNKS_CYPHER = (
        "MATCH (r:Report {id: $report_id}) RETURN r.chunks AS chunks LIMIT 1"
    )

    # -----------------------------------------------------------------
    # Nodos analíticos: Analysis / IntelRequirement / CoherenceAlert
    # (SPEC-01 §2; contrato §6 y §8)
    # -----------------------------------------------------------------
    CREATE_NODE_TEMPLATE = (
        "MERGE (n:{label} {{id: $id}})\n"
        "ON CREATE SET n += $props\n"
        "RETURN n AS node, labels(n) AS labels"
    )
    GET_ANALYSIS_CYPHER = (
        "MATCH (a:Analysis {id: $analysis_id}) RETURN a AS node LIMIT 1"
    )
    UPDATE_ANALYSIS_CYPHER = (
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "SET a += $patch, a.updated_at = $updated_at\n"
        "RETURN a AS node"
    )
    LIST_ANALYSES_CYPHER = (
        "MATCH (a:Analysis)\n"
        "WHERE $status IS NULL OR a.status = $status\n"
        "RETURN a AS node\n"
        "ORDER BY a.updated_at DESC"
    )
    # SPEC-01 §5.4/§8: pre-flight (Analysis activos por palabras clave).
    ACTIVE_ANALYSES_CYPHER = (
        "MATCH (a:Analysis)\n"
        "WHERE a.status = 'ACTIVE'\n"
        "  AND any(k IN $keywords "
        "WHERE toLower(coalesce(a.thesis_summary, '')) CONTAINS toLower(k))\n"
        "RETURN a.id AS id, a.code AS code, a.thesis_summary AS thesis_summary\n"
        "ORDER BY a.updated_at DESC"
    )

    GET_REQUIREMENT_CYPHER = (
        "MATCH (q:IntelRequirement {id: $requirement_id}) RETURN q AS node LIMIT 1"
    )
    #: RFI de un expediente (SPEC-01 §2/§3.2 · contrato §6 ``listAnalysisRequirements``).
    #: Un ``IntelRequirement`` pertenece a su ``Analysis`` por la arista
    #: ``HAS_REQUIREMENT``; se admite también la FK ``parent_analysis_id`` para no
    #: ocultar filas escritas antes de modelarse la relación.
    LIST_REQUIREMENTS_CYPHER = (
        "MATCH (q:IntelRequirement)\n"
        "WHERE q.parent_analysis_id = $analysis_id\n"
        "   OR EXISTS { (:Analysis {id: $analysis_id})-[:HAS_REQUIREMENT]->(q) }\n"
        "RETURN DISTINCT q AS node\n"
        "ORDER BY q.created_at"
    )
    LIST_OPEN_REQUIREMENTS_CYPHER = (
        "MATCH (q:IntelRequirement {status: 'OPEN'})\n"
        "RETURN q AS node\n"
        "ORDER BY q.priority"
    )

    # SPEC-01 §5.4: bandeja de coherencia.
    LIST_ALERTS_CYPHER = (
        "MATCH (al:CoherenceAlert)\n"
        "WHERE $status IS NULL OR al.status = $status\n"
        "OPTIONAL MATCH (al)-[:RAISED_FOR]->(a:Analysis)\n"
        "OPTIONAL MATCH (al)-[:ABOUT]->(e)\n"
        "RETURN al AS node, a.id AS analysis_id, e.id AS entity_id\n"
        "ORDER BY al.created_at DESC"
    )
    RESOLVE_ALERT_CYPHER = (
        "MATCH (al:CoherenceAlert {id: $alert_id})\n"
        "SET al.status = $status, al.resolution_note = $note\n"
        "RETURN al AS node"
    )

    # Escritores de coherencia (SPEC-01 §3.2; contrato §6).
    CORROBORATE_CYPHER = (
        "MATCH (rep:Report {id: $report_id})\n"
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "MERGE (rep)-[c:CORROBORATES]->(a)\n"
        "  ON CREATE SET c.confidence = $confidence, "
        "c.justification = $justification, c.verified_by_human = false, "
        "c.source_report_id = $report_id, c.exact_quote = $exact_quote, "
        "c.text_evidence_span = $text_evidence_span, c.page_number = $page_number\n"
        "  ON MATCH SET c.confidence = CASE WHEN $confidence > c.confidence "
        "THEN $confidence ELSE c.confidence END, "
        "c.justification = $justification, c.source_report_id = $report_id, "
        "c.exact_quote = coalesce($exact_quote, c.exact_quote), "
        "c.text_evidence_span = coalesce($text_evidence_span, c.text_evidence_span), "
        "c.page_number = coalesce($page_number, c.page_number)\n"
        "WITH a\n"
        "SET a.coherence_score = min(1.0, "
        "coalesce(a.coherence_score, 0) + $confidence)\n"
        "RETURN a AS node"
    )

    CONTRADICT_CYPHER = (
        "MATCH (rep:Report {id: $report_id})\n"
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "MERGE (rep)-[x:CONTRADICTS]->(a)\n"
        "  ON CREATE SET x.conflict_detail = $conflict_detail, "
        "x.severity = $severity, x.resolved = false, "
        "x.source_report_id = $report_id, x.exact_quote = $exact_quote, "
        "x.text_evidence_span = $text_evidence_span, x.page_number = $page_number\n"
        "  ON MATCH SET x.conflict_detail = $conflict_detail, "
        "x.severity = $severity, x.source_report_id = $report_id, "
        "x.exact_quote = coalesce($exact_quote, x.exact_quote), "
        "x.text_evidence_span = coalesce($text_evidence_span, x.text_evidence_span), "
        "x.page_number = coalesce($page_number, x.page_number)\n"
        "MERGE (al:CoherenceAlert {id: $alert_id})\n"
        "  ON CREATE SET al.alert_type = 'CONTRADICTION', "
        "al.status = 'PENDING_ANALYST', al.description = $conflict_detail, "
        "al.created_at = $created_at\n"
        "MERGE (al)-[rf:RAISED_FOR]->(a)\n"
        "  ON CREATE SET rf.raised_at = $created_at\n"
        "WITH al, $entity_id AS entity_id\n"
        "OPTIONAL MATCH (e {id: entity_id})\n"
        "FOREACH (_ IN CASE WHEN e IS NULL THEN [] ELSE [1] END |\n"
        "  MERGE (al)-[ab:ABOUT]->(e) ON CREATE SET ab.raised_at = $created_at)\n"
        "RETURN al AS node"
    )

    EXTEND_CYPHER = (
        "MATCH (rep:Report {id: $report_id})\n"
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "MERGE (rep)-[r:EXTENDS]->(a)\n"
        "  ON CREATE SET r.new_angle_summary = $new_angle_summary, "
        "r.source_report_id = $report_id, r.exact_quote = $exact_quote, "
        "r.text_evidence_span = $text_evidence_span, r.page_number = $page_number\n"
        "  ON MATCH SET r.new_angle_summary = $new_angle_summary, "
        "r.exact_quote = coalesce($exact_quote, r.exact_quote), "
        "r.text_evidence_span = coalesce($text_evidence_span, r.text_evidence_span), "
        "r.page_number = coalesce($page_number, r.page_number)\n"
        "RETURN r"
    )

    ANSWER_REQUIREMENT_CYPHER = (
        "MATCH (rep:Report {id: $report_id})\n"
        "MATCH (q:IntelRequirement {id: $requirement_id})\n"
        "MERGE (rep)-[r:ANSWERS_REQUIREMENT]->(q)\n"
        "  ON CREATE SET r.answer_summary = $answer_summary, "
        "r.confidence = $confidence, r.source_report_id = $report_id, "
        "r.exact_quote = $exact_quote, r.text_evidence_span = $text_evidence_span, "
        "r.page_number = $page_number\n"
        "  ON MATCH SET r.answer_summary = $answer_summary, "
        "r.confidence = $confidence, "
        "r.exact_quote = coalesce($exact_quote, r.exact_quote), "
        "r.text_evidence_span = coalesce($text_evidence_span, r.text_evidence_span), "
        "r.page_number = coalesce($page_number, r.page_number)\n"
        "SET q.status = 'ANSWERED'\n"
        "RETURN r AS edge, q AS node"
    )

    CITE_ANALYSIS_CYPHER = (
        "MATCH (rep:Report {id: $report_id})\n"
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "MERGE (rep)-[r:CITES_ANALYSIS]->(a)\n"
        "  ON CREATE SET r.relevance_score = $relevance_score, "
        "r.rationale = $rationale, r.source_report_id = $report_id, "
        "r.exact_quote = $exact_quote, r.text_evidence_span = $text_evidence_span, "
        "r.page_number = $page_number\n"
        "  ON MATCH SET r.relevance_score = $relevance_score, "
        "r.rationale = $rationale, "
        "r.source_report_id = coalesce(r.source_report_id, $report_id), "
        "r.exact_quote = coalesce($exact_quote, r.exact_quote), "
        "r.text_evidence_span = coalesce($text_evidence_span, r.text_evidence_span), "
        "r.page_number = coalesce($page_number, r.page_number)\n"
        "RETURN r"
    )

    LINK_ANALYSIS_TO_ENTITY_CYPHER = (
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "MATCH (e {id: $entity_id})\n"
        "MERGE (a)-[r:INVESTIGATES]->(e)\n"
        "  ON CREATE SET r.added_at = $added_at\n"
        "RETURN r"
    )

    CREATE_ALERT_CYPHER = (
        "MERGE (al:CoherenceAlert {id: $alert_id})\n"
        "  ON CREATE SET al.alert_type = $alert_type, al.status = $status, "
        "al.description = $description, al.created_at = $created_at\n"
        "RETURN al AS node"
    )
    RAISE_FOR_ANALYSIS_CYPHER = (
        "MATCH (al:CoherenceAlert {id: $alert_id})\n"
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "MERGE (al)-[r:RAISED_FOR]->(a)\n"
        "  ON CREATE SET r.raised_at = $raised_at\n"
        "RETURN r"
    )
    RAISE_FOR_INVESTIGATORS_CYPHER = (
        "MATCH (al:CoherenceAlert {id: $alert_id})\n"
        "MATCH (e {id: $entity_id})\n"
        "OPTIONAL MATCH (a:Analysis)-[:INVESTIGATES]->(e)\n"
        "FOREACH (_ IN CASE WHEN a IS NULL THEN [] ELSE [1] END |\n"
        "  MERGE (al)-[r:RAISED_FOR]->(a) ON CREATE SET r.raised_at = $raised_at)\n"
        "RETURN al AS node"
    )
    ABOUT_ENTITY_CYPHER = (
        "MATCH (al:CoherenceAlert {id: $alert_id})\n"
        "MATCH (e {id: $entity_id})\n"
        "MERGE (al)-[r:ABOUT]->(e)\n"
        "  ON CREATE SET r.raised_at = $raised_at\n"
        "RETURN r"
    )
    HAS_REQUIREMENT_CYPHER = (
        "MATCH (a:Analysis {id: $analysis_id})\n"
        "MATCH (q:IntelRequirement {id: $requirement_id})\n"
        "MERGE (a)-[r:HAS_REQUIREMENT]->(q)\n"
        "  ON CREATE SET r.added_at = $added_at\n"
        "RETURN r"
    )

    #: Pre-flight §8: diccionario de entidades canónicas OSINT.
    CANONICAL_ENTITIES_CYPHER = (
        "MATCH (e:Person|Organization|CyberAsset|Location|Event)\n"
        "RETURN e.id AS id, labels(e)[0] AS label,\n"
        "  coalesce(e.canonical_name, e.value, e.name) AS canonical_name\n"
        "ORDER BY canonical_name"
    )

    #: Propiedades que no forman parte de ``GraphNode.attributes``.
    _NODE_RESERVED_PROPS: frozenset[str] = frozenset(
        {
            "id",
            "label",
            "canonical_name",
            "name",
            "value",
            "aliases",
            "risk_score",
            "locked_by_analyst",
        }
    )

    def __init__(self, driver: Any = None, database: Optional[str] = None) -> None:
        self._driver = driver
        self._database = database

    # -----------------------------------------------------------------
    # Infraestructura
    # -----------------------------------------------------------------
    @contextmanager
    def _session(self) -> Iterator[Any]:
        if self._driver is None:
            raise RuntimeError(
                "GraphRepository requiere un driver Neo4j inyectado "
                "(usa app.db.driver.create_driver)."
            )
        session = (
            self._driver.session(database=self._database)
            if self._database
            else self._driver.session()
        )
        try:
            yield session
        finally:
            session.close()

    @staticmethod
    def _now() -> str:
        """Marca temporal ISO-8601 (UTC), formato ``DateTime`` del contrato."""
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _validate_label(label: str) -> None:
        if label not in ALLOWED_LABELS:
            raise ValueError(
                f"Label '{label}' no está permitido. SPEC-01 §1-2: {list(ALLOWED_LABELS)}"
            )

    @staticmethod
    def _validate_rel_type(rel_type: str) -> None:
        if rel_type not in ALLOWED_RELATION_TYPES:
            raise ValueError(
                f"Relación '{rel_type}' no está permitida. "
                f"SPEC-01 §3: {list(ALLOWED_RELATION_TYPES)}"
            )

    @staticmethod
    def _validate_choice(field: str, value: str, allowed: tuple[str, ...]) -> str:
        if value not in allowed:
            raise ValueError(
                f"'{field}'='{value}' no es válido. Valores permitidos: {list(allowed)} "
                "(SPEC-01 §2)."
            )
        return value

    @staticmethod
    def _relationship_params(props: Optional[dict[str, Any]]) -> dict[str, Any]:
        """Valida y normaliza los parámetros de una arista OSINT (SPEC-01 §3.1)."""
        props = dict(props or {})
        # ``text_evidence_span`` cae a ``exact_quote`` si el llamante omite el
        # span (siguen siendo el mismo fragmento exacto del informe).
        if props.get("text_evidence_span") is None and props.get("exact_quote") is not None:
            props["text_evidence_span"] = props["exact_quote"]
        # ``locked_by_analyst`` por defecto ``false`` (contrato §3.1).
        if props.get("locked_by_analyst") is None:
            props["locked_by_analyst"] = False
        missing = [p for p in REQUIRED_RELATION_PROPS if props.get(p) is None]
        if missing:
            raise ValueError(
                "Toda arista OSINT requiere las propiedades obligatorias "
                f"{list(REQUIRED_RELATION_PROPS)} (faltan: {missing}). "
                "SPEC-01 §3.1 · Constitución, Regla 4."
            )
        confidence = float(props["confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence debe estar en el rango [0.0, 1.0].")
        page_number = int(props["page_number"])
        if page_number < 1:
            raise ValueError("page_number es 1-based (>= 1).")
        params = {p: props[p] for p in REQUIRED_RELATION_PROPS}
        params["confidence"] = confidence
        params["page_number"] = page_number
        params["locked_by_analyst"] = bool(params["locked_by_analyst"])
        return params

    @staticmethod
    def _coherence_provenance(
        exact_quote: Optional[str], page_number: Optional[int]
    ) -> dict[str, Any]:
        """Normaliza la procedencia de una arista analítica (SPEC-01 §3.2 · Regla 4).

        Toda arista de coherencia lleva ``source_report_id`` (lo aporta el
        llamante), ``exact_quote`` y ``text_evidence_span`` (que **replica** la
        cita exacta); ``page_number`` es opcional y, cuando llega, debe ser
        1-based. Los valores ``None`` no se persisten (los ``SET`` usan
        ``coalesce`` para no borrar evidencia previa).
        """
        params: dict[str, Any] = {
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": None,
        }
        if page_number is not None:
            value = int(page_number)
            if value < 1:
                raise ValueError("page_number es 1-based (>= 1).")
            params["page_number"] = value
        return params

    # -----------------------------------------------------------------
    # Proyección a los esquemas del contrato (GraphNode / GraphEdge)
    # -----------------------------------------------------------------
    @classmethod
    def _project_node(
        cls, node: Optional[dict[str, Any]], label: Optional[str] = None
    ) -> dict[str, Any]:
        """Proyecta las propiedades de un nodo sobre ``GraphNode`` (SPEC-03 §3)."""
        props = dict(node or {})
        resolved_label = label or props.get("label")
        canonical_name = (
            props.get("canonical_name") or props.get("name") or props.get("value")
        )
        attributes = {
            key: value
            for key, value in props.items()
            if key not in cls._NODE_RESERVED_PROPS
        }
        return {
            "id": str(props.get("id") or ""),
            "label": resolved_label,
            "canonical_name": canonical_name,
            "aliases": list(props.get("aliases") or []),
            "risk_score": props.get("risk_score"),
            "locked_by_analyst": bool(props.get("locked_by_analyst") or False),
            "attributes": attributes,
        }

    @classmethod
    def _project_record_node(cls, record: Any) -> Optional[dict[str, Any]]:
        """Proyecta un registro ``{node, labels}`` o ``None``."""
        if record is None:
            return None
        node = record.get("node")
        if node is None:
            return None
        labels = record.get("labels") or []
        label = labels[0] if labels else None
        return cls._project_node(node, label)

    @staticmethod
    def _project_edge(record: Any) -> dict[str, Any]:
        """Proyecta una arista sobre ``GraphEdge`` conservando su procedencia.

        Se adjunta además ``props`` (la procedencia cruda) para que la interfaz
        de lectura del motor GraphRAG (SPEC-03 §5, B5) pueda construir citas.
        """
        props = dict(record.get("props") or {})
        return {
            "id": str(record.get("id") or ""),
            "from": str(record.get("from_id") or ""),
            "to": str(record.get("to_id") or ""),
            "type": record.get("rel_type"),
            "confidence": props.get("confidence"),
            "source_report_id": props.get("source_report_id"),
            "exact_quote": props.get("exact_quote"),
            "text_evidence_span": props.get("text_evidence_span"),
            "page_number": props.get("page_number"),
            "locked_by_analyst": bool(props.get("locked_by_analyst") or False),
            "first_seen": props.get("first_seen"),
            "last_seen": props.get("last_seen"),
            "props": props,
        }

    @staticmethod
    def _alert_dict(
        node: Optional[dict[str, Any]],
        *,
        analysis_id: Optional[str] = None,
        entity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Proyecta un ``CoherenceAlert`` (contrato ``openapi.yaml``)."""
        props = dict(node or {})
        return {
            "id": str(props.get("id") or ""),
            "alert_type": props.get("alert_type"),
            "status": props.get("status"),
            "description": props.get("description"),
            "resolution_note": props.get("resolution_note"),
            "created_at": props.get("created_at"),
            "analysis_id": analysis_id,
            "entity_id": entity_id,
        }

    @staticmethod
    def _normalize_chunk(raw: Any, index: int = 0) -> dict[str, Any]:
        """Normaliza un chunk almacenado sobre el schema ``Chunk`` (SPEC-03 §4)."""
        data = dict(raw or {})
        return {
            "chunk_id": str(data.get("chunk_id") or f"chunk-{index}"),
            "page_number": int(data.get("page_number") or 1),
            "char_start": int(data.get("char_start") or 0),
            "char_end": int(data.get("char_end") or 0),
            "text": str(data.get("text") or ""),
        }

    # -----------------------------------------------------------------
    # Constraints / índices (SPEC-01 §6 · contrato §1–4)
    # -----------------------------------------------------------------
    @staticmethod
    def constraint_statements() -> list[str]:
        """Devuelve las sentencias DDL del contrato (o el DDL embebido).

        Autorizado por: SPEC-01 §6 + ``contracts/graph-constraints.cypher`` v2.0.0.
        """
        if _CONTRACT_PATH.is_file():
            statements = _parse_cypher_statements(
                _CONTRACT_PATH.read_text(encoding="utf-8")
            )
            if statements:
                return statements
        return list(_FALLBACK_DDL)

    def ensure_constraints(self) -> list[str]:
        """Carga idempotente de constraints e índices (`IF NOT EXISTS`).

        Autorizado por: SPEC-01 §6 y contrato §1–4. Ejecuta cada sentencia en
        autocommit; el ``IF NOT EXISTS`` del contrato garantiza idempotencia.
        """
        statements = self.constraint_statements()
        with self._session() as session:
            for statement in statements:
                session.run(statement).consume()
        return statements

    # -----------------------------------------------------------------
    # Escritura de nodos y aristas
    # -----------------------------------------------------------------
    def upsert_entity(
        self,
        label: str,
        id: str,
        props: Optional[dict[str, Any]] = None,
        alias: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        """MERGE de nodo por ``id`` fusionando ``aliases`` sin duplicar.

        Autorizado por: SPEC-01 §4 (Regla de "Rehacer Relaciones") y §6
        (nunca ``MERGE`` sobre propiedades mutables como ``aliases``).
        Si el nodo existente está ``locked_by_analyst`` **no se sobrescriben**
        sus propiedades ni sus alias (Constitución, Regla 2).
        """
        self._validate_label(label)
        payload = dict(props or {})
        aliases = list(payload.pop("aliases", None) or [])
        if alias is not None and alias not in aliases:
            aliases.append(alias)
        cypher = self.UPSERT_ENTITY_TEMPLATE.format(label=label)
        params = {"id": id, "props": payload, "aliases": aliases}
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, **params).single()
            )
        return dict(record["n"]) if record else None

    def upsert_relationship(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        props: Optional[dict[str, Any]] = None,
    ) -> Optional[dict[str, Any]]:
        """MERGE de arista con la procedencia obligatoria (SPEC-01 §3.1).

        ``last_seen`` se refresca y ``confidence`` se maximiza en match. Las
        aristas OSINT exigen :data:`REQUIRED_RELATION_PROPS`. Las aristas de
        coherencia se persisten con la plantilla genérica parametrizada.
        """
        self._validate_rel_type(rel_type)
        cypher, params = self._edge_statement(rel_type, props)
        params["from_id"] = from_id
        params["to_id"] = to_id
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(cypher, **params).single()
            )
        return dict(record["r"]) if record else None

    def _edge_statement(
        self, rel_type: str, props: Optional[dict[str, Any]]
    ) -> tuple[str, dict[str, Any]]:
        """Cypher + parámetros según el tipo de arista (OSINT vs coherencia)."""
        if rel_type in OSINT_RELATION_TYPES:
            return (
                self.UPSERT_RELATIONSHIP_TEMPLATE.format(rel_type=rel_type),
                self._relationship_params(props),
            )
        return (
            self.UPSERT_COHERENCE_EDGE_TEMPLATE.format(rel_type=rel_type),
            {"props": dict(props or {})},
        )

    def merge_duplicates(self, canonical_id: str, duplicate_id: str) -> None:
        """Reasigna aristas al canónico y elimina el duplicado.

        Autorizado por: SPEC-01 §4 (paso 5 "Rehacer Relaciones"). El tipo de
        arista se re-``MERGE`` tipo a tipo (validado contra la permit-list);
        no se usa APOC para no añadir dependencias (contrato §7.1).
        **No** copia propiedades ni alias del duplicado (Regla 2: un nodo
        bloqueado nunca se sobrescribe).
        """
        if canonical_id == duplicate_id:
            raise ValueError("canonical_id y duplicate_id deben ser distintos.")

        def _work(tx: Any) -> None:
            outgoing = list(tx.run(self.OUTGOING_RELS_CYPHER, duplicate_id=duplicate_id))
            incoming = list(tx.run(self.INCOMING_RELS_CYPHER, duplicate_id=duplicate_id))
            for record in outgoing:
                self._merge_edge(
                    tx,
                    canonical_id,
                    record["target_id"],
                    record["rel_type"],
                    dict(record["props"]),
                )
            for record in incoming:
                self._merge_edge(
                    tx,
                    record["source_id"],
                    canonical_id,
                    record["rel_type"],
                    dict(record["props"]),
                )
            tx.run(self.DELETE_NODE_CYPHER, duplicate_id=duplicate_id).consume()

        with self._session() as session:
            session.execute_write(_work)

    def _merge_edge(
        self,
        tx: Any,
        from_id: str,
        to_id: str,
        rel_type: str,
        props: dict[str, Any],
    ) -> None:
        """Re-MERGE de una arista existente sobre el nodo canónico (SPEC-01 §4)."""
        self._validate_rel_type(rel_type)
        cypher, params = self._edge_statement(rel_type, props)
        params["from_id"] = from_id
        params["to_id"] = to_id
        tx.run(cypher, **params).consume()

    # -----------------------------------------------------------------
    # Inmutabilidad analítica (SPEC-01 §1/§4 · Constitución Regla 2)
    # -----------------------------------------------------------------
    def is_node_locked(self, node_id: str) -> bool:
        """Indica si un nodo está bloqueado por el analista (Regla 2)."""
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(self.NODE_LOCK_STATE_CYPHER, node_id=node_id).single()
            )
        return bool(record.get("locked")) if record else False

    def _ensure_unlocked(self, node_id: str) -> None:
        if self.is_node_locked(node_id):
            raise NodeLockedError(node_id)

    def set_node_locked(
        self, node_id: str, locked: bool = True
    ) -> Optional[dict[str, Any]]:
        """Marca/desmarca ``locked_by_analyst`` de un nodo (SPEC-01 §1).

        Es la operación humana autorizada (``POST /graph/nodes/{id}/lock``);
        no está sujeta a la propia protección.
        """
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    self.SET_NODE_LOCKED_CYPHER,
                    node_id=node_id,
                    locked=bool(locked),
                ).single()
            )
        return self._project_record_node(record)

    def lock_edge(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        locked: bool = True,
    ) -> Optional[dict[str, Any]]:
        """Marca/desmarca ``locked_by_analyst`` de una arista (SPEC-01 §3.1).

        Una arista bloqueada conserva su ``confidence`` en los ``MERGE``
        posteriores (protección en :data:`UPSERT_RELATIONSHIP_TEMPLATE`).
        """
        self._validate_rel_type(rel_type)
        cypher = self.LOCK_EDGE_TEMPLATE.format(rel_type=rel_type)
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    cypher,
                    from_id=from_id,
                    to_id=to_id,
                    locked=bool(locked),
                ).single()
            )
        return dict(record["r"]) if record else None

    # -----------------------------------------------------------------
    # Lectura / búsqueda
    # -----------------------------------------------------------------
    def find_by_unique_key(
        self, label: str, key: str, value: Any
    ) -> Optional[dict[str, Any]]:
        """Match exacto por clave única (SPEC-01 §4, paso 2)."""
        self._validate_label(label)
        if key not in ALLOWED_UNIQUE_KEYS:
            raise ValueError(
                f"Clave '{key}' no es una clave única permitida. "
                f"SPEC-01 §4.2: {list(ALLOWED_UNIQUE_KEYS)}"
            )
        cypher = self.FIND_BY_UNIQUE_KEY_TEMPLATE.format(label=label, key=key)
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(cypher, value=value).single()
            )
        return dict(record["n"]) if record else None

    def fulltext_search(
        self, name: str, label: Optional[str] = None, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Búsqueda difusa contra el índice ``entity_names_ft`` (SPEC-01 §4/§6)."""
        if label is not None:
            self._validate_label(label)
        query = _escape_lucene(name)
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(self.FULLTEXT_SEARCH_CYPHER, query=query, limit=limit))
            )
        results: list[dict[str, Any]] = []
        for record in records:
            if label is not None and label not in (record["labels"] or []):
                continue
            node = dict(record["node"])
            node["_score"] = record["score"]
            results.append(node)
        return results

    # -----------------------------------------------------------------
    # Consultas de inteligencia (SPEC-01 §5)
    # -----------------------------------------------------------------
    def paths_of_influence(
        self, origin_id: str, max_hops: int = 3, min_conf: float = 0.5
    ) -> list[dict[str, Any]]:
        """Rutas de influencia a N saltos (SPEC-01 §5.1)."""
        hops = int(max_hops)
        if not 1 <= hops <= 10:
            raise ValueError("max_hops debe estar entre 1 y 10 (SPEC-01 §5.1).")
        if not 0.0 <= float(min_conf) <= 1.0:
            raise ValueError("min_conf debe estar en [0.0, 1.0].")
        cypher = self.PATHS_OF_INFLUENCE_TEMPLATE.format(max_hops=hops)
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(cypher, origin_id=origin_id, min_conf=float(min_conf)))
            )
        return [dict(record) for record in records]

    def critical_brokers(
        self, min_hops: int = 2, max_hops: int = 4, limit: int = 25
    ) -> list[dict[str, Any]]:
        """Intermediarios críticos de la red (SPEC-01 §5.2)."""
        lo, hi = int(min_hops), int(max_hops)
        if not 1 <= lo <= hi <= 10:
            raise ValueError("Se requiere 1 <= min_hops <= max_hops <= 10.")
        cypher = self.CRITICAL_BROKERS_TEMPLATE.format(min_hops=lo, max_hops=hi)
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(cypher, limit=int(limit)))
            )
        return [dict(record) for record in records]

    def assets_with_evidence(self, asset_value: str) -> list[dict[str, Any]]:
        """Búsqueda por metadatos + evidencia (SPEC-01 §5.3)."""
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(self.ASSETS_WITH_EVIDENCE_CYPHER, asset_value=asset_value)
                )
            )
        return [dict(record) for record in records]

    def list_coherence_inbox(self) -> list[dict[str, Any]]:
        """Bandeja de coherencia (SPEC-01 §5.4/§8): alertas pendientes."""
        return self.list_alerts(status="PENDING_ANALYST")

    # -----------------------------------------------------------------
    # Nodos analíticos — CRUD (SPEC-01 §2 · consumido por Agentes 2 y 3)
    # -----------------------------------------------------------------
    def _create_node(
        self, label: str, node_id: str, props: dict[str, Any]
    ) -> dict[str, Any]:
        """MERGE idempotente por ``id`` de un nodo analítico/documental."""
        self._validate_label(label)
        cypher = self.CREATE_NODE_TEMPLATE.format(label=label)
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(cypher, id=node_id, props=dict(props)).consume()
            )
        return {"id": node_id, **props}

    # -- Analysis ------------------------------------------------------
    def create_analysis(
        self,
        title: str,
        thesis_summary: str,
        *,
        code: Optional[str] = None,
        author_analyst_id: Optional[str] = None,
        conclusion_body: Optional[str] = None,
        status: str = "ACTIVE",
        analysis_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Crea un ``Analysis`` (SPEC-01 §2; ``POST /analyses``)."""
        self._validate_choice("status", status, ANALYSIS_STATUSES)
        now = self._now()
        node_id = analysis_id or str(uuid.uuid4())
        props: dict[str, Any] = {
            "code": code or f"AN-{uuid.uuid4().hex[:8].upper()}",
            "title": title,
            "thesis_summary": thesis_summary,
            "conclusion_body": conclusion_body,
            "status": status,
            "author_analyst_id": author_analyst_id,
            "coherence_score": 0.0,
            "created_at": now,
            "updated_at": now,
        }
        props.update(dict(extra or {}))
        return self._create_node("Analysis", node_id, props)

    def get_analysis(self, analysis_id: str) -> Optional[dict[str, Any]]:
        """Ficha de un ``Analysis`` o ``None`` (SPEC-01 §2)."""
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    self.GET_ANALYSIS_CYPHER, analysis_id=analysis_id
                ).single()
            )
        return dict(record["node"]) if record else None

    def update_analysis(
        self, analysis_id: str, patch: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Edita tesis/conclusión/estado (autoridad humana; SPEC-01 §2)."""
        allowed = ("title", "thesis_summary", "conclusion_body", "status")
        clean = {k: v for k, v in dict(patch or {}).items() if k in allowed}
        if "status" in clean:
            self._validate_choice("status", clean["status"], ANALYSIS_STATUSES)
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    self.UPDATE_ANALYSIS_CYPHER,
                    analysis_id=analysis_id,
                    patch=clean,
                    updated_at=self._now(),
                ).single()
            )
        return dict(record["node"]) if record else None

    def list_analyses(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        """Lista ``Analysis`` filtrables por estado (SPEC-01 §2)."""
        if status is not None:
            self._validate_choice("status", status, ANALYSIS_STATUSES)
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(self.LIST_ANALYSES_CYPHER, status=status))
            )
        return [dict(record["node"]) for record in records]

    def active_analyses(self, keywords: list[str]) -> list[dict[str, Any]]:
        """Pre-Flight §8: ``Analysis`` activos que tocan las palabras clave."""
        clean = [str(k).strip() for k in (keywords or []) if str(k).strip()]
        if not clean:
            return []
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(self.ACTIVE_ANALYSES_CYPHER, keywords=clean))
            )
        return [dict(record) for record in records]

    # -- IntelRequirement ---------------------------------------------
    def create_requirement(
        self,
        question: str,
        *,
        priority: str = "MEDIUM",
        status: str = "OPEN",
        parent_analysis_id: Optional[str] = None,
        target_entity_id: Optional[str] = None,
        requirement_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Crea un ``IntelRequirement`` y lo vincula a su ``Analysis`` (SPEC-01 §2)."""
        self._validate_choice("priority", priority, REQUIREMENT_PRIORITIES)
        self._validate_choice("status", status, REQUIREMENT_STATUSES)
        now = self._now()
        node_id = requirement_id or str(uuid.uuid4())
        props: dict[str, Any] = {
            "question": question,
            "priority": priority,
            "status": status,
            "parent_analysis_id": parent_analysis_id,
            "target_entity_id": target_entity_id,
            "created_at": now,
        }
        self._create_node("IntelRequirement", node_id, props)
        if parent_analysis_id:
            with self._session() as session:
                session.execute_write(
                    lambda tx: tx.run(
                        self.HAS_REQUIREMENT_CYPHER,
                        analysis_id=parent_analysis_id,
                        requirement_id=node_id,
                        added_at=now,
                    ).consume()
                )
        return {"id": node_id, **props}

    def get_requirement(self, requirement_id: str) -> Optional[dict[str, Any]]:
        """Ficha de un ``IntelRequirement`` o ``None`` (SPEC-01 §2)."""
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    self.GET_REQUIREMENT_CYPHER, requirement_id=requirement_id
                ).single()
            )
        return dict(record["node"]) if record else None

    def list_requirements(self, analysis_id: str) -> list[dict[str, Any]]:
        """RFI vinculados a un ``Analysis`` (SPEC-01 §2 · contrato §6).

        Autorizado por: SPEC-03 §6 (``GET /analyses/{id}/requirements``). La
        pertenencia se resuelve por la arista ``HAS_REQUIREMENT`` o por la FK
        ``parent_analysis_id`` (ambas escritas por :meth:`create_requirement`).
        """
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(self.LIST_REQUIREMENTS_CYPHER, analysis_id=analysis_id)
                )
            )
        return [dict(record["node"]) for record in records]

    def list_open_requirements(self) -> list[dict[str, Any]]:
        """Pre-Flight §8: ``IntelRequirement`` con ``status: OPEN``."""
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(self.LIST_OPEN_REQUIREMENTS_CYPHER))
            )
        return [dict(record["node"]) for record in records]

    # -- CoherenceAlert ------------------------------------------------
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
        """Crea un ``CoherenceAlert`` y lo conecta (SPEC-01 §2/§5.4).

        ``RAISED_FOR`` → el ``Analysis`` indicado (o los que ``INVESTIGATES`` la
        entidad); ``ABOUT`` → la entidad afectada.
        """
        self._validate_choice("alert_type", alert_type, ALERT_TYPES)
        self._validate_choice("status", status, ALERT_STATUSES)
        now = self._now()
        node_id = alert_id or str(uuid.uuid4())
        props: dict[str, Any] = {
            "alert_type": alert_type,
            "status": status,
            "description": description,
            "created_at": now,
        }
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    self.CREATE_ALERT_CYPHER,
                    alert_id=node_id,
                    alert_type=alert_type,
                    status=status,
                    description=description,
                    created_at=now,
                ).consume()
            )
            if analysis_id:
                session.execute_write(
                    lambda tx: tx.run(
                        self.RAISE_FOR_ANALYSIS_CYPHER,
                        alert_id=node_id,
                        analysis_id=analysis_id,
                        raised_at=now,
                    ).consume()
                )
            elif entity_id:
                session.execute_write(
                    lambda tx: tx.run(
                        self.RAISE_FOR_INVESTIGATORS_CYPHER,
                        alert_id=node_id,
                        entity_id=entity_id,
                        raised_at=now,
                    ).consume()
                )
            if entity_id:
                session.execute_write(
                    lambda tx: tx.run(
                        self.ABOUT_ENTITY_CYPHER,
                        alert_id=node_id,
                        entity_id=entity_id,
                        raised_at=now,
                    ).consume()
                )
        return self._alert_dict(
            {"id": node_id, **props},
            analysis_id=analysis_id,
            entity_id=entity_id,
        )

    def list_alerts(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        """Bandeja de coherencia (SPEC-01 §5.4): alertas, filtrables por estado."""
        if status is not None:
            self._validate_choice("status", status, ALERT_STATUSES)
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(self.LIST_ALERTS_CYPHER, status=status))
            )
        return [
            self._alert_dict(
                record.get("node"),
                analysis_id=record.get("analysis_id"),
                entity_id=record.get("entity_id"),
            )
            for record in records
        ]

    def resolve_alert(
        self, alert_id: str, status: str, note: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        """Resuelve o descarta una alerta (SPEC-01 §5.4; ``.../resolve``)."""
        self._validate_choice("status", status, ("RESOLVED", "DISMISSED"))
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    self.RESOLVE_ALERT_CYPHER,
                    alert_id=alert_id,
                    status=status,
                    note=note,
                ).single()
            )
        if record is None:
            return None
        alert = self._alert_dict(record.get("node"))
        alert["status"] = status
        alert["resolution_note"] = note
        return alert

    # -----------------------------------------------------------------
    # Escritores de coherencia (SPEC-01 §3.2 · Coherence Engine, Agente 2)
    # -----------------------------------------------------------------
    def corroborate(
        self,
        report_id: str,
        analysis_id: str,
        *,
        confidence: float,
        justification: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        """``(Report)-[:CORROBORATES]->(Analysis)`` + ``coherence_score`` (SPEC-01 §3.2).

        Persiste la trazabilidad doble (``source_report_id``, ``exact_quote``,
        ``text_evidence_span`` y ``page_number`` cuando se aporta). **Nunca** toca
        ``conclusion_body`` (Constitución, Regla 7).
        """
        conf = float(confidence)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence debe estar en [0.0, 1.0].")
        provenance = self._coherence_provenance(exact_quote, page_number)
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    self.CORROBORATE_CYPHER,
                    report_id=report_id,
                    analysis_id=analysis_id,
                    confidence=conf,
                    justification=justification,
                    **provenance,
                ).consume()
            )
        return {
            "type": "CORROBORATES",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "confidence": conf,
            "justification": justification,
            "exact_quote": exact_quote,
            "text_evidence_span": provenance["text_evidence_span"],
            "page_number": provenance["page_number"],
        }

    def contradict(
        self,
        report_id: str,
        analysis_id: str,
        *,
        conflict_detail: str,
        severity: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
        entity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """``[:CONTRADICTS]`` + ``CoherenceAlert`` CONTRADICTION + ``[:RAISED_FOR]``.

        Persiste la trazabilidad doble en la arista. **No** altera la tesis humana
        (``conclusion_body`` queda intacto; Constitución, Reglas 7 y 9).
        """
        self._validate_choice("severity", severity, SEVERITIES)
        alert_id = str(uuid.uuid4())
        now = self._now()
        provenance = self._coherence_provenance(exact_quote, page_number)
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    self.CONTRADICT_CYPHER,
                    report_id=report_id,
                    analysis_id=analysis_id,
                    conflict_detail=conflict_detail,
                    severity=severity,
                    entity_id=entity_id,
                    alert_id=alert_id,
                    created_at=now,
                    **provenance,
                ).consume()
            )
        return self._alert_dict(
            {
                "id": alert_id,
                "alert_type": "CONTRADICTION",
                "status": "PENDING_ANALYST",
                "description": conflict_detail,
                "created_at": now,
                "source_report_id": report_id,
                "exact_quote": exact_quote,
                "text_evidence_span": provenance["text_evidence_span"],
                "page_number": provenance["page_number"],
            },
            analysis_id=analysis_id,
            entity_id=entity_id,
        )

    def extend_analysis(
        self,
        report_id: str,
        analysis_id: str,
        *,
        new_angle_summary: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        """``(Report)-[:EXTENDS]->(Analysis)`` (SPEC-01 §3.2) con trazabilidad doble."""
        provenance = self._coherence_provenance(exact_quote, page_number)
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    self.EXTEND_CYPHER,
                    report_id=report_id,
                    analysis_id=analysis_id,
                    new_angle_summary=new_angle_summary,
                    **provenance,
                ).consume()
            )
        return {
            "type": "EXTENDS",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "new_angle_summary": new_angle_summary,
            "exact_quote": exact_quote,
            "text_evidence_span": provenance["text_evidence_span"],
            "page_number": provenance["page_number"],
        }

    def answer_requirement(
        self,
        report_id: str,
        requirement_id: str,
        *,
        answer_summary: str,
        confidence: float,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        """``(Report)-[:ANSWERS_REQUIREMENT]->(IntelRequirement)`` (marca ANSWERED)."""
        conf = float(confidence)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence debe estar en [0.0, 1.0].")
        provenance = self._coherence_provenance(exact_quote, page_number)
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    self.ANSWER_REQUIREMENT_CYPHER,
                    report_id=report_id,
                    requirement_id=requirement_id,
                    answer_summary=answer_summary,
                    confidence=conf,
                    **provenance,
                ).consume()
            )
        return {
            "type": "ANSWERS_REQUIREMENT",
            "report_id": report_id,
            "source_report_id": report_id,
            "requirement_id": requirement_id,
            "answer_summary": answer_summary,
            "confidence": conf,
            "status": "ANSWERED",
            "exact_quote": exact_quote,
            "text_evidence_span": provenance["text_evidence_span"],
            "page_number": provenance["page_number"],
        }

    def cite_analysis(
        self,
        report_id: str,
        analysis_id: str,
        *,
        relevance_score: float,
        rationale: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        """``(Report)-[:CITES_ANALYSIS]->(Analysis)`` (SPEC-01 §3.2; contrato §6.7)."""
        score = float(relevance_score)
        if not 0.0 <= score <= 1.0:
            raise ValueError("relevance_score debe estar en [0.0, 1.0].")
        provenance = self._coherence_provenance(exact_quote, page_number)
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    self.CITE_ANALYSIS_CYPHER,
                    report_id=report_id,
                    analysis_id=analysis_id,
                    relevance_score=score,
                    rationale=rationale,
                    **provenance,
                ).consume()
            )
        return {
            "type": "CITES_ANALYSIS",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "relevance_score": score,
            "rationale": rationale,
            "exact_quote": exact_quote,
            "text_evidence_span": provenance["text_evidence_span"],
            "page_number": provenance["page_number"],
        }

    def link_analysis_to_entity(
        self, analysis_id: str, entity_id: str
    ) -> dict[str, Any]:
        """``(Analysis)-[:INVESTIGATES]->(entidad)`` (SPEC-01 §3.2; contrato §6.1)."""
        added_at = self._now()
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(
                    self.LINK_ANALYSIS_TO_ENTITY_CYPHER,
                    analysis_id=analysis_id,
                    entity_id=entity_id,
                    added_at=added_at,
                ).consume()
            )
        return {
            "type": "INVESTIGATES",
            "analysis_id": analysis_id,
            "entity_id": entity_id,
            "added_at": added_at,
        }

    # -----------------------------------------------------------------
    # Pre-Flight (SPEC-02 §1 Fase 0 · contrato §8)
    # -----------------------------------------------------------------
    def canonical_entities(self) -> list[dict[str, Any]]:
        """Diccionario de entidades canónicas ``{id, label, canonical_name}``."""
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(self.CANONICAL_ENTITIES_CYPHER))
            )
        return [
            {
                "id": str(record.get("id") or ""),
                "label": record.get("label"),
                "canonical_name": record.get("canonical_name"),
            }
            for record in records
        ]

    # -----------------------------------------------------------------
    # Lectura del grafo (SPEC-03 §3) — consumido por GraphGateway
    # -----------------------------------------------------------------
    def list_nodes(
        self,
        *,
        label: Optional[str] = None,
        q: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        min_confidence: Optional[float] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        """Lista/filtra nodos; ``q`` usa el índice full-text ``entity_names_ft``.

        Autorizado por: SPEC-03 §3 (``listGraphNodes``) y SPEC-01 §6.
        """
        if label is not None:
            self._validate_label(label)
        confidence = self._optional_confidence(min_confidence)
        capped = max(1, min(int(limit), 500))
        with self._session() as session:
            if q:
                records = session.execute_read(
                    lambda tx: list(
                        tx.run(
                            self.LIST_NODES_FULLTEXT_CYPHER,
                            query=_escape_lucene(str(q)),
                            label=label,
                            limit=capped,
                        )
                    )
                )
                items = [
                    projected
                    for projected in (self._project_record_node(r) for r in records)
                    if projected is not None
                ]
                return {"items": items, "next_cursor": None}
            records = session.execute_read(
                lambda tx: list(
                    tx.run(
                        self.LIST_NODES_CYPHER,
                        label=label,
                        cursor=cursor,
                        min_confidence=confidence,
                        from_time=from_,
                        to_time=to,
                        limit=capped,
                    )
                )
            )
        items = [
            projected
            for projected in (self._project_record_node(r) for r in records)
            if projected is not None
        ]
        next_cursor = items[-1]["id"] if len(items) == capped and items else None
        return {"items": items, "next_cursor": next_cursor}

    def get_node(self, node_id: str) -> Optional[dict[str, Any]]:
        """Ficha completa de un nodo o ``None`` si no existe (SPEC-03 §3)."""
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(self.GET_NODE_CYPHER, node_id=node_id).single()
            )
        return self._project_record_node(record)

    def update_node(
        self, node_id: str, patch: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        """Edita/fusiona un nodo (SPEC-03 §3, ``updateGraphNode``).

        ``patch`` admite ``canonical_name``, ``aliases`` (fusión de alias sin
        duplicar), ``attributes`` y ``merge_with`` (lista de ids duplicados que
        se reasignan al canónico vía :meth:`merge_duplicates`).

        :raises NodeLockedError: si el nodo tiene ``locked_by_analyst: true``
            (Constitución, Regla 2; la API lo traduce a ``409``).
        """
        self._ensure_unlocked(node_id)
        patch = dict(patch or {})
        merge_with = patch.pop("merge_with", None) or []
        canonical_name = patch.pop("canonical_name", None)
        aliases = list(patch.pop("aliases", None) or [])
        attributes = {
            key: value
            for key, value in dict(patch.pop("attributes", None) or {}).items()
            if key not in ("id", "label")
        }
        for duplicate in merge_with:
            duplicate_id = str(duplicate)
            if duplicate_id and duplicate_id != node_id:
                self.merge_duplicates(node_id, duplicate_id)
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    self.UPDATE_NODE_CYPHER,
                    node_id=node_id,
                    canonical_name=canonical_name,
                    aliases=aliases,
                    attributes=attributes,
                ).single()
            )
        return self._project_record_node(record)

    def delete_node(self, node_id: str) -> None:
        """Borra un nodo y sus aristas con ``DETACH DELETE`` (SPEC-03 §3).

        :raises NodeLockedError: si el nodo tiene ``locked_by_analyst: true``.
        """
        self._ensure_unlocked(node_id)
        with self._session() as session:
            session.execute_write(
                lambda tx: tx.run(self.DELETE_NODE_BY_ID_CYPHER, node_id=node_id).consume()
            )

    def list_edges(
        self, *, min_confidence: Optional[float] = None, limit: int = 50
    ) -> dict[str, Any]:
        """Lista/filtra aristas conservando su procedencia (SPEC-03 §3)."""
        confidence = self._optional_confidence(min_confidence)
        capped = max(1, min(int(limit), 500))
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(self.LIST_EDGES_CYPHER, min_confidence=confidence, limit=capped)
                )
            )
        return {"items": [self._project_edge(r) for r in records]}

    def neighborhood(self, node_id: str, depth: int = 1) -> dict[str, Any]:
        """Vecindad a ``depth`` saltos (1..3) con nodos y aristas (SPEC-03 §6).

        Doble uso: ``getGraphNeighborhood`` (SPEC-03 §3) y la interfaz de lectura
        del motor GraphRAG (SPEC-03 §5, B5).
        """
        hops = int(depth)
        if not 1 <= hops <= 3:
            raise ValueError("depth debe estar en 1..3 (SPEC-03 §6).")
        nodes_cypher = self.NEIGHBORHOOD_NODES_TEMPLATE.format(depth=hops)
        edges_cypher = self.NEIGHBORHOOD_EDGES_TEMPLATE.format(depth=hops)
        with self._session() as session:
            node_records, edge_records = session.execute_read(
                lambda tx: (
                    list(tx.run(nodes_cypher, node_id=node_id)),
                    list(tx.run(edges_cypher, node_id=node_id)),
                )
            )
        nodes: list[dict[str, Any]] = []
        for record in node_records:
            props = dict(record.get("props") or {})
            props.setdefault("id", record.get("id"))
            nodes.append(self._project_node(props, record.get("label")))
        return {
            "nodes": nodes,
            "edges": [self._project_edge(r) for r in edge_records],
        }

    def search_nodes(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Adaptador de la interfaz de lectura de GraphRAG (SPEC-03 §5, B5)."""
        term = _escape_lucene(str(query or ""))
        if not term:
            return []
        capped = max(1, min(int(limit), 500))
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(
                    tx.run(self.SEARCH_NODES_CYPHER, query=term, limit=capped)
                )
            )
        return [
            projected
            for projected in (self._project_record_node(r) for r in records)
            if projected is not None
        ]

    # -----------------------------------------------------------------
    # Lectura de informes (SPEC-03 §4) — consumido por GraphGateway
    # -----------------------------------------------------------------
    def get_report(self, report_id: str) -> Optional[dict[str, Any]]:
        """Metadatos del informe o ``None`` si no existe (SPEC-03 §4)."""
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(self.GET_REPORT_CYPHER, report_id=report_id).single()
            )
        if record is None:
            return None
        return {
            "id": str(record.get("id") or report_id),
            "title": record.get("title"),
            "file_path": record.get("file_path"),
            "ingested_at": record.get("ingested_at"),
            "author_id": record.get("author_id"),
        }

    def get_report_file_path(self, report_id: str) -> Optional[str]:
        """Ruta del PDF del informe o ``None`` (SPEC-03 §4 / SPEC-04 §4)."""
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    self.GET_REPORT_FILE_PATH_CYPHER, report_id=report_id
                ).single()
            )
        return record.get("file_path") if record is not None else None

    def get_report_chunks(self, report_id: str) -> dict[str, Any]:
        """Chunks con coordenadas del informe (SPEC-03 §4).

        Se leen de la propiedad ``chunks`` del nodo ``Report`` (lista de mapas
        con ``chunk_id``/``page_number``/``char_start``/``char_end``/``text``).
        """
        with self._session() as session:
            record = session.execute_read(
                lambda tx: tx.run(
                    self.GET_REPORT_CHUNKS_CYPHER, report_id=report_id
                ).single()
            )
        raw_chunks = list(record.get("chunks") or []) if record is not None else []
        return {
            "items": [
                self._normalize_chunk(chunk, index)
                for index, chunk in enumerate(raw_chunks)
            ]
        }

    @staticmethod
    def _optional_confidence(value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        confidence = float(value)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("min_confidence debe estar en [0.0, 1.0].")
        return confidence

    # -----------------------------------------------------------------
    # Apoyo a Entity Resolution (§4)
    # -----------------------------------------------------------------
    def list_relationships(self, entity_id: str) -> list[dict[str, Any]]:
        """Aristas entrantes y salientes de un nodo (para recalcular riesgo)."""
        with self._session() as session:
            records = session.execute_read(
                lambda tx: list(tx.run(self.LIST_RELATIONSHIPS_CYPHER, id=entity_id))
            )
        return [dict(record) for record in records]

    def set_risk_score(self, entity_id: str, risk_score: float) -> Optional[float]:
        """Fija ``risk_score`` de forma idempotente (SPEC-01 §1/§4)."""
        with self._session() as session:
            record = session.execute_write(
                lambda tx: tx.run(
                    self.SET_RISK_SCORE_CYPHER, id=entity_id, risk_score=float(risk_score)
                ).single()
            )
        return record["risk_score"] if record else None


__all__ = [
    "GraphRepository",
    "NodeLockedError",
    "ALLOWED_LABELS",
    "OSINT_ENTITY_LABELS",
    "ALLOWED_RELATION_TYPES",
    "OSINT_RELATION_TYPES",
    "COHERENCE_RELATION_TYPES",
    "ALLOWED_UNIQUE_KEYS",
    "REQUIRED_RELATION_PROPS",
    "ANALYSIS_STATUSES",
    "REQUIREMENT_PRIORITIES",
    "REQUIREMENT_STATUSES",
    "ALERT_TYPES",
    "ALERT_STATUSES",
    "SEVERITIES",
    "INFLUENCE_RELATION_TYPES",
    "FULLTEXT_INDEX",
]
