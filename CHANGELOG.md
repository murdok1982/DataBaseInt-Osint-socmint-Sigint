# Changelog — OSINT Nexus Platform

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El proyecto sigue **Spec-Driven Development (SDD)**: cada versión de contrato va
precedida de su spec y sellada en `contracts/SEAL.md`.

## [v3.0.0-hito1] — 2026-10-23

### Añadido
- **SPEC.md v3.0.0 aprobada**: célula de inteligencia
  `DIRECTOR_CELL`/`HERMES`/`ARACNE`/`ARGOS`/`ATENEA`, doble capa
  ledger + grafo STIX 2.1, Almirantazgo `[A-F][1-6]`, TLP y RLS.
- **`contracts/v3/db-contract.sql`** (DRAFT): PostgreSQL 16 + `pgvector`,
  roles por agente, ledger append-only con SHA-256/cadena de custodia,
  funciones `SECURITY DEFINER`, triggers y vistas `api.*`.
- **`contracts/v3/agent-contracts.json`**: JSON Schema 2020-12 para los
  mensajes inter-agente.
- **`deploy/docker-compose.prod.yml`**: servicio `postgres`
  (`pgvector/pgvector:pg16`) en `internal_intel_net`, sin puerto público,
  healthcheck y volumen `postgres_data`.
- **`PostgresRepository`** (`apps/backend/app/db/postgres_repository.py`):
  subconjunto compatible con `GraphRepository` para ledger y grafo validado.
- **ETL Neo4j → PostgreSQL** (`scripts/migrate/neo4j_to_pg.py`) con
  cuarentena y reconciliación.
- **Tests T-H1-01…T-H1-10** (`apps/backend/tests/v3/db/`) y workflow
  `.github/workflows/v3-db.yml`.
- **ADR-002** (modelo de evidencia y cadena de custodia).

### Pendiente
- Ejecutar los tests con Docker/Testcontainers y sellar los contratos v3.0.0.
- Dual-write + `GRAPH_BACKEND=dual` (Hito 1, entregable 1.9).
- Resolver `DEC-01`…`DEC-08` y `SPEC-GAP-001`…`010` restantes.

## [v2.1.0] — 2026-10-17

### Añadido
- **Contrato `openapi.yaml` v2.1.0**: endpoints `GET /api/v1/analyses`
  (`listAnalyses`, filtro `?status`) y `GET /api/v1/analyses/{id}/requirements`
  (`listAnalysisRequirements`) → **29** `operationId`.
- Consumo de ambos endpoints en el frontend (se elimina el rodeo por
  `GET /graph/nodes?label=Analysis` y se listan los RFI del expediente).
- Endurecimiento `404` en `POST /analyses/{id}/requirements` cuando el
  `Analysis` no existe (SPEC-03 §6 v2.0.1) + 2 tests.

### Corregido
- **`graph-constraints.cypher` v2.1.0**: el índice vectorial era multi-label
  (`FOR (n:Person|Organization|CyberAsset)`), no soportado por Neo4j. Se
  sustituye por uno **por label** (`person_embedding_idx`,
  `organization_embedding_idx`, `cyberasset_embedding_idx`).
- **SPEC-02 §1.7**: difusión de la contradicción al Analista autor aclarada
  (alerta `[:RAISED_FOR]` al `Analysis`; el autor se resuelve por
  `Analysis.author_analyst_id`).

### Despliegue
- Servicio `worker` (Celery) dual-homed (`public_net` + `internal_intel_net`)
  para permitir egreso al proveedor LLM manteniendo DB aislada.
- Plugin **APOC** (`NEO4J_PLUGINS=["apoc"]`) y licencia Neo4j enterprise.
- Runbook `deploy/README.md`, `deploy/scripts/apply-constraints.sh` (idempotente),
  `deploy/scripts/smoke-test.sh`, volumen `reports_data`, webroot ACME.

### Verificación
- Auditoría de trazabilidad SDD: **12/12**.
- Backend **334+ passed** · Informant Agent **36 passed** · frontend compila.
- Test E2E del ciclo simbiótico (`tests/e2e/test_symbiotic_cycle.py`).

## [v2.0.0] — 2026-10-17

### Añadido — Motor de Coherencia Simbiótica (Analista ↔ Informante IA)
- Nodos analíticos `Analysis`, `IntelRequirement`, `CoherenceAlert`; relaciones
  `INVESTIGATES`, `HAS_REQUIREMENT`, `CITES_ANALYSIS`, `CORROBORATES`,
  `CONTRADICTS`, `EXTENDS`, `ANSWERS_REQUIREMENT`, `RAISED_FOR`, `ABOUT`.
- `locked_by_analyst` en entidades y aristas (inmutabilidad analítica).
- Nodo de plataforma `ApiKey` y autenticación M2M `X-API-Key` para
  `AI_INFORMANT`.
- Endpoints `/context/preflight`, `/ingest/informant` (anti-ingesta-ciega),
  `/analyses`, `/analyses/{id}/requirements`, `/coherence/alerts`,
  `/coherence/alerts/{alert_id}/resolve`, `/graph/nodes/{id}/lock`.
- Roles `ADMIN | ANALYST | VIEWER` (+ principal M2M `AI_INFORMANT`).
  `OPERATOR` → `ANALYST`.
- `extraction-schema.json` v2: `report_metadata`, `extracted_triplets`,
  `analytical_context_links` (`report_id` asignado por el servidor).
- App `apps/informant-agent/` (Agente 5) y storage
  `storage/raw_reports/`, `storage/processed_chunks/`.

### Cambiado
- Provenance en aristas: `source_report_id` + `exact_quote` (+`page_number`).

### Eliminado (breaking deliberado)
- Nodos `Device` y `FinancialAccount` (consolidados en `CyberAsset` con
  `CRYPTO_WALLET`/`PHONE`); aristas `OWNS_DEVICE` y `HOLDS_ACCOUNT`.

## [v1.1.0] — 2026-10-17

### Añadido
- `page_number` en aristas, búsqueda `?q`, endpoint WebSocket de progreso de
  ingesta, `/info` público y nodo `User`.

## [v1.0.0] — 2026-10-17

### Añadido
- Sello inicial de los 3 contratos (Fase 2): `graph-constraints.cypher`,
  `extraction-schema.json`, `openapi.yaml`.
