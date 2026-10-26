# TASKS — Plan de Implementación SDD v3.1 (Multi-INT)

> **Estado: EN EJECUCIÓN v3.1 (2026-10-26).**
> Este archivo es el plan de tareas; **no** es código ni autorización implícita.
> Baseline: `main` post-Hito 1 · Fecha: 2026-10-26 · Autor: Agente 0 (Gobernanza SDD).

---

## 0.0 Estado de implementación (2026-10-26)

> Esta tabla resume qué partes del plan tienen evidencia verificable. No se
> marca `[x]` una tarea cuyo test de aceptación requiera PostgreSQL/PostGIS y
> sólo se haya ejecutado con dobles. Los `skip` de Testcontainers impiden el
> cierre del hito.

| Bloque | Tareas | Estado | Evidencia / límite |
|---|---|---|---|
| Contratos y sello | TASK-03, TASK-17, TASK-33 | Hecho (sello provisional) | `bash scripts/validate-contracts.sh` 5/5; `python scripts/audit_sdd.py` 15/15; 38 `operationId` |
| Esquema y custodia | TASK-05…TASK-10, TASK-12, TASK-13 | Implementado en DDL + servicios | `pglast` parse OK; tests de servicios v3.1; falta aplicar a PostgreSQL real |
| Migración y RLS runtime | TASK-02, TASK-04, TASK-14, TASK-15 | Bloqueado | No hay Docker/daemon en este entorno; Testcontainers sigue en `skip` |
| Harness Hermes | TASK-16…TASK-20, TASK-22 | Hecho | `apps/agents/hermes/tests` 10 passed; gateway con allowlist, idempotencia y OPSEC fail-closed |
| Correlación y API | TASK-27, TASK-29, TASK-31, TASK-34 | Hecho | 16 tests de servicios + 14 tests HTTP v3.1; RBAC/TLP/máscara |
| UI interactiva | TASK-35…TASK-39 | Hecho parcial | Cliente tipado, subgrafo, mapa MapLibre, timeline, inspector y bandeja; 76 tests frontend; faltan E2E y páginas dedicadas `/missions` y `/events/:id` |
| E2E y accesibilidad | TASK-40, TASK-41 | Pendiente | Sin Playwright ejecutado; no se declara WCAG 2.2 AA |
| Dual-write / ETL / cutover | TASK-42…TASK-44 | Pendiente | No implementado; Neo4j sigue siendo el grafo operativo |
| Observabilidad y cierre | TASK-26, TASK-45…TASK-48 | Pendiente / provisional | Sello v3.1 marcado provisional en `contracts/SEAL.md` |

## 0. Reglas de ejecución

1. **Spec first:** ninguna tarea de `/apps`, `/deploy` o `/scripts` empieza sin requisito EARS y spec aprobada.
2. **Contract first:** todo cambio de esquema/API/mensaje se refleja primero en `/contracts` y se re-sella en `contracts/SEAL.md`.
3. **Test first:** cada tarea termina con un test de aceptación reproducible; un `skip` local no cierra la tarea.
4. **No borrar:** la capa validada se revoca (`revoked=true`), nunca se elimina.
5. **Separación de roles:** `HERMES`, `ARACNE`, `ARGOS` sólo escriben ledger/propuestas; `ATENEA` es el único escritor de la capa validada; `DIRECTOR_CELL` gobierna misión/PIR/EEI.
6. **Sin exposición de DB:** PostgreSQL, Neo4j y Redis nunca publican puertos; todo pasa por FastAPI + JWT/API Key + RLS/TLP.
7. **Parada:** si una decisión `DEC-*` no está resuelta, se eleva `SPEC-GAP` y se detiene la tarea; no se improvisa.

### 0.1 Leyenda

| Símbolo | Significado |
|---|---|
| `[ ]` | Pendiente |
| `[x]` | Hecho y verificado |
| `[~]` | En curso |
| `[!]` | Bloqueado |
| **GATE** | No se avanza sin aprobación/evidencia |
| **P0/P1/P2** | Prioridad crítica/alta/media |

---

## 1. Fase 0 — Aprobación y preparación (GATE)

### TASK-01 · Aprobar specs y decisiones — **GATE**
- **Prioridad:** P0 · **Owner:** Usuario + Agente 0
- **Depende de:** nada
- **Acción:** revisar `00-AUDIT.md`, `SPEC-01`…`SPEC-04`, `TASKS.md`; resolver `DEC-A`…`DEC-H` y `SPEC-GAP-*`.
- **Salida:** respuesta “APROBADO” o cambios concretos; `contracts/SEAL.md` actualizado con hashes de las specs.
- **Test:** `python scripts/audit_sdd.py` (12/12) y revisión humana.
- **Criterio:** no se toca producción antes de este cierre.

### TASK-02 · Baseline reproducible con Docker/PostGIS
- **Prioridad:** P0 · **Owner:** Agente 3 (Deploy) + Agente 1 (Datos)
- **Depende de:** TASK-01
- **Archivos:** `deploy/docker-compose.dev.yml`, `deploy/docker-compose.prod.yml`, `deploy/.env.example`, `apps/backend/tests/v3/db/conftest.py`
- **Acción:** añadir PostgreSQL 16 con PostGIS + pgvector; healthcheck; volumen; red interna; sin `ports:`. Documentar imagen y extensiones.
- **Test:** `docker compose -f deploy/docker-compose.dev.yml config`; `psql -c 'SELECT PostGIS_Version()'`.
- **Criterio:** los 12 tests v3 dejan de saltarse y pasan contra un servidor real.

### TASK-03 · Sello de contrato v3.1 y auditoría SDD
- **Prioridad:** P0 · **Owner:** Agente 0
- **Depende de:** TASK-01
- **Archivos:** `contracts/SEAL.md`, `scripts/audit_sdd.py`, `scripts/validate-contracts.sh`, `SPEC.md`, `PLAN.md`
- **Acción:** crear la sección v3.1; hashes de `db-contract.sql`, `agent-contracts.json`, `openapi.yaml`; reglas de validación.
- **Test:** `bash scripts/validate-contracts.sh`; `python scripts/audit_sdd.py`.
- **Criterio:** un cambio de contrato sin sello falla CI.

---

## 2. Fase 1 — Modelo de datos y custodia

### TASK-04 · Marco de migraciones y backup/restore
- **Prioridad:** P0 · **Owner:** Agente 1
- **Depende de:** TASK-02
- **Archivos:** `migrations/v3.1/0001_extensions.sql`, `0002_rename.sql`, `0003_new_tables.sql`, `0004_backfill.sql`, `0005_functions.sql`, `0006_rls.sql`, `migrations/rollback/`
- **Acción:** migraciones idempotentes, transaccionales y reversibles; backup previo; checksum de cada archivo.
- **Test:** `pytest tests/v3/db/test_migrations.py -q`; restore en base limpia.
- **Criterio:** migración y rollback reproducen el esquema sin pérdida.

### TASK-05 · Extensiones, esquemas y tipos
- **Prioridad:** P0 · **Owner:** Agente 1
- **Depende de:** TASK-04
- **Archivos:** `contracts/v3/db-contract.sql`, `migrations/v3.1/0001_extensions.sql`
- **Acción:** `postgis`, `vector`, `btree_gist`, `pg_trgm`, `pgcrypto`; `intel.discipline_enum`, `observable_type_enum`, `alias_type_enum`, `event_type_enum`, `proposal_status_enum`, `validation_state_enum`.
- **Test:** `pytest tests/v3/db/test_extensions.py -q`; `\dT+`.
- **Criterio:** tipos y extensiones existen; `PostGIS_Version()` y `vector` disponibles.

### TASK-06 · `raw_evidence_vault` y adjuntos
- **Prioridad:** P0 · **Owner:** Agente 1
- **Depende de:** TASK-05
- **Archivos:** `contracts/v3/db-contract.sql`, `migrations/v3.1/0002_rename.sql`, `apps/backend/app/db/postgres_repository.py`
- **Acción:** renombrar `raw_intel_ledger`; añadir `discipline`, `source_type`, `observed_at_utc`, `collector_run_id`, `storage_uri`, `canonicalization`; crear `raw_evidence_attachments`; vista de compatibilidad.
- **Test:** `pytest tests/v3/db/test_raw_vault.py -q`; insertar/leer; intentar update/delete → `42501`.
- **Criterio:** append-only, hash y TLP verificados; nombres nuevos y compatibilidad.

### TASK-07 · `core_entities`, `observables`, `observable_alias_history`, `intelligence_events`
- **Prioridad:** P0 · **Owner:** Agente 1
- **Depende de:** TASK-05
- **Archivos:** `contracts/v3/db-contract.sql`, `migrations/v3.1/0003_new_tables.sql`
- **Acción:** DDL de SPEC-01 §6–§7; exclusiones temporales; `geography(Point,4326)`; `value_hash`; sin `aliases[]` en `core_entities`.
- **Test:** `pytest tests/v3/db/test_observables.py tests/v3/db/test_events.py -q`.
- **Criterio:** selector duplicado en ventana solapada → `23P01`; evento sin selector/observable → rechazo.

### TASK-08 · Normalización y `value_hash`
- **Prioridad:** P0 · **Owner:** Agente 2
- **Depende de:** TASK-07
- **Archivos:** `apps/backend/app/services/selector_normalizer.py`, `apps/backend/tests/v3/services/test_selector_normalizer.py`
- **Acción:** E.164, MAC, IMSI, URL, email, wallet, platform IDs; hash SHA-256; tests de vectores.
- **Test:** `pytest apps/backend/tests/v3/services/test_selector_normalizer.py -q`.
- **Criterio:** mismo selector en distintas formas produce el mismo `value_hash`; formas ambiguas se rechazan.

### TASK-09 · Evidencia: canonicalización, hash y colisión de idempotencia
- **Prioridad:** P0 · **Owner:** Agente 2 + Agente 1
- **Depende de:** TASK-06
- **Archivos:** `apps/backend/app/services/evidence_service.py`, `contracts/v3/db-contract.sql`, `apps/backend/tests/v3/db/test_evidence.py`
- **Acción:** RFC 8785, SHA-256, `append_evidence` con `409` si misma clave/bytes distintos; nunca devolver evidencia anterior silenciosamente.
- **Test:** `pytest tests/v3/db/test_evidence.py -q`; test de colisión.
- **Criterio:** alteración de 1 byte → rechazo; colisión → `409` + `audit.events`.

### TASK-10 · Verificación de cadena y endpoint
- **Prioridad:** P0 · **Owner:** Agente 1 + Agente 3
- **Depende de:** TASK-09
- **Archivos:** `contracts/v3/db-contract.sql`, `apps/backend/app/api/v1/evidence.py`, `apps/backend/tests/v3/api/test_evidence_verify.py`
- **Acción:** `staging.verify_evidence_chain`; `GET /api/v1/evidence/{id}/verify`; devolver `chain_valid`, hashes, hora; RBAC/TLP.
- **Test:** `pytest tests/v3/api/test_evidence_verify.py -q`.
- **Criterio:** cadena alterada → `valid=false`; no se permite aprobar propuestas con cadena inválida.

### TASK-11 · Adjuntos y WORM
- **Prioridad:** P1 · **Owner:** Agente 1 + Agente 3
- **Depende de:** TASK-06
- **Archivos:** `contracts/v3/db-contract.sql`, `deploy/README.md`, `apps/backend/app/services/storage_service.py`
- **Acción:** `immutable_uri`, object-lock, verificación de hash de adjunto; no sustitución silenciosa.
- **Test:** `pytest tests/v3/db/test_attachments.py -q`; test de hash alterado.
- **Criterio:** adjunto con hash distinto → rechazo; URI inmutable documentada.

### TASK-12 · `hermes_agent_runs`, `hermes_tool_calls`, `agent_proposals`, `entity_merge_log`
- **Prioridad:** P0 · **Owner:** Agente 1
- **Depende de:** TASK-05
- **Archivos:** `contracts/v3/db-contract.sql`, `migrations/v3.1/0003_new_tables.sql`
- **Acción:** DDL de SPEC-01 §4 y §9; `run_id`, `prompt_sha256`, `tool_catalog_sha256`; propuestas `AGENT_STAGED`; merge log.
- **Test:** `pytest tests/v3/db/test_proposals.py -q`.
- **Criterio:** transición inválida → `23514`; `AGENT_STAGED` no escribe capa validada.

### TASK-13 · Índices espaciales/temporales y planes
- **Prioridad:** P0 · **Owner:** Agente 1 + Agente 3
- **Depende de:** TASK-07
- **Archivos:** `contracts/v3/db-contract.sql`, `apps/backend/tests/v3/db/test_indexes.py`
- **Acción:** GiST `location`, BRIN `event_time`, compuestos `(mission_id,event_time)`, `(discipline,event_time)`, GIN payload, HNSW embeddings.
- **Test:** `pytest tests/v3/db/test_indexes.py -q`; `EXPLAIN` con dataset sintético.
- **Criterio:** consultas por radio/misión usan índice; no `Seq Scan` en tablas grandes.

### TASK-14 · Funciones de escritura, revocación y fusión
- **Prioridad:** P0 · **Owner:** Agente 1
- **Depende de:** TASK-07, TASK-12
- **Archivos:** `contracts/v3/db-contract.sql`, `apps/backend/app/db/postgres_repository.py`
- **Acción:** `upsert_entity`, `upsert_relationship`, `upsert_observable`, `upsert_event`, `revoke_*`, `approve_merge`; evidencia obligatoria; sólo `svc_atenea`.
- **Test:** `pytest tests/v3/db/test_write_functions.py -q`.
- **Criterio:** sin evidencia → `23503`; no delete; TLP no baja; fusión crea `ALIAS_OF` y revoca origen.

### TASK-15 · RLS, roles y vistas de fusión
- **Prioridad:** P0 · **Owner:** Agente 1 + Agente 3
- **Depende de:** TASK-14
- **Archivos:** `contracts/v3/db-contract.sql`, `migrations/v3.1/0006_rls.sql`, `apps/backend/tests/v3/db/test_rls.py`
- **Acción:** `svc_*`, `api_reader`, `svc_director_cell`; políticas TLP/misión; `api.v_entity_fusion`, `v_observable_current_alias`, `v_entity_timeline`.
- **Test:** `pytest tests/v3/db/test_rls.py -q`.
- **Criterio:** Hermes no lee otra misión; Atenea escribe; API no ve por encima de clearance.

---

## 3. Fase 2 — Harness Hermes y tools

### TASK-16 · Tool gateway y allowlist
- **Prioridad:** P0 · **Owner:** Agente 2 + Agente 5
- **Depende de:** TASK-12, TASK-15
- **Archivos:** `apps/agents/hermes/tool_gateway.py`, `apps/agents/hermes/policy.py`, `apps/backend/tests/v3/hermes/test_tool_gateway.py`
- **Acción:** registro de 5 tools; validación JSON Schema/Pydantic; `session_user`; auditoría; sin SQL/HTTP genérico.
- **Test:** `pytest tests/v3/hermes/test_tool_gateway.py -q`.
- **Criterio:** tool no allowlisted → `403`; argumentos inválidos → `422`; toda llamada auditada.

### TASK-17 · Contratos JSON Schema + Pydantic v2
- **Prioridad:** P0 · **Owner:** Agente 2 + Agente 5
- **Depende de:** TASK-16
- **Archivos:** `contracts/v3/hermes-tools.json`, `apps/agents/hermes/models.py`, `apps/backend/tests/v3/contracts/test_hermes_tools_contract.py`
- **Acción:** bundle de SPEC-02 §3–§8; `extra="forbid"`; validadores de ventana/TLP/Admiralty/hash; ejemplos válidos/inválidos.
- **Test:** `pytest tests/v3/contracts/test_hermes_tools_contract.py -q`; `scripts/validate-contracts.sh`.
- **Criterio:** contrato y modelos aceptan/rechazan el mismo conjunto.

### TASK-18 · Idempotencia y canonicalización
- **Prioridad:** P0 · **Owner:** Agente 2
- **Depende de:** TASK-09, TASK-17
- **Archivos:** `apps/agents/hermes/idempotency.py`, `apps/backend/tests/v3/hermes/test_idempotency.py`
- **Acción:** fórmula §9; recálculo; `DUPLICATE` vs `409`; registro en tool calls.
- **Test:** `pytest tests/v3/hermes/test_idempotency.py -q`.
- **Criterio:** misma clave/mismo hash → `DUPLICATE`; distinto hash → `409` + alerta.

### TASK-19 · Entity Resolution v3
- **Prioridad:** P0 · **Owner:** Agente 2
- **Depende de:** TASK-08, TASK-15
- **Archivos:** `apps/backend/app/services/entity_resolver_v3.py`, `apps/backend/tests/v3/services/test_entity_resolver_v3.py`
- **Acción:** exact selector → alias history → trigram → embeddings; score §10; `ALIAS_OF`/merge proposal; locked analyst.
- **Test:** `pytest tests/v3/services/test_entity_resolver_v3.py -q`.
- **Criterio:** nombres solos no auto-fusionan; selector exacto no duplica; ambiguo → revisión.

### TASK-20 · Máquina de estados y API de propuestas
- **Prioridad:** P0 · **Owner:** Agente 2 + Agente 3
- **Depende de:** TASK-12, TASK-16
- **Archivos:** `apps/backend/app/api/v1/proposals.py`, `apps/agents/hermes/proposal_service.py`, `apps/backend/tests/v3/api/test_proposals.py`
- **Acción:** `GET /proposals`, `POST /{id}/approve`, `/reject`; `version` optimista; transiciones; `APPLIED` sólo ATENEA.
- **Test:** `pytest tests/v3/api/test_proposals.py -q`.
- **Criterio:** `AGENT_STAGED → ANALYST_VERIFIED → APPLIED`; rechazo exige motivo; conflicto `409`.

### TASK-21 · Contexto de misión, PIR/EEI y tasking
- **Prioridad:** P1 · **Owner:** Agente 5
- **Depende de:** TASK-16
- **Archivos:** `apps/agents/hermes/context.py`, `apps/backend/app/api/v1/missions.py`, `apps/backend/tests/v3/hermes/test_context.py`
- **Acción:** cargar misión/PIR/EEI; validar que evidencia y propuesta pertenecen a la misión; tasking.
- **Test:** `pytest tests/v3/hermes/test_context.py -q`.
- **Criterio:** propuesta fuera de misión → `403`; contexto no filtra TLP superior.

### TASK-22 · OPSEC y kill switch
- **Prioridad:** P0 · **Owner:** Agente 5 + Agente 1
- **Depende de:** TASK-16
- **Archivos:** `apps/agents/hermes/opsec.py`, `contracts/v3/db-contract.sql`, `apps/backend/tests/v3/hermes/test_opsec.py`
- **Acción:** invocar `humint.check_opsec` antes de interacción; `ABORT` + `OpsecEvent`; kill switch por misión/avatar.
- **Test:** `pytest tests/v3/hermes/test_opsec.py -q`.
- **Criterio:** violación → no se envía; alerta HIGH; no se degrada a warning.

### TASK-23 · Límites, presupuesto y DLQ
- **Prioridad:** P1 · **Owner:** Agente 2 + Agente 3
- **Depende de:** TASK-16
- **Archivos:** `apps/agents/hermes/limits.py`, `apps/backend/app/workers/hermes_tasks.py`, `apps/backend/tests/v3/hermes/test_limits.py`
- **Acción:** 30 evid/min, 50 tool calls/run, timeouts, tokens, DLQ, reintentos idempotentes.
- **Test:** `pytest tests/v3/hermes/test_limits.py -q`.
- **Criterio:** exceso → `429`/DLQ; no duplicados.

### TASK-24 · Autenticación de agentes y separación de secretos
- **Prioridad:** P0 · **Owner:** Agente 3
- **Depende de:** TASK-15
- **Archivos:** `apps/backend/app/core/rbac.py`, `apps/backend/app/core/security.py`, `deploy/.env.example`, `apps/backend/tests/v3/security/test_agent_auth.py`
- **Acción:** API keys por agente, rotación, hash, `session_user`; secretos en KMS/env; prohibir credenciales en prompts.
- **Test:** `pytest tests/v3/security/test_agent_auth.py -q`.
- **Criterio:** clave revocada → `401`; Hermes no obtiene DSN de escritura.

### TASK-25 · Loop del harness y defensa de prompt injection
- **Prioridad:** P0 · **Owner:** Agente 5
- **Depende de:** TASK-17, TASK-22
- **Archivos:** `apps/agents/hermes/harness.py`, `apps/agents/hermes/prompts.py`, `apps/backend/tests/v3/hermes/test_prompt_injection.py`
- **Acción:** bucle máximo, separación `UNTRUSTED_DATA`, sin instrucciones del payload, evaluación de jailbreak, salida validada.
- **Test:** `pytest tests/v3/hermes/test_prompt_injection.py -q`.
- **Criterio:** payload malicioso no cambia tools/estado; no escritura directa.

### TASK-26 · Telemetría de runs y tool calls
- **Prioridad:** P1 · **Owner:** Agente 3
- **Depende de:** TASK-12
- **Archivos:** `apps/backend/app/services/telemetry.py`, `deploy/otel/`, `apps/backend/tests/v3/telemetry/test_runs.py`
- **Acción:** `run_id`, modelo, prompt hash, tool calls, latencia, tokens, coste; trazas OpenTelemetry.
- **Test:** `pytest tests/v3/telemetry/test_runs.py -q`.
- **Criterio:** toda tool call tiene run y hash; no se loguean selectores.

---

## 4. Fase 3 — Motor de correlación

### TASK-27 · Colocalización SIGINT-SOCMINT
- **Prioridad:** P0 · **Owner:** Agente 2 + Agente 3
- **Depende de:** TASK-13, TASK-15
- **Archivos:** `apps/backend/app/services/correlation.py`, `apps/backend/app/api/v1/correlation.py`, `apps/backend/tests/v3/correlation/test_colocation.py`
- **Acción:** query SPEC-03 §3; `ST_DWithin`; ventana; score; clusters; TLP; máscara.
- **Test:** `pytest tests/v3/correlation/test_colocation.py -q`.
- **Criterio:** par dentro de radio/tiempo → cluster; fuera → no; TLP:RED oculto.

### TASK-28 · Ingesta de eventos SIGINT y endpoint
- **Prioridad:** P0 · **Owner:** Agente 2 + Agente 3
- **Depende de:** TASK-27
- **Archivos:** `apps/backend/app/api/v1/events.py`, `apps/backend/app/services/event_service.py`, `apps/backend/tests/v3/api/test_sigint_events.py`
- **Acción:** `record_sigint_telemetry` → propuesta → aprobación → `intelligence_events`; máscara de IMSI/MAC; GeoJSON.
- **Test:** `pytest tests/v3/api/test_sigint_events.py -q`.
- **Criterio:** evento sin selector/evidencia → rechazo; coordenada SRID 4326.

### TASK-29 · Subgrafo multicapa
- **Prioridad:** P0 · **Owner:** Agente 2 + Agente 3
- **Depende de:** TASK-13, TASK-15
- **Archivos:** `apps/backend/app/services/subgraph.py`, `apps/backend/app/api/v1/graph.py`, `apps/backend/tests/v3/correlation/test_subgraph.py`
- **Acción:** CTE recursiva SPEC-03 §4; `depth` 1–5; dirección; filtros; `max_nodes/edges`; `truncated`; `as_of`.
- **Test:** `pytest tests/v3/correlation/test_subgraph.py -q`.
- **Criterio:** ciclos no infinitos; filtros correctos; staged oculto por defecto.

### TASK-30 · Semántica `as_of` y revocaciones
- **Prioridad:** P1 · **Owner:** Agente 1 + Agente 2
- **Depende de:** TASK-29
- **Archivos:** `apps/backend/app/services/temporal.py`, `apps/backend/tests/v3/correlation/test_as_of.py`
- **Acción:** `valid_from/valid_until`, `revoked_at`, eventos; consultas históricas; tests de borde.
- **Test:** `pytest tests/v3/correlation/test_as_of.py -q`.
- **Criterio:** estado pasado reproducible; revocación no borra historia.

### TASK-31 · Detección de evasión de alias
- **Prioridad:** P1 · **Owner:** Agente 2 + Agente 3
- **Depende de:** TASK-19, TASK-29
- **Archivos:** `apps/backend/app/services/alias_evasion.py`, `apps/backend/app/api/v1/analytics.py`, `apps/backend/tests/v3/correlation/test_alias_evasion.py`
- **Acción:** rotación, cross-platform, colisión; score; evidencia; no fusión automática.
- **Test:** `pytest tests/v3/correlation/test_alias_evasion.py -q`.
- **Criterio:** 6 handles/18 h → señal; colisión → señal, no `ALIAS_OF`.

### TASK-32 · Coste, planes y materialización
- **Prioridad:** P1 · **Owner:** Agente 1 + Agente 3
- **Depende de:** TASK-27–TASK-31
- **Archivos:** `apps/backend/tests/v3/correlation/test_query_plans.py`, `migrations/v3.1/0007_materialized.sql`
- **Acción:** `statement_timeout`, `EXPLAIN` en CI, `mv_event_hotspots` opcional, cursor.
- **Test:** `pytest tests/v3/correlation/test_query_plans.py -q`.
- **Criterio:** sin `Seq Scan` en filtros críticos; P95 < 2 s con 10k eventos.

### TASK-33 · OpenAPI v3.1 y tipos generados
- **Prioridad:** P0 · **Owner:** Agente 3 + Agente 0
- **Depende de:** TASK-27–TASK-31
- **Archivos:** `contracts/openapi.yaml`, `apps/frontend/src/types/api.d.ts`, `apps/backend/tests/v3/contracts/test_openapi.py`
- **Acción:** 9 operaciones nuevas (38 `operationId` totales); schemas; errores; ejemplos; `npm run gen:api`.
- **Test:** `pytest tests/v3/contracts/test_openapi.py -q`; `npm run gen:api`.
- **Criterio:** contrato y UI compilan; no `any`.

### TASK-34 · RBAC/TLP/auditoría de correlación
- **Prioridad:** P0 · **Owner:** Agente 3
- **Depende de:** TASK-27–TASK-31
- **Archivos:** `apps/backend/app/api/v1/correlation.py`, `apps/backend/tests/v3/security/test_correlation_rbac.py`
- **Acción:** rol, misión, TLP, máscara, rate limit, `audit.events` con hash de query.
- **Test:** `pytest tests/v3/security/test_correlation_rbac.py -q`.
- **Criterio:** VIEWER no ve TLP:RED; Hermes no ve otra misión; auditoría completa.

### TASK-35 · Cliente frontend y SSE
- **Prioridad:** P1 · **Owner:** Agente 4
- **Depende de:** TASK-33
- **Archivos:** `apps/frontend/src/lib/api.ts`, `apps/frontend/src/lib/sse.ts`, `apps/frontend/src/**/*.test.ts`
- **Acción:** cliente tipado, manejo de errores, SSE de propuestas/alertas, limpieza al cambiar misión.
- **Test:** `npm test -- --run`.
- **Criterio:** no cachea TLP:RED; reconexión; tipos sin `any`.

---

## 5. Fase 4 — UI interactiva

### TASK-36 · Grafo multicapa y filtros
- **Prioridad:** P0 · **Owner:** Agente 4
- **Depende de:** TASK-29, TASK-35
- **Archivos:** `apps/frontend/src/components/graph/GraphCanvas.tsx`, `GraphFilters.tsx`, `src/app/graph/page.tsx`
- **Acción:** colores/formas, staged separado, filtros disciplina/TLP/Admiralty/confianza/as_of, URL state, teclado.
- **Test:** `npm test -- --run`; `npx playwright test graph.spec.ts`.
- **Criterio:** 5k nodos ≥ 30 fps; no color-only; staged no mezclado.

### TASK-37 · Bandeja de aprobación
- **Prioridad:** P0 · **Owner:** Agente 4
- **Depende de:** TASK-20, TASK-35
- **Archivos:** `apps/frontend/src/components/proposals/ApprovalInbox.tsx`, `ProposalDiff.tsx`, `src/app/approvals/page.tsx`
- **Acción:** lista, diff, evidencia, aprobar/rechazar, motivo, versión, SSE, estados.
- **Test:** `npm test -- --run`; `npx playwright test approvals.spec.ts`.
- **Criterio:** no “aprobar todo”; rechazo exige motivo; `409` manejado.

### TASK-38 · Mapa-tiempo y `as_of`
- **Prioridad:** P0 · **Owner:** Agente 4
- **Depende de:** TASK-27, TASK-35
- **Archivos:** `apps/frontend/src/components/map/MultiIntMap.tsx`, `EventTimeline.tsx`, workspace integrado en `src/app/page.tsx`
- **Acción:** MapLibre, clusters, time brush, colocalización, máscara TLP:RED, tabla accesible.
- **Test:** `npx playwright test map.spec.ts`; `npm test -- --run`.
- **Criterio:** sin coordenadas exactas TLP:RED; brush < 100 ms; sin tiles públicos sensibles.

### TASK-39 · Inspector de evidencia
- **Prioridad:** P0 · **Owner:** Agente 4
- **Depende de:** TASK-10, TASK-35
- **Archivos:** `apps/frontend/src/components/evidence/EvidenceInspector.tsx`, `src/app/events/[id]/page.tsx`
- **Acción:** hash, cadena, adjuntos, TLP, base legal, extracto, verificar, descarga condicionada.
- **Test:** `npx playwright test evidence.spec.ts`.
- **Criterio:** `chainValid=false` bloquea aprobación; no XSS; no selectores completos sin rol.

### TASK-40 · Misión/PIR, accesibilidad y presupuestos
- **Prioridad:** P1 · **Owner:** Agente 4
- **Depende de:** TASK-36–TASK-39
- **Archivos:** `src/app/missions/[id]/page.tsx`, `src/components/a11y/`, `apps/frontend/tests/a11y.spec.ts`
- **Acción:** cobertura PIR/EEI, navegación por teclado, contraste, `prefers-reduced-motion`, presupuestos.
- **Test:** `npx playwright test a11y.spec.ts`; Lighthouse CI.
- **Criterio:** WCAG 2.2 AA; LCP/INP dentro de presupuesto.

### TASK-41 · E2E y regresión visual
- **Prioridad:** P0 · **Owner:** Agente 4 + Agente 3
- **Depende de:** TASK-36–TASK-40
- **Archivos:** `apps/frontend/e2e/*.spec.ts`, `.github/workflows/ci.yml`
- **Acción:** flujos críticos: filtro TLP, staged, aprobar, evidencia alterada, conflicto, mapa.
- **Test:** `npm run test:e2e`.
- **Criterio:** 0 fallos; capturas estables; CI bloquea merge.

---

## 6. Fase 5 — Dual-write, migración y producción

### TASK-42 · Adaptador `GRAPH_BACKEND=neo4j|postgres|dual`
- **Prioridad:** P0 · **Owner:** Agente 1 + Agente 3
- **Depende de:** TASK-14, TASK-29
- **Archivos:** `apps/backend/app/db/factory.py`, `apps/backend/app/db/dual_repository.py`, `apps/backend/tests/v3/db/test_dual_write.py`
- **Acción:** escritura a PostgreSQL y proyección Neo4j; consistencia; fallback; feature flag.
- **Test:** `pytest tests/v3/db/test_dual_write.py -q`.
- **Criterio:** fallo de proyección no pierde ledger; rollback a Neo4j.

### TASK-43 · ETL Neo4j → PostgreSQL y reconciliación
- **Prioridad:** P0 · **Owner:** Agente 1
- **Depende de:** TASK-42
- **Archivos:** `scripts/migrate/neo4j_to_postgres.py`, `apps/backend/tests/v3/migration/test_etl.py`
- **Acción:** mapear labels/relaciones; deduplicar; evidencia histórica; reporte de diferencias.
- **Test:** `pytest tests/v3/migration/test_etl.py -q`.
- **Criterio:** conteos y muestras reconcilian; diferencias documentadas.

### TASK-44 · Cutover, backup y rollback
- **Prioridad:** P0 · **Owner:** Agente 3
- **Depende de:** TASK-42, TASK-43
- **Archivos:** `deploy/runbooks/cutover.md`, `deploy/runbooks/rollback.md`, `deploy/backup/`
- **Acción:** ensayo en staging; backup; ventana; rollback a Neo4j; verificación.
- **Test:** simulacro de cutover/rollback en staging.
- **Criterio:** RTO/RPO definidos; sin pérdida de evidencia.

### TASK-45 · Revisión de seguridad y OPSEC
- **Prioridad:** P0 · **Owner:** Agente 3 + Agente 0
- **Depende de:** TASK-34, TASK-42
- **Archivos:** `docs/security/THREAT-MODEL.md`, `docs/security/PENTEST.md`
- **Acción:** STRIDE, abuso de tools, prompt injection, TLP, secretos, dependencias, rate limit.
- **Test:** `pip-audit`, `npm audit`, revisión manual.
- **Criterio:** hallazgos críticos cerrados o aceptados con riesgo documentado.

### TASK-46 · Observabilidad, SLO y runbooks
- **Prioridad:** P1 · **Owner:** Agente 3
- **Depende de:** TASK-26, TASK-42
- **Archivos:** `deploy/otel/`, `deploy/runbooks/operations.md`, `docs/slo.md`
- **Acción:** métricas de ingesta, latencia, errores, cola, tool calls; alertas; dashboards.
- **Test:** smoke test de métricas/trazas.
- **Criterio:** SLO definidos; alertas probadas.

---

## 7. Fase 6 — Cierre y sello

### TASK-47 · Sello v3.1 y release
- **Prioridad:** P0 · **Owner:** Agente 0
- **Depende de:** TASK-41–TASK-46
- **Archivos:** `contracts/SEAL.md`, `CHANGELOG.md`, `SPEC.md`, `PLAN.md`, `AUDIT.md`
- **Acción:** hashes, matriz de trazabilidad, release notes, migración documentada.
- **Test:** `python scripts/audit_sdd.py`; `bash scripts/validate-contracts.sh`.
- **Criterio:** 12/12 checks; todos los EARS con test; sin `skip` crítico.

### TASK-48 · Aceptación final
- **Prioridad:** P0 · **Owner:** Usuario + Agente 0
- **Depende de:** TASK-47
- **Acción:** revisar matriz EARS→test→evidencia; aprobar release.
- **Test:** suite completa backend/frontend/agente; E2E; migración; rollback.
- **Criterio:** “APROBADO” explícito; no se libera sin él.

---

## 8. Matriz de trazabilidad resumida

| Spec | Tareas principales | Gate |
|---|---|---|
| SPEC-01 | TASK-04…TASK-15 | PostgreSQL real + triggers + RLS |
| SPEC-02 | TASK-16…TASK-26 | tool gateway + cuarentena + OPSEC |
| SPEC-03 | TASK-27…TASK-35 | PostGIS + subgrafo + alias evasion |
| SPEC-04 | TASK-36…TASK-41 | E2E + accesibilidad + TLP |
| Dual-write | TASK-42…TASK-46 | cutover/rollback |
| Cierre | TASK-47…TASK-48 | sello + aceptación |

### 8.1 Dependencias críticas

```text
TASK-01 (aprobación)
  └─ TASK-02 (PostGIS/Docker)
      └─ TASK-04 (migraciones)
          ├─ TASK-06/07/12 (tablas)
          │   ├─ TASK-14 (funciones)
          │   │   └─ TASK-15 (RLS)
          │   │       └─ TASK-16 (gateway)
          │   │           ├─ TASK-17/18/19/20
          │   │           └─ TASK-22/23/24/25
          │   └─ TASK-13 (índices)
          │       └─ TASK-27/29/31 (correlación)
          └─ TASK-33 (OpenAPI)
              └─ TASK-35 (cliente)
                  └─ TASK-36…TASK-41 (UI)
                      └─ TASK-42…TASK-46 (dual-write)
                          └─ TASK-47/48 (sello)
```

---

## 9. Comandos de verificación

```bash
# Backend
cd apps/backend && python -m pytest -q
cd apps/backend && python -m pytest tests/v3/db -q
cd apps/backend && python -m pytest tests/v3/hermes -q
cd apps/backend && python -m pytest tests/v3/correlation -q
cd apps/backend && python -m pytest tests/v3/security -q

# Agente
cd apps/informant-agent && python -m pytest -q

# Frontend
cd apps/frontend && npm test -- --run
cd apps/frontend && npm run test:e2e

# Contratos/gobernanza
bash scripts/validate-contracts.sh
python scripts/audit_sdd.py
```

**Regla:** los tests que requieren Docker/PostGIS deben ejecutarse en CI; no se acepta `skip` como cierre de un hito.

---

## 10. Riesgos y rollback

| Riesgo | Mitigación | Rollback |
|---|---|---|
| Renombrar tablas rompe v2.2 | vistas de compatibilidad + feature flag | `GRAPH_BACKEND=neo4j` |
| PostGIS no disponible | imagen probada en CI | volver a `pgvector` sin eventos espaciales |
| Propuestas se aplican sin aprobación | triggers + RLS + tests | deshabilitar `apply` |
| Fuga TLP en UI/API | RLS + tests + revisión | bloquear endpoint |
| Coste de queries recursivas | límites + índices + `EXPLAIN` | degradar a depth 2 |
| Pérdida de evidencia | WORM + hash chain + backup | restaurar bundle/backup |
| Deriva de contrato | sello + CI | rechazar merge |

---

## 11. Definition of Done (por tarea)

Una tarea sólo se marca `[x]` si:

1. El requisito EARS está identificado.
2. El contrato está actualizado y sellado.
3. Hay test unitario y, si aplica, de integración con PostgreSQL real.
4. El test falla antes del cambio y pasa después.
5. No hay `skip` nuevo sin justificación.
6. RLS/TLP/auditoría verificados.
7. `scripts/audit_sdd.py` sigue en 12/12.
8. La documentación de rollback está actualizada.

---

## 12. Estado actual

| Fase | Estado |
|---|---|
| 0 — Aprobación | **APROBADA** por el usuario el 2026-10-26 |
| 1 — Datos | Implementado en DDL/servicios; **runtime bloqueado** sin Docker |
| 2 — Hermes | **Hecho**; 10 tests; gateway con OPSEC fail-closed |
| 3 — Correlación | **Hecho**; servicios y rutas con TLP/RBAC |
| 4 — UI | **Parcial**; 6 vistas, 76 tests; faltan E2E y accesibilidad |
| 5 — Dual-write | **Pendiente**; Neo4j sigue siendo el backend operativo |
| 6 — Cierre | **Provisional**; sello pendiente de Testcontainers |

**Próxima acción:** ejecutar la migración v3.1 y las pruebas de RLS/PostGIS en
un entorno con Docker; sólo entonces promover el sello provisional y cerrar el
hito. La tabla `0.0` de este documento es la referencia de estado vigente.
