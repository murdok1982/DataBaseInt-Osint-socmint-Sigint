# SPEC-01: Ontología del Grafo, Coherencia y Resolución de Entidades (v2.0)

> Spec rectora del **Agente 1 (Arquitecto de Grafos)**.
> Contrato derivado: `/contracts/graph-constraints.cypher`.
> Tecnologías: Neo4j 5 / Memgraph, Cypher, APOC, GDS.

## 0. Cambio de ontología v1.x → v2.0 (breaking, deliberado)

La ontología v2.0 **consolida** las entidades OSINT en **5 tipos**:

- Se **retiran** `Device` y `FinancialAccount`.
- `CyberAsset` absorbe ambos: `asset_type ∈ {IP, DOMAIN, EMAIL, CRYPTO_WALLET, PHONE}`.
- Se retiran las relaciones `OWNS_DEVICE` y `HOLDS_ACCOUNT` (su semántica cae en
  `USES_ASSET` sobre un `CyberAsset` de tipo `PHONE`/`CRYPTO_WALLET`).
- Se **añaden** los nodos analíticos `Analysis`, `IntelRequirement`, `CoherenceAlert`.

> Requisito previo preservado: trazabilidad, entity resolution, umbral, `risk_score`,
> `max_hops` y claves únicas siguen vigentes (ver §4).

## 1. Nodos OSINT (5 labels)

| Label | Concepto (ES) | Propiedades |
| :--- | :--- | :--- |
| `Person` | Persona | `id: UUID`, `canonical_name: String`, `aliases: List[String]`, `risk_score: Float`, `locked_by_analyst: Boolean` |
| `Organization` | Organización | `id: UUID`, `canonical_name: String`, `aliases: List[String]`, `registration_id: String?`, `country: String?`, `risk_score: Float`, `locked_by_analyst: Boolean` |
| `CyberAsset` | Ciberactivo | `id: UUID`, `asset_type: Enum[IP, DOMAIN, EMAIL, CRYPTO_WALLET, PHONE]`, `value: String`, `locked_by_analyst: Boolean` |
| `Location` | Ubicación | `id: UUID`, `name: String`, `country_code: String?`, `coordinates: Point?` |
| `Event` | Evento | `id: UUID`, `title: String`, `event_date: DateTime`, `category: String?` |

**No consolidables:** `Report`, `Analysis`, `IntelRequirement`, `CoherenceAlert`
(§2) y los nodos de plataforma `User`/`ApiKey` (SPEC-04 §5).

**`locked_by_analyst`** es la marca de **inmutabilidad analítica** (Regla 2). Una
entidad marcada solo admite *nuevas evidencias* o *alertas*; nunca edición
automática de atributos clave ni borrado.

## 2. Nodos documentales y analíticos (clave de coherencia)

| Label | Propiedades |
| :--- | :--- |
| `Report` | `id: UUID`, `title: String`, `source_type: Enum[OSINT_PDF, NEWS, TELEGRAM, RSS, MANUAL, PUBLIC_REGISTRY]`, `file_path: String`, `ingested_by_role: Enum[ANALYST, AI_INFORMANT]`, `ingested_at: DateTime`, `reliability_rating: Enum[A,B,C,D,E,F]` |
| `Analysis` | `id: UUID`, `code: String`, `title: String`, `thesis_summary: Text`, `conclusion_body: Text`, `status: Enum[ACTIVE, UNDER_REVIEW, CLOSED]`, `author_analyst_id: UUID`, `coherence_score: Float`, `created_at: DateTime`, `updated_at: DateTime` |
| `IntelRequirement` | `id: UUID`, `question: String`, `priority: Enum[CRITICAL, HIGH, MEDIUM, LOW]`, `status: Enum[OPEN, ANSWERED]`, `parent_analysis_id: UUID?`, `target_entity_id: UUID?` |
| `CoherenceAlert` | `id: UUID`, `alert_type: Enum[CONTRADICTION, HIGH_VALUE_CORROBORATION, NEW_ALIAS_MERGE]`, `status: Enum[PENDING_ANALYST, RESOLVED, DISMISSED]`, `description: Text`, `created_at: DateTime` |

## 3. Relaciones

> **Convención:** tipo Cypher canónico **ASCII en mayúsculas**. La columna ES es
> la equivalencia funcional usada por la UI.

### 3.1 Red OSINT (7)

| Tipo Cypher | Concepto (ES) | Dirección |
| :--- | :--- | :--- |
| `CONTROLS` | `CONTROLADO_POR` (inv.) | `(Person)-[:CONTROLS]->(Organization)` |
| `FINANCED` | `FINANCIÓ_A` | `(Person\|Organization)-[:FINANCED]->(Person\|Organization)` |
| `USES_ASSET` | `USA_ACTIVO` | `(Person\|Organization)-[:USES_ASSET]->(CyberAsset)` |
| `LOCATED_AT` | `UBICADO_EN` | `(Person\|Organization\|Event)-[:LOCATED_AT]->(Location)` |
| `ASSOCIATED_WITH` | `ASOCIADO_CON` | `(Person)-[:ASSOCIATED_WITH]->(Person)` |
| `PARTICIPATED_IN` | `PARTICIPÓ_EN` | `(Person\|Organization)-[:PARTICIPATED_IN]->(Event)` |
| `MENTIONED_IN` | `MENCIONADO_EN` | `(Entity)-[:MENTIONED_IN]->(Report)` |

**Toda relación OSINT DEBE incluir** (Regla 4, trazabilidad doble):
`confidence` (0.0–1.0), `source_report_id` (UUID), `exact_quote` (String),
`page_number` (Integer ≥ 1), `first_seen` (DateTime), `last_seen` (DateTime),
`locked_by_analyst` (Boolean, por defecto `false`).

### 3.2 Coherencia y análisis (9)

| Tipo Cypher | Dirección | Propiedades de la arista |
| :--- | :--- | :--- |
| `INVESTIGATES` | `(Analysis)->(Person\|Organization\|CyberAsset\|Event)` | `added_at` |
| `HAS_REQUIREMENT` | `(Analysis)->(IntelRequirement)` | `added_at` |
| `CITES_ANALYSIS` | `(Report)->(Analysis)` | `relevance_score: Float`, `rationale: String` |
| `CORROBORATES` | `(Report)->(Analysis)` | `confidence: Float`, `justification: String`, `verified_by_human: Boolean` |
| `CONTRADICTS` | `(Report)->(Analysis)` | `conflict_detail: String`, `severity: Enum[LOW,MEDIUM,CRITICAL]`, `resolved: Boolean` |
| `EXTENDS` | `(Report)->(Analysis)` | `new_angle_summary: String` |
| `ANSWERS_REQUIREMENT` | `(Report)->(IntelRequirement)` | `answer_summary: String`, `confidence: Float` |
| `RAISED_FOR` | `(CoherenceAlert)->(Analysis)` | `raised_at` |
| `ABOUT` | `(CoherenceAlert)->(Entity)` | `raised_at` |

Las aristas analíticas llevan además `source_report_id` + `exact_quote` y, cuando el
vínculo lo permita, `page_number` (el contrato `analytical_context_links` aporta
`exact_quote`; `page_number` se deriva de la tripleta cuya cita coincide, si
existe) — Regla 4. `text_evidence_span` replica `exact_quote`.

## 4. Motor de "Rehacer Relaciones" (Entity Resolution + Recálculo)

Al recibir una entidad extraída:

1. **Normalizar** (lowercase, sin acentos) y **expandir/comparar iniciales**
   (`"J."` ↔ `"Juan"`; las iniciales **no se eliminan**).
2. **Match exacto** por clave única (§4.2).
3. **Match difuso** por similitud de embeddings + **Jaro-Winkler** sobre
   `canonical_name`/`aliases` (full-text index `entity_names_ft`).
4. **Decisión y protección** (Regla 2):

| Caso | Acción |
| :--- | :--- |
| `score ≥ 0.88`, sin match, `locked_by_analyst = false` | **Fusionar** (`apoc.refactor.mergeNodes`), conservar el `id` más antiguo, añadir el nombre nuevo a `aliases`, reconectar todas las aristas entrantes/salientes y **recalcular `risk_score`** |
| Coincide, `locked_by_analyst = true` | **No** alterar `canonical_name` ni atributos clave. Enlazar las nuevas aristas al nodo existente. Si un atributo clave **difiere**, crear un `CoherenceAlert` (`NEW_ALIAS_MERGE` o `CONTRADICTION`) conectado al `Analysis` correspondiente |
| `0.70 ≤ score < 0.88` | `PENDING_HUMAN_REVIEW` (no impacta el grafo) |
| `score < 0.70` | Crear nodo nuevo |

### 4.1 Cálculo de `risk_score` (función pura, idempotente)
Aplica a `Person` y `Organization`: `risk_score = min(1.0, Σ confidence_i · weight(rel_type_i))`

| Relación | Peso |
| :--- | :--- |
| `CONTROLS` | 1.0 |
| `FINANCED` | 0.9 |
| `USES_ASSET` | 0.7 |
| `ASSOCIATED_WITH` | 0.5 |
| `PARTICIPATED_IN` | 0.4 |
| `LOCATED_AT` | 0.2 |
| `MENTIONED_IN` | 0.05 |

### 4.2 Claves únicas por label (match exacto)

| Label | Clave(s) única(s) |
| :--- | :--- |
| `CyberAsset` | `value` |
| `Organization` | `registration_id` (y `id`) |
| `Person` | nombre normalizado (`canonical_name`/`aliases`) o `id` |
| `Location` | nombre normalizado o `id` |
| `Event` | `id` |
| `Report` | `id` |
| `Analysis` | `id` (+ `code` único) |
| `IntelRequirement` | `id` |
| `CoherenceAlert` | `id` |
| `User` | `username` (`id`) — plataforma, SPEC-04 |
| `ApiKey` | `key_hash` (`id`) — plataforma, SPEC-04 |

## 5. Consultas de Inteligencia Clave

**5.1 Rutas de influencia a N saltos** — `max_hops` (entero ∈ [1,10], validado e
**interpolado**, único uso permitido; Neo4j 5 no admite parámetro en `*1..$n`).

**5.2 Intermediarios críticos** — `(Person)-[:ASSOCIATED_WITH*2..4]-(Person)`, grado de cruce.

**5.3 Búsqueda por metadatos + evidencia** — por `CyberAsset.value` con evidencia y confianza.

**5.4 Bandeja de coherencia** (nueva v2.0):
```cypher
MATCH (al:CoherenceAlert {status: 'PENDING_ANALYST'})-[:RAISED_FOR]->(a:Analysis)
OPTIONAL MATCH (al)-[:ABOUT]->(e)
RETURN al, a.code AS analisis, labels(e)[0] AS entidad, e.canonical_name AS nombre
ORDER BY al.created_at DESC;
```

## 6. Indexación

- **Unicidad** por `id` en los 11 labels + `Organization.registration_id`,
  `CyberAsset.value`, `Analysis.code`, `User.username`, `ApiKey.key_hash`.
- **Índices** de apoyo: `Person.canonical_name`, `Location.name`, `Report.ingested_at`,
  `Analysis.status`, `IntelRequirement.status`, `CoherenceAlert.status`.
- **Full-text** `entity_names_ft` sobre `canonical_name`/`aliases`/`value`.
- **Vectorial** (uno por label — Neo4j **no** admite índices vectoriales
  multi-label): `person_embedding_idx`, `organization_embedding_idx`,
  `cyberasset_embedding_idx` sobre `canonical_name_embedding` (similitud coseno).
- Evitar `MERGE` sobre propiedades mutables (`aliases`, `risk_score`).

## 7. Criterios de Aceptación
- [ ] Existen los 11 labels y las 16 relaciones como constraint/plantilla.
- [ ] Ningún `MERGE` crea un segundo nodo con identidad equivalente.
- [ ] Toda arista OSINT tiene las propiedades obligatorias de §3.1.
- [ ] `risk_score` es idempotente y sigue §4.1.
- [ ] La fusión de iniciales (`"J. Pérez"` ↔ `"Juan Pérez"`) funciona.
- [ ] Con `locked_by_analyst: true` **ningún** proceso automático altera atributos clave; se crea `CoherenceAlert`.
