# Changelog — OSINT Nexus Platform

Formato basado en [Keep a Changelog](https://keepachangelog.com/es-ES/1.1.0/).
El proyecto sigue **Spec-Driven Development (SDD)**: cada versión de contrato va
precedida de su spec y sellada en `contracts/SEAL.md`.

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
