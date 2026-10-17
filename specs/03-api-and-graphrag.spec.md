# SPEC-03: API REST/WS, M2M y GraphRAG (v2.0)

> Spec rectora de los **Agentes 2, 3, 4 y 5**. Contrato derivado:
> `/contracts/openapi.yaml`. Toda ruta aquí listada debe existir en el contrato.

## 1. Convenciones
- Base: `/api/v1`. Formato JSON.
- Autenticación: **JWT `Bearer`** (humanos) y **API Key M2M** `X-API-Key` (IA).
- Errores: `{ "error": { "code": String, "message": String } }`.
- Paginación: `?limit` (def. 50, máx 500) y `?cursor`.
- Correlación: toda respuesta incluye la cabecera `X-Request-ID` (SPEC-06 §1.3).

## 2. Roles (nombres canónicos v2.0)
`ADMIN`, `ANALYST` (analista humano; **sustituye a `OPERATOR`**), `VIEWER`, y el
principal M2M `AI_INFORMANT` (no es un `User`; se autentica por API Key, SPEC-04).

## 3. Autenticación (`/auth`)
| Método | Ruta | Descripción | Rol mínimo |
| :--- | :--- | :--- | :--- |
| POST | `/api/v1/auth/login` | Login → access + refresh cookie (2FA según SPEC-04 §2; `429` por fuerza bruta, SPEC-04 §2.1) | público |
| POST | `/api/v1/auth/refresh` | Renueva access token | autenticado |
| POST | `/api/v1/auth/logout` | Invalida refresh token | autenticado |
| GET | `/api/v1/auth/me` | Perfil y rol del operador | autenticado |

## 4. Grafo (`/graph`)
| Método | Ruta | Descripción | Rol mínimo |
| :--- | :--- | :--- | :--- |
| GET | `/api/v1/graph/nodes` | Lista/filtra nodos | VIEWER |
| GET | `/api/v1/graph/nodes/{id}` | Ficha completa de nodo | VIEWER |
| GET | `/api/v1/graph/edges` | Lista/filtra aristas | VIEWER |
| GET | `/api/v1/graph/neighborhood/{id}` | Vecindad a N saltos (máx 3) | VIEWER |
| PUT | `/api/v1/graph/nodes/{id}` | Edita/fusiona nodo (respeta `locked_by_analyst`) | ANALYST |
| POST | `/api/v1/graph/nodes/{id}/lock` | Bloquea nodo verificado (`locked_by_analyst: true`) | ANALYST |
| DELETE | `/api/v1/graph/nodes/{id}` | Borra nodo | ADMIN |

Filtros: `label`, `q`, `?from`/`?to`, `min_confidence`.

## 5. Ingesta y Pre-Flight
| Método | Ruta | Descripción | Rol mínimo |
| :--- | :--- | :--- | :--- |
| POST | `/api/v1/context/preflight` | **Fase 0**: análisis activos + RFIs + entidades canónicas | AI_INFORMANT\|\|ANALYST |
| POST | `/api/v1/ingest` | Sube informe (multipart) | ANALYST |
| POST | `/api/v1/ingest/informant` | Ingesta IA (JSON del contrato + `analytical_context_links`) | **AI_INFORMANT** |
| GET | `/api/v1/ingest/{job_id}` | Estado del job | VIEWER |
| WS | `/api/v1/ingest/{job_id}/ws` | Progreso en vivo (cookie de sesión) | VIEWER |
| GET | `/api/v1/reports/{id}` | Metadatos del informe | VIEWER |
| GET | `/api/v1/reports/{id}/file` | Sirve el PDF original para el visor Split-View | VIEWER |
| GET | `/api/v1/reports/{id}/chunks` | Chunks con coordenadas (subrayado) | VIEWER |

**Ingesta ciega prohibida:** `POST /ingest/informant` sin un `preflight_token`
(vigente) → `409 CONFLICT`.

## 6. Expedientes analíticos y Coherencia
| Método | Ruta | Descripción | Rol mínimo |
| :--- | :--- | :--- | :--- |
| POST | `/api/v1/analyses` | Crea expediente analítico (tesis) | ANALYST |
| PUT | `/api/v1/analyses/{id}` | Edita tesis/conclusión/estado | ANALYST |
| GET | `/api/v1/analyses/{id}` | Ficha del expediente | VIEWER |
| POST | `/api/v1/analyses/{id}/requirements` | Crea RFI (IntelRequirement) | ANALYST |
| GET | `/api/v1/analyses` | Lista expedientes analíticos (`?status`) | VIEWER |
| GET | `/api/v1/analyses/{id}/requirements` | Lista los RFI del expediente | VIEWER |
| GET | `/api/v1/coherence/alerts` | Bandeja de alertas | ANALYST |
| PATCH | `/api/v1/coherence/alerts/{alert_id}/resolve` | Resuelve/descarta alerta | ANALYST |

> **Integridad referencial (v2.0.1, ampliada en v2.1.0):** `POST` y `GET
> /analyses/{id}/requirements` exigen que el `Analysis` `{id}` exista; si no,
> responden `404` (nunca `500` por un `MATCH` fallido ni `200` con lista vacía).

## 7. GraphRAG (`/search`)
| Método | Ruta | Descripción | Rol mínimo |
| :--- | :--- | :--- | :--- |
| POST | `/api/v1/search/rag` | Pregunta en lenguaje natural | VIEWER |

Respuesta: `{ answer, subgraph: {nodes, edges}, citations: [...] }`. Las `citations`
citan **informes OSINT** (`report_id`, `page_number`, `exact_quote`) y
**análisis humanos** (`analysis_id`, `code`). Debe citar siempre la evidencia usada.

**Interfaz de lectura del motor:** `GraphRagEngine` depende de un repositorio con
`search_nodes(query, limit)` y `neighborhood(node_id, depth)` (duck-typing).

## 8. Criterios de Aceptación
- [ ] Todo endpoint existe en `openapi.yaml` con su esquema de seguridad (JWT o API Key).
- [ ] Cada endpoint respeta el rol mínimo (matriz SPEC-04 §1).
- [ ] `POST /ingest/informant` exige API Key M2M y rechaza sin Pre-Flight (`409`).
- [ ] GraphRAG nunca **afirma** sin `citations`; sin evidencia → `200` con
      `answer` de no-evidencia y `citations: []`.
- [ ] `neighborhood` limita saltos (`?depth`, máx 3).
- [ ] Crear o listar RFI de un `Analysis` inexistente devuelve `404` (no `500`).
