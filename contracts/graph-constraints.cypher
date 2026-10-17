// =====================================================================
// CONTRATO: graph-constraints.cypher
// Deriva de: SPEC-01 v2.0 (Ontología, Coherencia, Entity Resolution).
// Autoría inicial: Agente 1  ·  Sella: Agente 0 (Orquestador)
// Estado: SELLADO v2.1.0 — 2026-10-17 (Agente 0). Cambios requieren nuevo sello en SEAL.md.
// v2.0.0: + nodos analíticos (Analysis, IntelRequirement, CoherenceAlert);
//         + relaciones de coherencia; + locked_by_analyst; + nodo ApiKey (M2M);
//         - Device / - FinancialAccount (consolidados en CyberAsset); - OWNS_DEVICE/- HOLDS_ACCOUNT.
// v2.1.0: índice vectorial por label (Neo4j no admite vector multi-label).
// Dialecto: Neo4j 5 (constraints) / Memgraph-compatible. APOC requerido para mergeNodes.
// =====================================================================

// =====================================================================
// 1. UNICIDAD POR id — entidades OSINT + documentales + analíticos
// =====================================================================
CREATE CONSTRAINT person_id_unique       IF NOT EXISTS FOR (n:Person)           REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT organization_id_unique IF NOT EXISTS FOR (n:Organization)     REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT cyberasset_id_unique   IF NOT EXISTS FOR (n:CyberAsset)       REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT location_id_unique     IF NOT EXISTS FOR (n:Location)         REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT event_id_unique        IF NOT EXISTS FOR (n:Event)            REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT report_id_unique       IF NOT EXISTS FOR (n:Report)           REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT analysis_id_unique     IF NOT EXISTS FOR (n:Analysis)         REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT requirement_id_unique  IF NOT EXISTS FOR (n:IntelRequirement) REQUIRE n.id IS UNIQUE;
CREATE CONSTRAINT alert_id_unique        IF NOT EXISTS FOR (n:CoherenceAlert)   REQUIRE n.id IS UNIQUE;

// Claves naturales únicas (match exacto del Entity Resolution)
CREATE CONSTRAINT organization_regid_unique IF NOT EXISTS FOR (n:Organization) REQUIRE n.registration_id IS UNIQUE;
CREATE CONSTRAINT cyberasset_value_unique   IF NOT EXISTS FOR (n:CyberAsset)   REQUIRE n.value IS UNIQUE;
CREATE CONSTRAINT analysis_code_unique      IF NOT EXISTS FOR (n:Analysis)     REQUIRE n.code IS UNIQUE;

// ---- 1.b Nodos de PLATAFORMA (fuera de la ontología OSINT; SPEC-04 §5)
CREATE CONSTRAINT user_id_unique           IF NOT EXISTS FOR (u:User)    REQUIRE u.id IS UNIQUE;
CREATE CONSTRAINT user_username_unique     IF NOT EXISTS FOR (u:User)    REQUIRE u.username IS UNIQUE;
CREATE CONSTRAINT apikey_id_unique         IF NOT EXISTS FOR (k:ApiKey)  REQUIRE k.id IS UNIQUE;
CREATE CONSTRAINT apikey_key_hash_unique   IF NOT EXISTS FOR (k:ApiKey)  REQUIRE k.key_hash IS UNIQUE;

// =====================================================================
// 2. EXISTENCIA DE PROPIEDADES OBLIGATORIAS
// =====================================================================
CREATE CONSTRAINT person_required        IF NOT EXISTS FOR (n:Person)           REQUIRE (n.canonical_name, n.risk_score) IS NOT NULL;
CREATE CONSTRAINT organization_required  IF NOT EXISTS FOR (n:Organization)     REQUIRE n.canonical_name IS NOT NULL;
CREATE CONSTRAINT cyberasset_required    IF NOT EXISTS FOR (n:CyberAsset)       REQUIRE (n.asset_type, n.value) IS NOT NULL;
CREATE CONSTRAINT location_required      IF NOT EXISTS FOR (n:Location)         REQUIRE n.name IS NOT NULL;
CREATE CONSTRAINT event_required         IF NOT EXISTS FOR (n:Event)            REQUIRE (n.title, n.event_date) IS NOT NULL;
CREATE CONSTRAINT report_required        IF NOT EXISTS FOR (n:Report)           REQUIRE (n.file_path, n.ingested_at) IS NOT NULL;
CREATE CONSTRAINT analysis_required      IF NOT EXISTS FOR (n:Analysis)         REQUIRE (n.thesis_summary, n.status) IS NOT NULL;
CREATE CONSTRAINT requirement_required   IF NOT EXISTS FOR (n:IntelRequirement) REQUIRE (n.question, n.status) IS NOT NULL;
CREATE CONSTRAINT alert_required         IF NOT EXISTS FOR (n:CoherenceAlert)   REQUIRE (n.alert_type, n.status) IS NOT NULL;

// =====================================================================
// 3. ÍNDICES SIMPLES DE APOYO
// =====================================================================
CREATE INDEX person_name_idx        IF NOT EXISTS FOR (n:Person)           ON (n.canonical_name);
CREATE INDEX organization_regid_idx IF NOT EXISTS FOR (n:Organization)     ON (n.registration_id);
CREATE INDEX cyberasset_value_idx   IF NOT EXISTS FOR (n:CyberAsset)       ON (n.value);
CREATE INDEX location_name_idx      IF NOT EXISTS FOR (n:Location)         ON (n.name);
CREATE INDEX event_date_idx         IF NOT EXISTS FOR (n:Event)            ON (n.event_date);
CREATE INDEX report_ingested_idx    IF NOT EXISTS FOR (n:Report)           ON (n.ingested_at);
CREATE INDEX analysis_status_idx    IF NOT EXISTS FOR (n:Analysis)         ON (n.status);
CREATE INDEX requirement_status_idx IF NOT EXISTS FOR (n:IntelRequirement) ON (n.status);
CREATE INDEX alert_status_idx       IF NOT EXISTS FOR (n:CoherenceAlert)   ON (n.status);
CREATE INDEX entity_locked_idx      IF NOT EXISTS FOR (n:Person|Organization|CyberAsset) ON (n.locked_by_analyst);

// =====================================================================
// 4. FULL-TEXT + VECTOR (similitud fonética/embeddings >= 0.88)
// =====================================================================
CREATE FULLTEXT INDEX entity_names_ft IF NOT EXISTS
FOR (n:Person|Organization|Location|CyberAsset|Event)
ON EACH [n.canonical_name, n.aliases, n.value, n.name]
OPTIONS { indexConfig: { `fulltext.analyzer`: 'standard-no-stop-words' } };

// Requiere Neo4j 5.11+ (vector index). Neo4j NO admite índices vectoriales
// multi-label, así que se define uno por label.
CREATE VECTOR INDEX person_embedding_idx IF NOT EXISTS
FOR (n:Person) ON (n.canonical_name_embedding)
OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } };

CREATE VECTOR INDEX organization_embedding_idx IF NOT EXISTS
FOR (n:Organization) ON (n.canonical_name_embedding)
OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } };

CREATE VECTOR INDEX cyberasset_embedding_idx IF NOT EXISTS
FOR (n:CyberAsset) ON (n.canonical_name_embedding)
OPTIONS { indexConfig: { `vector.dimensions`: 1536, `vector.similarity_function`: 'cosine' } };

// =====================================================================
// 5. PLANTILLAS DE ESCRITURA PARAMETRIZADA (implementación en app/db)
//    Toda arista OSINT DEBE incluir trazabilidad doble (Regla 4):
//    confidence, source_report_id, exact_quote, page_number,
//    first_seen, last_seen, locked_by_analyst.
//    Relaciones OSINT: CONTROLS, FINANCED, USES_ASSET, LOCATED_AT,
//      ASSOCIATED_WITH, PARTICIPATED_IN, MENTIONED_IN
// =====================================================================

// 5.1 MERGE de nodo con identidad única (no duplica; no pisa nodo bloqueado)
// Params: $id, $canonical_name, $alias, $risk_score
// MERGE (p:Person {id: $id})
//   ON CREATE SET p.canonical_name = $canonical_name, p.aliases = [$alias],
//                 p.risk_score = $risk_score, p.locked_by_analyst = false
//   ON MATCH  SET p.aliases = CASE WHEN p.locked_by_analyst THEN p.aliases
//                                  WHEN $alias IN p.aliases THEN p.aliases
//                                  ELSE p.aliases + $alias END
// RETURN p;

// 5.2 MERGE de arista OSINT con trazabilidad obligatoria (cualquier tipo)
// Params: $from_id, $to_id, $confidence, $report_id, $quote, $page_number, $now
// MATCH (a {id: $from_id}) MATCH (b {id: $to_id})
// MERGE (a)-[r:CONTROLS]->(b)
//   ON CREATE SET r.first_seen = $now, r.last_seen = $now,
//                 r.confidence = $confidence, r.source_report_id = $report_id,
//                 r.exact_quote = $quote, r.text_evidence_span = $quote,
//                 r.page_number = $page_number, r.locked_by_analyst = false
//   ON MATCH  SET r.last_seen = $now,
//                 r.confidence = CASE WHEN r.locked_by_analyst THEN r.confidence
//                                     WHEN $confidence > r.confidence THEN $confidence
//                                     ELSE r.confidence END
// RETURN r;

// 5.3 Vínculo entidad -> informe
// MATCH (e {id: $entity_id}) MATCH (rep:Report {id: $report_id})
// MERGE (e)-[m:MENTIONED_IN]->(rep)
//   ON CREATE SET m.exact_quote = $quote, m.source_report_id = $report_id,
//                 m.page_number = $page_number, m.confidence = $confidence, m.first_seen = $now, m.last_seen = $now
// RETURN m;

// =====================================================================
// 6. RELACIONES DE COHERENCIA Y ANÁLISIS (9)
//    INVESTIGATES, HAS_REQUIREMENT, CITES_ANALYSIS, CORROBORATES,
//    CONTRADICTS, EXTENDS, ANSWERS_REQUIREMENT, RAISED_FOR, ABOUT
// =====================================================================

// 6.1 (Analysis)-[:INVESTIGATES]->(entidad)
// MERGE (a:Analysis {id: $analysis_id})-[:INVESTIGATES {added_at: $now}]->(e {id: $entity_id});

// 6.2 (Analysis)-[:HAS_REQUIREMENT]->(IntelRequirement)
// MERGE (a:Analysis {id: $analysis_id})-[:HAS_REQUIREMENT {added_at: $now}]->(q:IntelRequirement {id: $req_id});

// 6.3 CORROBORATES: incrementa coherence_score; NUNCA toca conclusion_body
// MATCH (rep:Report {id: $report_id}) MATCH (a:Analysis {id: $analysis_id})
// MERGE (rep)-[c:CORROBORATES]->(a)
//   ON CREATE SET c.confidence = $confidence, c.justification = $justification,
//                 c.verified_by_human = false, c.exact_quote = $quote
//   ON MATCH  SET c.confidence = CASE WHEN $confidence > c.confidence THEN $confidence ELSE c.confidence END
// WITH a SET a.coherence_score = min(1.0, coalesce(a.coherence_score, 0) + $confidence);

// 6.4 CONTRADICTS: crea arista + CoherenceAlert (mantiene intacta la tesis humana)
// MATCH (rep:Report {id: $report_id}) MATCH (a:Analysis {id: $analysis_id})
// MERGE (rep)-[x:CONTRADICTS]->(a)
//   ON CREATE SET x.conflict_detail = $detail, x.severity = $severity,
//                 x.resolved = false, x.exact_quote = $quote
// WITH a, rep, $alert_id AS aid
// MERGE (al:CoherenceAlert {id: aid})
//   ON CREATE SET al.alert_type = 'CONTRADICTION', al.status = 'PENDING_ANALYST',
//                 al.description = $detail, al.created_at = $now
// MERGE (al)-[:RAISED_FOR {raised_at: $now}]->(a)
// WITH al MATCH (e {id: $entity_id})
// MERGE (al)-[:ABOUT {raised_at: $now}]->(e);

// 6.5 EXTENDS / 6.6 ANSWERS_REQUIREMENT
// MERGE (rep:Report {id: $report_id})-[:EXTENDS {new_angle_summary: $summary}]->(a:Analysis {id: $analysis_id});
// MERGE (rep:Report {id: $report_id})-[:ANSWERS_REQUIREMENT {answer_summary: $summary, confidence: $confidence}]->(q:IntelRequirement {id: $req_id})
//   ON CREATE SET q.status = 'ANSWERED';
// 6.7 CITES_ANALYSIS: (Report)-[:CITES_ANALYSIS {relevance_score, rationale}]->(Analysis)

// =====================================================================
// 7. ENTITY RESOLUTION CON PROTECCIÓN (Regla 2 — locked_by_analyst)
// =====================================================================

// 7.1 Match exacto / difuso (>= 0.88) y decisión
// Params: $dup_id, $canonical_id
// CASE A — no bloqueado: fusionar (conserva el id más antiguo), unir aliases, recalcular risk_score
// MATCH (dup {id: $dup_id}) MATCH (canon {id: $canonical_id})
// WHERE canons.locked_by_analyst = false
// CALL apoc.refactor.mergeNodes([canon, dup], {properties: {aliases: 'combine'}, mergeRels: true}) YIELD node
// WITH node SET node.risk_score = 0.0   // se recalcula después (SPEC-01 §4.1)
// RETURN node;
//
// CASE B — bloqueado (locked_by_analyst = true): NO fusionar ni sobrescribir.
//   Solo enlazar nuevas aristas; si un atributo clave difiere -> CoherenceAlert(NEW_ALIAS_MERGE)
// MATCH (canon {id: $canonical_id})-[:INVESTIGATES|RAISED_FOR]-(a:Analysis)
// WHERE canon.locked_by_analyst = true
// MERGE (al:CoherenceAlert {id: $alert_id})
//   ON CREATE SET al.alert_type = 'NEW_ALIAS_MERGE', al.status = 'PENDING_ANALYST',
//                 al.description = $detail, al.created_at = $now
// MERGE (al)-[:RAISED_FOR]->(a)
// RETURN al;

// 7.2 Umbral (SPEC-02 §3): < 0.50 => PENDING_HUMAN_REVIEW (no impacta el grafo).
//     0.70..0.88 => PENDING_HUMAN_REVIEW. >= 0.88 => impacto automático (o alerta si bloqueado).

// =====================================================================
// 8. CONSULTA DE PRE-FLIGHT (SPEC-02 Fase 0)
// =====================================================================
// Params: $keywords (lista normalizada)
// MATCH (a:Analysis) WHERE a.status = 'ACTIVE' AND ANY(k IN $keywords WHERE toLower(a.thesis_summary) CONTAINS k)
// RETURN a.id AS id, a.code AS code, a.thesis_summary AS thesis_summary;
// MATCH (q:IntelRequirement {status: 'OPEN'})
// RETURN q.id AS id, q.question AS question, q.priority AS priority;
// MATCH (e:Person|Organization|CyberAsset|Location|Event)
// RETURN e.id AS id, labels(e)[0] AS label, coalesce(e.canonical_name, e.value, e.name) AS canonical_name;
