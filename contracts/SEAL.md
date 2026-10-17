# Registro de Sellado de Contratos

> El Orquestador (Agente 0) añade una entrada al sellar. **A partir del sello,
> `/contracts` está congelado:** el código que lo contradiga se corrige, o se
> abre una nueva spec y un nuevo sello. No se edita en silencio.

## Sello vigente — v2.2.0 (2026-10-17)

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

## Cómo re-sellar
1. Verificar que el cambio traza a una spec y que hay aprobación humana.
2. Editar el contrato y actualizar su versión interna.
3. Ejecutar `bash scripts/validate-contracts.sh` (debe dar `ExitCode=0`).
4. Recalcular el SHA-256, añadir una entrada de versión arriba y en el historial.
