# Changelog — OSINT Nexus Platform

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El proyecto sigue **Spec-Driven Development (SDD)**: cada versión de contrato va
precedida de su spec y sellada en `contracts/SEAL.md`.

## [Sin publicar]

### v3.1.0 — multi-INT, custodia y aprobación (2026-10-26)
- **Contrato v3.1.0 (provisional)**: `openapi.yaml` con 38 `operationId`;
  `v3/hermes-tools.json` con cinco tools; `v3/db-contract-v3.1.sql` con capas
  `core_entities`, `observables`, `observability_events`, `raw_evidence_vault`,
  `agent_proposals`, PostGIS/pgvector, RLS/TLP y vistas de compatibilidad v3.0.
- **Nuevas rutas**: `POST /graph/subgraph`, `POST /correlation/colocation`,
  `POST /analytics/alias-evasion`, `GET /proposals`, `GET /proposals/{id}`,
  `POST /proposals/{id}/approve|reject`, `GET /evidence/{id}` y
  `GET /evidence/{id}/verify`.
- **Harness Hermes** (`apps/agents/hermes`): gateway con allowlist, validación
  Pydantic, idempotencia, OPSEC fail-closed y auditoría; backend inyectado, sin
  credenciales ni imports de `app.*`.
- **Frontend**: cliente tipado para las 9 operaciones, subgrafo circular,
  mapa MapLibre sin tiles externos por defecto, timeline de eventos, inspector
  de evidencia y bandeja de aprobación con bloqueo por cadena inválida.
- **Migración reproducible**: `scripts/migrate/generate_v31_schema.py` genera
  `migrations/v3.1/0001_schema.sql`; `pglast` valida sintaxis y el contrato.
- **Pruebas**: backend `478 passed, 3 skipped` con Docker; Hermes `10 passed`;
  frontend `76 passed`; auditoría SDD `15/15`.
- **Verificación PostgreSQL real** (2026-10-26): migración v3.0 → v3.1,
  PostGIS/pgvector, RLS con `api_reader`, cadena de evidencia y repositorio
  v3.1 probados en Docker. Se corrigieron colisiones de índices/constraints,
  casts de enums, grants de `tlp_rank`, la vista de adjuntos y un `ANY` de
  subgrafo que fallaba sólo en PostgreSQL real.

### Añadido
- **Copia de seguridad de PostgreSQL** (`deploy/scripts/backup.sh`): volcado
  `pg_dump -Fc` cifrado (`postgres.dump.gpg`) incluido en `SHA256SUMS`.
  `restore.sh` lo restaura con `pg_restore --clean --if-exists` y rechaza
  copias incompletas (anteriores a v3).
- **Integración continua** (`.github/workflows/ci.yml`): contratos y auditoría
  SDD, backend con cobertura mínima del 80 %, informante, frontend (tsc, lint,
  Vitest con cobertura, build y Playwright), validación de Compose y build de
  imágenes, gitleaks y auditorías de dependencias (pip-audit y npm).
- **Pre-commit** (`.pre-commit-config.yaml`): gitleaks y comprobaciones de
  claves privadas, ficheros grandes y formatos.
- **Límite de subida** `MAX_UPLOAD_MB` (50 MB por defecto): lectura en bloques,
  respuesta `413 PAYLOAD_TOO_LARGE`.
- **Validación de formato** de la subida: extensión admitida, cabecera `%PDF`,
  UTF-8 estricto y JSON válido; rechazo con `415 UNSUPPORTED_MEDIA_TYPE`.
- **Métricas Prometheus** en `/metrics` (fuera de `/api/v1`, solo red interna):
  contador y latencia por plantilla de ruta. Servicio `prometheus` opcional
  (`--profile monitoring`) con `deploy/monitoring/prometheus.yml`.
- **Feature flag `GRAPH_BACKEND`** (`neo4j` | `postgres` | `dual`), validado al
  arrancar. `postgres` y `dual` responden 503 hasta tener paridad.
- **Pruebas frontend**: Vitest (63 pruebas unitarias, cobertura ≥ 80 %) y
  Playwright (4 pruebas e2e del flujo de sesión con la API simulada).
- **Pruebas backend sin Docker** para la capa PostgreSQL (pool falso) y para
  los límites de subida y la configuración.

### Cambiado
- **Next.js 14 → 15.5.28** y **React 18 → 19.1.10** para cerrar avisos de
  seguridad críticos y altos de la línea 14 (incluida ejecución remota de
  código en el optimizador de imágenes). Tipos `JSX` migrados a `ReactElement`.
- `postcss` fijado a 8.5.31 mediante `overrides` (avisos en el árbol de Next).
- Vitest 2 → 4.1.12 y Playwright 1.49 → 1.62.0.

### Pendiente
- `contracts/openapi.yaml` (sellado v2.2.0) no documenta aún las respuestas
  `413` y `415` de `createIngestJob` y `createInformantIngestJob`; requiere una
  spec y un nuevo sellado antes de cambiarlo.
- `AGENT_MODE`: definido en PLAN.md para agentes que aún no existen; no se
  implementa hasta que se fije su semántica.
- Tailwind 3.4 conserva 3 avisos moderados (solo en el build de CSS); el
  arreglo exige migrar a Tailwind 4.
- Las pruebas `tests/v3/db` (PostgreSQL real) requieren Docker: se ejecutan en
  el workflow `v3-db`, no en local sin Docker.

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
