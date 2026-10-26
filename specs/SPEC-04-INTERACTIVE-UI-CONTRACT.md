# SPEC-04 — Contrato de UI Interactiva y Bandeja de Aprobación

> **Estado: DRAFT v0.1.0 — PROPUESTA, no aprobada.**
> Depende de: `SPEC-01-DATA-MODEL.md`, `SPEC-02-HERMES-HARNESS-INGESTION.md`, `SPEC-03-CORRELATION-ENGINE.md`.
> Aprobador requerido: usuario. Sin aprobación no se modifica `apps/frontend/**` ni `contracts/openapi.yaml`.

---

## 1. Contexto y objetivo

El frontend actual (`apps/frontend`) es un dashboard Next.js 15.5.28 / React 19 con cuatro vistas: grafo Cytoscape, dossier, análisis y coherencia. Es una base sólida, pero no permite operar la célula multi-INT:

- el grafo no distingue OSINT/SOCMINT/SIGINT;
- no hay mapa ni timeline espacial;
- no hay filtros TLP/Almirantazgo/disciplina;
- no hay bandeja de propuestas `AGENT_STAGED`;
- no hay inspector de evidencia con verificación de hash/cadena;
- no hay reconstrucción histórica (`as_of`);
- no hay visualización de colocalización SIGINT-SOCMINT.

Este documento define el **contrato de UI**: información que se muestra, interacciones permitidas, estados, permisos, rendimiento y accesibilidad. No define el código React; eso se implementa en `TASKS.md` tras aprobación.

### 1.1 Principio rector

> **El analista ve primero la evidencia y el estado de validación; nunca una “verdad” no aprobada.**

Toda pantalla debe responder, para cada dato:

1. ¿De qué disciplina viene?
2. ¿Qué evidencia lo soporta?
3. ¿Está validado por humano o sólo staged?
4. ¿Qué TLP/clearance tiene?
5. ¿Cuándo fue válido?

---

## 2. Arquitectura de información

### 2.1 Layout principal

```text
┌─────────────────────────────────────────────────────────────────────┐
│ MissionContextBar: misión · PIR · EEI · usuario · TLP banner       │
├──────────────┬──────────────────────────────────────┬───────────────┤
│ FilterRail   │ Workspace                             │ Inspector     │
│              │  ┌────────┬────────┬──────────────┐  │               │
│ Misión       │  │ Graph  │ Map    │ Timeline     │  │ Entity/Event  │
│ PIR/EEI      │  │        │        │              │  │ Evidence      │
│ Disciplina   │  │        │        │              │  │ Approvals     │
│ TLP          │  └────────┴────────┴──────────────┘  │               │
│ Almirantazgo │                                      │               │
│ Confianza    │                                      │               │
│ Tiempo       │                                      │               │
│ as_of        │                                      │               │
├──────────────┴──────────────────────────────────────┴───────────────┤
│ ApprovalTray: propuestas AGENT_STAGED · alertas OPSEC · conflictos  │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Rutas

| Ruta | Vista | Operación principal |
|---|---|---|
| `/` | Mission overview | cobertura, PIR, alertas |
| `/graph` | Grafo multicapa | `getMultiLayerSubgraph` |
| `/map` | Mapa-tiempo | `findSigintSocmintColocation` |
| `/events/:id` | Evento/evidencia | evidencia + cadena |
| `/approvals` | Bandeja de aprobación | propuestas `AGENT_STAGED` |
| `/missions/:id` | Misión/PIR/EEI | cobertura y tasking |
| `/search` | Búsqueda híbrida | GraphRAG + selectores |

Las rutas existentes se conservan si no contradicen este contrato; se añaden las nuevas sin romper enlaces.

---

## 3. Contratos de componentes

### 3.1 `MissionContextBar`

**Props**

```ts
type MissionContextBarProps = {
  mission: {
    id: string;
    name: string;
    status: "ACTIVE" | "PAUSED" | "CLOSED";
    tlp: Tlp;
    pirCount: number;
    eeiCount: number;
  };
  user: {
    id: string;
    role: "VIEWER" | "ANALYST" | "ADMIN";
    clearance: Tlp;
  };
  onMissionChange: (missionId: string) => void;
};
```

**Reglas**

- El TLP banner es siempre visible; no se puede ocultar.
- Si `mission.status !== ACTIVE`, el workspace muestra banner de sólo lectura y deshabilita aprobaciones.
- Si el usuario cambia de misión, se limpian selecciones, filtros y cachés sensibles.
- El selector de misión no muestra misiones fuera del RBAC.

### 3.2 `FilterRail`

**Filtros obligatorios**

| Filtro | Tipo | Regla |
|---|---|---|
| `disciplines` | multi-select | `OSINT`, `SOCMINT`, `SIGINT`, `HUMINT`, `TECHINT`, `FININT`, `GEOINT`, `MIXED` |
| `tlpMax` | select | nunca superior al clearance del usuario |
| `admiraltyMin` | select | `A1`…`F6`; comparación por letra y número |
| `confidenceMin` | range 0–100 | por defecto 50 |
| `window` | date-time range | máximo 30 días; validación en cliente y servidor |
| `asOf` | date-time / “ahora” | reconstrucción histórica |
| `depth` | 1–5 | sólo vista grafo; por defecto 2 |
| `includeStaged` | checkbox | sólo `ANALYST`/`ADMIN`; por defecto `false` |
| `search` | text | selectores, nombres, alias; debounce 300 ms |

**Reglas**

- Un filtro vacío no significa “todo” si el TLP/clearance lo impide.
- El estado de filtros se serializa en la URL (`?discipline=SIGINT,SOCMINT&asOf=…`) para compartir una vista sin datos sensibles.
- No se guardan filtros con valores de selectores sensibles en `localStorage`.
- Al cambiar `asOf`, se invalida la caché del workspace y se vuelve a consultar.

### 3.3 `GraphCanvas`

**Props**

```ts
type GraphNode = {
  id: string;
  stixId: string;
  stixType: string;
  label: string;
  discipline: Discipline;
  tlp: Tlp;
  admiralty: string;
  confidence: number;
  validationState: "ANALYST_VERIFIED" | "AGENT_STAGED";
  depth: number;
  observables: { type: string; maskedValue: string }[];
  evidenceIds: string[];
};

type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  discipline: Discipline;
  confidence: number;
  admiralty: string;
  tlp: Tlp;
  validFrom: string;
  validUntil: string | null;
  evidenceIds: string[];
};

type GraphCanvasProps = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedIds: string[];
  onSelect: (ids: string[]) => void;
  onExpand: (entityId: string) => void;
  onOpenEvidence: (entityId: string) => void;
  onPin: (entityId: string) => void;
  maxNodes?: number;
};
```

**Representación visual**

| Dimensión | Codificación | Regla |
|---|---|---|
| Disciplina | color de borde | OSINT azul, SOCMINT violeta, SIGINT naranja, HUMINT rojo, mixto gris |
| Tipo STIX | forma | `identity` círculo, `threat-actor` hexágono, `location` pin, `event` rombo |
| Validación | estilo | validado sólido; `AGENT_STAGED` punteado + badge “STAGED” |
| TLP | badge | siempre visible en hover y en inspector; no sólo color |
| Almirantazgo | etiqueta | `A1`…`F6`; no saturar si zoom < 0.6 |
| Confianza | opacidad/arista | 0–100; no usar sólo opacidad (accesibilidad) |
| Dirección | flecha | relaciones dirigidas; `COMMUNICATES_WITH`/`ALIAS_OF` simétricas sin flecha |

**Interacciones**

- Click: selecciona nodo; `Shift+Click`: multi-selección.
- Doble click: expande vecindad (respeta `depth` y filtros).
- Botón “Expandir”: llama `getMultiLayerSubgraph` con `root_ids=[id]`, `depth=1`.
- Botón “Fijar”: mantiene el nodo visible al cambiar filtros.
- Botón “Evidencia”: abre `EvidenceInspector`.
- Arrastrar: layout; no persiste posiciones sensibles.
- Zoom/pan: 60 fps objetivo; renderizado WebGL si > 2 000 nodos.
- Teclado: `Tab` navega nodos; `Enter` abre inspector; `Esc` limpia selección.

**Límites**

- Máximo 5 000 nodos y 10 000 aristas en pantalla; si se supera, mostrar “Refina filtros” y no renderizar.
- `maxNodes` de la API es 500; la UI puede acumular varias expansiones hasta 5 000.
- No se muestran nodos `AGENT_STAGED` mezclados con validados; se agrupan en una capa separada con leyenda.

### 3.4 `SpatioTemporalMap`

**Props**

```ts
type MapEvent = {
  id: string;
  eventType: string;
  discipline: Discipline;
  time: string;
  lat?: number;
  lon?: number;
  accuracyM?: number;
  geohash?: string;
  tlp: Tlp;
  entityId?: string;
  maskedSelector?: string;
  evidenceIds: string[];
};

type SpatioTemporalMapProps = {
  events: MapEvent[];
  center?: { lat: number; lon: number };
  radiusM: number;
  window: { from: string; to: string };
  onTimeChange: (window: { from: string; to: string }) => void;
  onSelectEvent: (eventId: string) => void;
  onClusterClick: (eventIds: string[]) => void;
};
```

**Capas**

1. Mapa base (MapLibre GL, estilo oscuro/claro).
2. Eventos individuales (puntos) y clusters.
3. Círculo de radio de consulta.
4. Heatmap de densidad (opcional; TLP ≤ AMBER).
5. Líneas de colocalización SIGINT-SOCMINT (no implican causalidad).

**Reglas**

- Si `event.tlp` es superior al clearance, no se incluye; no se muestra “punto ciego” que revele ubicación.
- Para TLP:RED, sólo geohash de baja precisión o centroide de área; nunca coordenadas exactas.
- El time brush actualiza `window` con debounce 250 ms; no lanza una consulta por píxel.
- El mapa y el timeline están sincronizados: seleccionar un evento resalta ambos.
- `accuracyM` se dibuja como círculo de incertidumbre; si es `null`, no se inventa.
- El mapa no usa tiles públicos que reciban coordenadas sensibles; el proveedor debe ser compatible con OPSEC.

### 3.5 `EvidenceInspector`

**Props**

```ts
type EvidenceInspectorProps = {
  evidenceId: string;
  onVerify: (evidenceId: string) => Promise<{
    valid: boolean;
    payloadSha256: string;
    chainValid: boolean;
    verifiedAt: string;
  }>;
  canDownload: boolean;
  onClose: () => void;
};
```

**Contenido**

| Sección | Campos |
|---|---|
| Identidad | `evidence_id`, `ledger_seq`, `collector_agent`, `discipline`, `source_platform`, `source_uri` |
| Tiempo | `captured_at_utc`, `observed_at_utc`, `ingested_at` |
| Custodia | `payload_sha256`, `prev_hash`, `record_hash`, `chain_valid`, `legal_basis`, `retention_until`, `legal_hold` |
| Clasificación | `tlp`, `admiralty`, `confidence_hint` |
| Adjuntos | nombre, MIME, tamaño, SHA-256, `immutable_uri`, botón “Verificar” |
| Extracto | texto citado con resaltado; nunca HTML crudo |
| Vínculos | entidades/eventos/relaciones que usan la evidencia |

**Reglas**

- El botón “Verificar” llama `GET /api/v1/evidence/{id}/verify`; muestra `payloadSha256`, `chainValid` y hora.
- Si `chainValid=false`, banner rojo y no se permite aprobar propuestas que dependan de esa evidencia.
- `canDownload=false` para TLP:RED o rol sin permiso; el botón se deshabilita con explicación.
- El texto se renderiza como texto plano (React escapa por defecto); no `dangerouslySetInnerHTML`.
- El inspector no muestra selectores completos (IMSI/MAC) salvo `ADMIN` + confirmación + auditoría.
- El hash se puede copiar; no se puede editar.

### 3.6 `ApprovalInbox`

**Props**

```ts
type Proposal = {
  id: string;
  type: "OBSERVABLE" | "RELATIONSHIP" | "EVENT" | "ENTITY_MERGE" | "ALIAS";
  status: "AGENT_STAGED" | "ANALYST_VERIFIED" | "APPLIED" | "REJECTED" | "EXPIRED";
  agent: "HERMES" | "ARACNE" | "ARGOS";
  missionId: string;
  confidence: number;
  admiralty: string;
  tlp: Tlp;
  evidenceIds: string[];
  payload: unknown;
  createdAt: string;
  expiresAt: string | null;
  version: number;
};

type ApprovalInboxProps = {
  proposals: Proposal[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onApprove: (id: string, reason?: string, version?: number) => Promise<void>;
  onReject: (id: string, reason: string, version?: number) => Promise<void>;
  onOpenEvidence: (evidenceId: string) => void;
};
```

**Contenido de una propuesta**

1. Cabecera: tipo, agente, misión, TLP, Almirantazgo, confianza.
2. Payload legible: selector/evento/relación, no JSON crudo por defecto; botón “Ver JSON”.
3. Evidencia: lista de `evidence_ids` con hash y verificación.
4. Diff: para `ENTITY_MERGE`/`ALIAS`, mostrar entidad origen/destino, selectores y alias.
5. Conflictos: relaciones solapadas, TLP, duplicados, `locked_by_analyst`.
6. Acciones: “Aprobar”, “Rechazar”, “Pedir más evidencia” (comentario).
7. Historial: `created_at`, `reviewed_by`, `reviewed_at`, `decision_reason`.

**Reglas**

- Sólo `ANALYST`/`ADMIN` pueden aprobar/rechazar; `VIEWER` sólo lectura.
- Rechazar exige motivo (mínimo 8 caracteres).
- Aprobar una propuesta no escribe el grafo; sólo pasa a `ANALYST_VERIFIED`. La aplicación la hace `ATENEA` y la UI muestra `APPLIED`.
- Optimistic concurrency: se envía `version`; si cambió, `409 PROPOSAL_VERSION_CONFLICT` y se recarga.
- No se permite aprobar si `chainValid=false`, `tlp` excede clearance o falta evidencia.
- Las propuestas `EXPIRED` no se pueden aprobar; sólo archivar/ver.
- La bandeja se actualiza por SSE `proposal.staged`; no hace polling agresivo.
- El botón “Aprobar todo” no existe: cada propuesta se revisa individualmente.

### 3.7 `Timeline`

**Props**

```ts
type TimelineProps = {
  items: Array<{
    id: string;
    type: "EVENT" | "RELATIONSHIP" | "PROPOSAL";
    time: string;
    label: string;
    discipline: Discipline;
    tlp: Tlp;
    confidence: number;
  }>;
  window: { from: string; to: string };
  onWindowChange: (window: { from: string; to: string }) => void;
  onSelect: (id: string) => void;
};
```

**Reglas**

- Agrupa por hora/día según zoom; no muestra más de 500 marcas.
- Colores por disciplina + icono; no sólo color.
- El brush selecciona una ventana y actualiza el mapa/grafo.
- `as_of` se muestra como línea vertical; los eventos posteriores se atenúan.
- No se muestra un evento TLP:RED en un timeline compartido sin permiso.

---

## 4. Contratos de datos y API

### 4.1 Endpoints consumidos

| Vista | Endpoint | Spec |
|---|---|---|
| Grafo | `POST /api/v1/graph/subgraph` | SPEC-03 §4 |
| Mapa | `POST /api/v1/correlation/colocation` | SPEC-03 §3 |
| Alias | `POST /api/v1/analytics/alias-evasion` | SPEC-03 §5 |
| Evidencia | `GET /api/v1/evidence/{id}` + `/verify` | SPEC-01/03 |
| Propuestas | `GET /api/v1/proposals?status=AGENT_STAGED` | SPEC-02 §9.2 |
| Aprobar | `POST /api/v1/proposals/{id}/approve` | SPEC-02 §11 |
| Rechazar | `POST /api/v1/proposals/{id}/reject` | SPEC-02 §11 |
| Misión | `GET /api/v1/missions/{id}` | SPEC.md |
| Búsqueda | `GET /api/v1/search/rag` | v2.2 existente |

### 4.2 Cliente tipado

- Generar tipos desde `contracts/openapi.yaml` con `npm run gen:api`.
- No usar `any` en respuestas de evidencia/propuesta.
- Validar en runtime con Zod o equivalente sólo en el borde de la API; no duplicar esquemas a mano.
- Errores: mapear `TLP_DENIED`, `MISSION_FORBIDDEN`, `PROPOSAL_VERSION_CONFLICT`, `QUERY_TIMEOUT` a mensajes claros; nunca mostrar un stack trace.

### 4.3 Estado y caché

| Estado | Herramienta propuesta | Regla |
|---|---|---|
| Server state | TanStack Query | `staleTime` ≤ 30 s; no cachear TLP:RED |
| UI state | React state/context | filtros, selección, panel abierto |
| URL state | `searchParams` | filtros compartibles; sin selectores sensibles |
| Tiempo real | SSE | propuestas/alertas; reconexión con backoff |
| Formularios | React Hook Form + Zod | validación cliente/servidor |

- Al cambiar de misión, `queryClient.clear()`.
- Al cerrar sesión, limpiar caché y estado.
- No usar `localStorage` para evidencia, selectores, tokens ni propuestas.

---

## 5. Seguridad de UI

| Control | Regla |
|---|---|
| TLP | Banner persistente; componentes reciben `tlp` y no renderizan por encima del clearance |
| RBAC | Acciones deshabilitadas, no sólo ocultas; el servidor vuelve a validar |
| Selectores sensibles | `maskedValue` por defecto; revelar exige `ADMIN` + confirmación + auditoría |
| Coordenadas | TLP:RED sólo geohash/centroide; no tiles públicos con coordenadas exactas |
| XSS | Sin `dangerouslySetInnerHTML`; texto citado como texto; CSP estricta |
| CSRF | Cookies `SameSite=Strict` + token; API key sólo servidor |
| Sesión | Timeout 15 min de inactividad; aviso a los 13 min |
| Cache | `Cache-Control: no-store` para TLP:AMBER+; limpiar al cerrar |
| Logs | No loguear selectores, tokens ni payloads completos |
| Auditoría | Aprobar/rechazar/ver evidencia sensible genera `audit.events` |

---

## 6. Rendimiento y accesibilidad

### 6.1 Presupuestos

| Métrica | Objetivo |
|---|---|
| LCP (dashboard) | < 2.5 s en red 4G |
| INP | < 200 ms |
| Render de grafo 5k nodos | ≥ 30 fps; 60 fps objetivo |
| Cambio de filtro | < 500 ms percibido; debounce 300 ms |
| Carga de evidencia | < 1 s para 5 MiB (sin descarga) |
| Time brush | < 100 ms por frame; consulta debounce 250 ms |
| Memoria | < 500 MB con 5k nodos |

### 6.2 Accesibilidad (WCAG 2.2 AA)

- No usar color como única codificación; añadir forma/icono/etiqueta.
- Contraste mínimo 4.5:1 en texto; 3:1 en componentes.
- Navegación completa por teclado; foco visible.
- `aria-live` para aprobaciones/errores; no para cada evento del mapa.
- Mapas: tabla alternativa de eventos y resumen textual de clusters.
- `prefers-reduced-motion`: desactivar animaciones de layout/zoom.
- Formularios con labels, `aria-describedby`, errores asociados.

---

## 7. Escenarios de aceptación (Gherkin)

```gherkin
Feature: UI multi-INT con evidencia y aprobación

  Scenario: Filtro por disciplina y TLP
    Given un workspace con eventos OSINT, SOCMINT y SIGINT
    When el analista selecciona disciplina SIGINT y TLP:AMBER
    Then el grafo y el mapa muestran sólo eventos SIGINT dentro del TLP
    And el filtro queda reflejado en la URL
    And no se muestra ningún evento TLP:RED

  Scenario: Nodo staged
    Given una propuesta AGENT_STAGED de tipo OBSERVABLE
    When el analista abre el grafo con includeStaged=false
    Then el nodo no aparece en el grafo validado
    And la bandeja muestra la propuesta con badge STAGED

  Scenario: Aprobar propuesta
    Given una propuesta AGENT_STAGED con evidencia válida
    When un ANALYST la aprueba
    Then la propuesta pasa a ANALYST_VERIFIED
    And ATENEA la aplica y la UI muestra APPLIED
    And hasta APPLIED el nodo no aparece como validado

  Scenario: Evidencia alterada
    Given una evidencia cuyo chainValid=false
    When el analista intenta aprobar una propuesta que la usa
    Then la acción Aprobar está deshabilitada
    And se muestra un banner rojo con el hash esperado/real

  Scenario: Conflicto de versión
    Given una propuesta que otro analista ya revisó
    When el analista pulsa Aprobar con version antigua
    Then la API responde 409 PROPOSAL_VERSION_CONFLICT
    And la UI recarga la propuesta sin perder la selección

  Scenario: Mapa TLP:RED
    Given un evento SIGINT TLP:RED
    When un ANALYST con clearance TLP:RED abre el mapa
    Then se muestra sólo geohash/centroide, nunca la coordenada exacta
    And el selector aparece enmascarado
    And la acción de revelar exige confirmación y auditoría

  Scenario: Accesibilidad de grafo
    Given un analista que usa sólo teclado
    When navega por el grafo
    Then puede seleccionar nodos, abrir inspector y expandir
    And el foco es visible
    And la información no depende sólo del color
```

---

## 8. Trazabilidad

| Requisito base | Este spec | Tarea |
|---|---|---|
| R-07 | Grafo/alias/evidencia | TASK-36 |
| R-11 | `as_of` + timeline | TASK-38 |
| R-12 | TLP banner/filtros | TASK-39 |
| R-17 | Búsqueda híbrida | TASK-36 |
| R-24 | Sin exposición de DB | TASK-39 |
| R-25 | Rate limit/errores | TASK-39 |
| DM-E9 | `as_of` | TASK-38 |
| DM-E12 | Bandeja de aprobación | TASK-37 |
| AUD-23 | UI sin mapa/aprobación/evidencia | TASK-36–40 |

### 8.1 SPEC-GAP

- **SPEC-GAP-UI-001:** librería de mapa. Propuesta: MapLibre GL JS; validar OPSEC del proveedor de tiles.
- **SPEC-GAP-UI-002:** si el analista puede revelar selectores completos (IMSI/MAC) y con qué rol. Propuesta: sólo `ADMIN`, con confirmación y auditoría.
- **SPEC-GAP-UI-003:** política de capturas/exportaciones. Propuesta: exportar sólo TLP ≤ GREEN; TLP:RED prohibido.
- **SPEC-GAP-UI-004:** “Pedir más evidencia” crea una tarea para Hermes o un comentario. Propuesta: crea `tasking_order` con estado `PENDING` y no modifica el grafo.

**No se implementa hasta aprobación.**
