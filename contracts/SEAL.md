# Registro de Sellado de Contratos

> El Orquestador (Agente 0) añade una entrada al sellar. **A partir del sello,
> `/contracts` está congelado:** el código que lo contradiga se corrige, o se
> abre una nueva spec y un nuevo sello. No se edita en silencio.

## Sello v3.1.0 (2026-10-26) — PROVISIONAL, PENDIENTE DE TESTCONTAINERS

**Sellado por:** Agente 0 (Director de Arquitectura / Orquestador SDD)
**Autorizado por:** aprobación humana de `SPEC-01`…`SPEC-04` v3.1 (2026-10-26)
**Evidencia de validación:** `bash scripts/validate-contracts.sh` → `ExitCode=0`
(5/5 checks); `python scripts/audit_sdd.py` → 15/15. **Verificación manual
Docker (2026-10-26):** migración v3.0 → v3.1 aplicada con `ON_ERROR_STOP`,
PostGIS 3.6.4 + pgvector 0.8.7, RLS por `api_reader` y repositorio v3.1
probados contra PostgreSQL 16. El sello sigue provisional hasta que
`apps/backend/tests/v3/db/` incluya la migración v3.1 en Testcontainers/CI y
se ejecute `EXPLAIN` de las consultas nuevas.

| Contrato | Versión | SHA-256 |
| :--- | :--- | :--- |
| `openapi.yaml` | **v3.1.0** | `f16fbe714c7bbd4ae1a643654956a585f00bbb38c0ed0335190513c248920307` |
| `v3/hermes-tools.json` | **v3.1.0** | `7b93ff8f58832b951aea187d4d8afde962fc9e91b5af41c9db5fad39bbed9f78` |
| `v3/db-contract-v3.1.sql` | **v3.1.0** (re-sellado 2026-10-29) | `3c33e523b279f0ab6a415da9d6a94f7ed0fc2b5fb5b031787a31df465008bb38` |
| `v3/agent-contracts.json` | v3.0.0 (sin cambios) | `6b10056d7c88feff77a03ebf7d1ed2a673ecfaeca6bdbb8f577c1ba93f87d90f` |
| `graph-constraints.cypher` | v2.1.0 (sin cambios) | `43ecd30a0529fdfcdfc2bd37b2b378688e53cac51c549c74194a310b04b57363` |
| `extraction-schema.json` | v2.0.0 (sin cambios) | `7f66c7d866b29bfdab2c762a09b2642f7055716a872c8717a8fc52203050e7f8` |

### Cambios v2.2.0 → v3.1.0
- `openapi.yaml`: **38** `operationId` (37 HTTP + WebSocket). Nueve rutas v3.1:
  `POST /graph/subgraph`, `POST /correlation/colocation`,
  `POST /analytics/alias-evasion`, `GET /proposals`, `GET /proposals/{id}`,
  `POST /proposals/{id}/approve`, `POST /proposals/{id}/reject`,
  `GET /evidence/{id}` y `GET /evidence/{id}/verify`.
- `v3/hermes-tools.json`: cinco tools con `additionalProperties=false`,
  selectores y evidencias validadas; cualquier tool fuera de la allowlist se
  rechaza antes de tocar la base.
- `v3/db-contract-v3.1.sql`: capas `core_entities`/`observables`/eventos,
  `raw_evidence_vault` append-only, `agent_proposals` con `version` para
  bloqueo optimista, PostGIS + pgvector, RLS/TLP y vistas de compatibilidad
  v3.0. La migración (`migrations/v3.1/0001_schema.sql`) es byte a byte igual.
- `graph-constraints.cypher`, `extraction-schema.json` y
  `v3/agent-contracts.json`: sin cambios.

## Sello anterior — v2.2.0 (2026-10-17)

**Sellado por:** Agente 0 (Director de Arquitectura / Orquestador SDD)
**Autorizado por:** SPEC-04 §2/§2.1 v2.2 · SPEC-06 §1.3
**Evidencia de validación:** `bash scripts/validate-contracts.sh` → `ExitCode=0`

| Contrato | Versión | SHA-256 |
| :--- | :--- | :--- |
| `graph-constraints.cypher` | v2.1.0 (sin cambios) | `43ecd30a0529fdfcdfc2bd37b2b378688e53cac51c549c74194a310b04b57363` |
| `extraction-schema.json` | v2.0.0 (sin cambios) | `7f66c7d866b29bfdab2c762a09b2642f7055716a872c8717a8fc52203050e7f8` |
| `openapi.yaml` | **v2.2.0** | `83a182d7d8250f5b6e984dc95d6c589bb90fe68fa39dcf1d15a1b7bb4f6446b2` |

### Cambios v2.1.0 → v2.2.0
- `openapi.yaml`: `authLogin` declara `429 TooManyRequests` (cabecera
  `Retry-After`); `TokenResponse.expires_in` ≤ 900 s (15 min);
  `LoginRequest.totp_code` con patrón de 6 dígitos; nota de `X-Request-ID`.
  Sin cambios de `operationId` (**29**).
- `graph-constraints.cypher` y `extraction-schema.json`: sin cambios.

### Cambios v2.0.0 → v2.1.0
- `graph-constraints.cypher`: índice vectorial **por label** (`person_embedding_idx`,
  `organization_embedding_idx`, `cyberasset_embedding_idx`) — Neo4j no admite
  índices vectoriales multi-label.
- `openapi.yaml`: nuevos endpoints `GET /analyses` (`listAnalyses`) y
  `GET /analyses/{id}/requirements` (`listAnalysisRequirements`); **29** `operationId`.
- `extraction-schema.json`: sin cambios.

### Resultado de la validación
- `extraction-schema.json`: válido (ajv-cli, draft 2020-12).
- `openapi.yaml`: válido (Redocly).
- Coherencia: **11 labels** y **16 relaciones** presentes.

## Historial de sellado

| Fecha | Versión | Cambio | Motivo | Sella |
| :--- | :--- | :--- | :--- | :--- |
| 2026-10-17 | v1.0.0 | Sello inicial de los 3 contratos | Cierre de Fase 2 | Agente 0 |
| 2026-10-17 | v1.1.0 | `page_number` en aristas + `q` + WS + `/info` + nodo `User` | Gaps A/B de la Fase 3 | Agente 0 |
| 2026-10-17 | v2.0.0 | Nodos analíticos + coherencia Humano-IA + M2M | Arquitectura SDD v2.0 | Agente 0 |
| 2026-10-17 | v2.1.0 | `GET /analyses` + `GET /analyses/{id}/requirements` + vector index por label | Mejora H-5 y corrección de contrato | Agente 0 |
| 2026-10-17 | v2.2.0 | `429` en login, JWT ≤ 15 min, patrón TOTP, `X-Request-ID` | Auditoría SDD v2.2 (SPEC-04 v2.2, SPEC-06) | Agente 0 |
| 2026-10-26 | v3.1.0 | 9 rutas multi-INT/evidencia/propuestas, `hermes-tools.json`, DDL v3.1 | Cierre AUD-23/SPEC-01..04 | Agente 0 (provisional) |
| 2026-10-29 | v3.1.0 (re-sello) | `fn_decide_proposal` sin `p_reviewer`/`p_clearance` (identidad y TLP desde `osint.actor`/`osint.clearance`); `verify_evidence_chain` restringida a `api_reader`, `svc_atenea`, `svc_director_cell` | Auditoría 2026-10-29: escalada de clearance y firma falsificable en aprobaciones; aprobado por el humano | Agente 0 (provisional) |
| 2026-10-29 | v3.1.0 (re-sello 2) | `fn_decide_proposal` exige atestación HMAC-SHA256 (`p_attestation`); tabla `staging.attestation_keys` sin acceso para `api_reader`; revisor y TLP firmados por el backend | Revisión de seguridad: la identidad no puede declararla el cliente ni fijarla un GUC | Agente 0 (provisional) |

## Cómo re-sellar
1. Verificar que el cambio traza a una spec y que hay aprobación humana.
2. Editar el contrato y actualizar su versión interna.
3. Ejecutar `bash scripts/validate-contracts.sh` (debe dar `ExitCode=0`).
4. Recalcular el SHA-256, añadir una entrada de versión arriba y en el historial.
