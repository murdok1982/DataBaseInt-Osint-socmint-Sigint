# OSINT Nexus — Frontend (Dashboard, 4 vistas)

> **Agente 4 — Arquitecto Frontend UI.**
> Specs rectoras: [`specs/05-dashboard-ui-ux.spec.md`](../../specs/05-dashboard-ui-ux.spec.md) (v2.0)
> y [`specs/03-api-and-graphrag.spec.md`](../../specs/03-api-and-graphrag.spec.md) (v2.0).
> Contrato consumido: [`contracts/openapi.yaml`](../../contracts/openapi.yaml) (**v2.0.0, SELLADO**).
> Referencia visual: [`.antigravity/midjourney-ui-prompts.md`](../../.antigravity/midjourney-ui-prompts.md).

Stack: **Next.js 14 (App Router) + TypeScript estricto + Tailwind CSS + Cytoscape.js**.

---

## 1. Cómo arrancar

```bash
cd apps/frontend

# 1) Instalar dependencias
npm install

# 2) Derivar los tipos del contrato OpenAPI -> src/types/api.d.ts (codegen)
npm run gen:api          # openapi-typescript ../../contracts/openapi.yaml

# 3) Desarrollo
npm run dev              # http://localhost:3000

# Verificación
npx tsc --noEmit         # chequeo de tipos (estricto)
npm run lint             # ESLint (next/core-web-vitals)
npm run build            # build de producción (incluye lint + tipos)
```

### Variables de entorno (`.env.example`)
| Variable | Uso |
| :--- | :--- |
| `NEXT_PUBLIC_API_BASE_URL` | Base de la API. Vacío → `/api/v1` (mismo origen / Nginx). |
| `BACKEND_ORIGIN` | Sólo dev: destino del rewrite `/api/v1/*` de Next. |

Autenticación de sesión: access token JWT en memoria (**Bearer**, 30 min,
SPEC-04 §2) + refresh token en cookie **HttpOnly/Secure** (todas las llamadas se
hacen con `credentials: "include"`). **2FA (TOTP)** opcional en el login
(`totp_code`, obligatorio para ADMIN — SPEC-04 §2).

---

## 2. Las 4 vistas (SPEC-05 §0)

Navegables desde la barra de pestañas del dashboard (`src/app/page.tsx`); el
estado del grafo se conserva entre vistas.

| # | Vista | Componente | SPEC-05 |
| :--- | :--- | :--- | :--- |
| 1 | **Grafo interactivo** | `components/graph/*` | §2 |
| 2 | **Visor Split-View** | `components/dossier/*` | §3 |
| 3 | **Workspace de Análisis** | `components/analysis/*` | §4 |
| 4 | **Bandeja de Coherencia** | `components/coherence/*` | §5 |
| — | Ingesta drag & drop | `components/ingest/*` | §6 |
| — | Consola GraphRAG | `components/search/*` | §1/§7 |

Seleccionar un nodo en el grafo (clic) abre el **Dossier** (vista 2) sin perder
el grafo; doble clic o el botón «Expandir vecindad» consultan
`getGraphNeighborhood`.

---

## 3. Árbol de archivos

```
apps/frontend/
├── package.json                 # scripts: dev/build/start/lint/gen:api
├── tsconfig.json                # TypeScript estricto + alias @/*
├── next.config.mjs              # rewrite /api/v1/* -> BACKEND_ORIGIN (dev)
├── tailwind.config.ts           # paleta dark/neon
├── .eslintrc.json / .env.example
└── src/
    ├── types/api.d.ts           # TIPOS GENERADOS por openapi-typescript (v2.0.0)
    ├── services/api.ts          # cliente tipado — los 27 operationId
    ├── lib/labels.ts            # colores por label, relaciones y semántica de confianza
    ├── context/AuthContext.tsx  # JWT + 2FA + rol + can(action) (matriz SPEC-04)
    ├── app/
    │   ├── layout.tsx           # <AuthProvider> + dark shell
    │   ├── globals.css
    │   ├── page.tsx             # shell + navegación de las 4 vistas + transversales
    │   └── login/page.tsx       # acceso (usuario/contraseña + TOTP opcional)
    └── components/
        ├── graph/GraphCanvas.tsx
        ├── graph/GraphFilters.tsx
        ├── graph/Timeline.tsx
        ├── dossier/DossierPanel.tsx
        ├── dossier/ReportViewer.tsx
        ├── dossier/AnalysisCitationCard.tsx
        ├── analysis/AnalysisWorkspace.tsx
        ├── coherence/CoherenceInbox.tsx
        ├── ingest/IngestDropzone.tsx
        └── search/SearchConsole.tsx
```

`src/types/api.d.ts` **se genera** con `npm run gen:api`
(`openapi-typescript`); no se edita a mano. `src/services/api.ts` sólo consume
`components["schemas"][...]` y `operations[...]`, por lo que regenerar el
contrato no rompe el cliente.

---

## 4. Trazabilidad componente ↔ SPEC-05 ↔ `operationId`

| Componente (`src/`) | SPEC-05 | `operationId` de `openapi.yaml` |
| :--- | :--- | :--- |
| `graph/GraphCanvas.tsx` | §2 (Cytoscape, estilo por `label`, doble clic → vecindad, selección → Dossier) | `listGraphNodes`, `listGraphEdges`, `getGraphNeighborhood` |
| `graph/GraphFilters.tsx` | §2 (`label`, `q`, `min_confidence`) | `listGraphNodes`, `listGraphEdges` |
| `graph/Timeline.tsx` | §2 (rango temporal `first_seen`/`last_seen`) | `listGraphNodes` (`from`/`to`) |
| `dossier/DossierPanel.tsx` | §3 (ficha + `risk_score` + `aliases` + `INVESTIGATES`; edición/bloqueo/borrado) | `updateGraphNode`, `lockGraphNode`, `deleteGraphNode` |
| `dossier/ReportViewer.tsx` | §3 (visor PDF, subrayado de `exact_quote` con `page_number` + chunks) | `getReport`, `getReportFile`, `getReportChunks` |
| `dossier/AnalysisCitationCard.tsx` | §3 (cita de análisis humano) | (deriva de `RagResponse.citations[].analysis_id`) |
| `analysis/AnalysisWorkspace.tsx` | §4 (Analysis + RFI + `coherence_score` + evidencias) | `createAnalysis`, `getAnalysis`, `updateAnalysis`, `createIntelRequirement`, `listGraphNodes`, `listGraphEdges`, `postContextPreflight` |
| `coherence/CoherenceInbox.tsx` | §5 (bandeja, aprobar/descartar, CONTRADICTION) | `listCoherenceAlerts`, `resolveCoherenceAlert`, `getAnalysis` |
| `ingest/IngestDropzone.tsx` | §6 (drag & drop, progreso en vivo, `pending_review`) | `createIngestJob`, `getIngestJob`, `ingestProgressWs` |
| `search/SearchConsole.tsx` | §1/§7 (GraphRAG + citas) | `searchRag` |
| `context/AuthContext.tsx` | §1/§7 (sesión + 2FA) | `authLogin`, `authRefresh`, `authLogout`, `authMe` |
| `services/api.ts` | §1 (tipos derivados de OpenAPI) | los **27** `operationId` del contrato |

**Cobertura del contrato (27/27):** `authLogin`, `authRefresh`, `authLogout`,
`authMe`, `listGraphNodes`, `getGraphNode`, `updateGraphNode`, `deleteGraphNode`,
`lockGraphNode`, `listGraphEdges`, `getGraphNeighborhood`, `postContextPreflight`,
`createIngestJob`, `createInformantIngestJob`, `getIngestJob`, `ingestProgressWs`,
`getReport`, `getReportFile`, `getReportChunks`, `createAnalysis`, `getAnalysis`,
`updateAnalysis`, `createIntelRequirement`, `listCoherenceAlerts`,
`resolveCoherenceAlert`, `searchRag`, `getInfo`.

> `createInformantIngestJob` (M2M `AI_INFORMANT`) y `getInfo` se exponen por
> completitud del contrato; el dashboard humano no los invoca (el Informante IA
> es el Agente 5).

---

## 5. Diferenciación visual analista vs. IA (SPEC-05 §2)

| Estado | Estilo (Cytoscape) | Fuente en el contrato |
| :--- | :--- | :--- |
| Verificado por el analista | borde **sólido** (nodo/arista) | `locked_by_analyst: true` |
| Evidencia IA sin verificar | borde **punteado** | `locked_by_analyst !== true` |
| Contradicción | **rojo** | nodo: `CoherenceAlert` `CONTRADICTION` (`entity_id`); arista: `type = "CONTRADICTS"` |

El color de relleno del nodo se asigna por `label` (9 labels del contrato).

---

## 6. Matriz RBAC de la UI (`can(action)`)

`src/context/AuthContext.tsx` replica la matriz de **SPEC-04 §1** (roles
canónicos v2.0: `ADMIN`, `ANALYST`, `VIEWER`; `ANALYST` sustituye a `OPERATOR`).

| `RbacAction` | ADMIN | ANALYST | VIEWER | UI afectada |
| :--- | :---: | :---: | :---: | :--- |
| `graph:read` | Sí | Sí | Sí | grafo + dossier |
| `reports:read` | Sí | Sí | Sí | visor de informes |
| `rag:query` | Sí | Sí | Sí | consola GraphRAG |
| `analysis:read` | Sí | Sí | Sí | workspace (lectura) |
| `ingest:create` | Sí | Sí | **No** | `IngestDropzone` se **oculta** |
| `analysis:write` | Sí | Sí | **No** | alta/edición de Analysis y RFI ocultas |
| `coherence:read` | Sí | Sí | **No** | pestaña Bandeja de Coherencia oculta |
| `coherence:resolve` | Sí | Sí | **No** | botones Aprobar/Descartar ocultos |
| `graph:update` | Sí | Sí | **No** | «Editar / fusionar» oculto |
| `graph:lock` | Sí | Sí | **No** | «Bloquear nodo» oculto |
| `graph:delete` | Sí | **No** | **No** | «Borrar nodo» oculto |
| `admin:manage` | Sí | **No** | **No** | (reservado) |

`can()` sólo **oculta**; cada petición no autorizada sigue siendo rechazada con
`403` por el backend (SPEC-04 §4). La UI no es la seguridad.

---

## 7. Notas de implementación

- **Subrayado de evidencia:** el contrato expone `exact_quote` + `page_number` y
  `Chunk { page_number, char_start, char_end, text }`. `ReportViewer` localiza la
  cita exacta dentro del chunk de la página (con respaldo tolerante a espacios) y
  subraya ese rango; abre el PDF en `#page=N`. SPEC-02 §2 confirma que
  `char_start`/`char_end` son offsets **relativos a la página**.
- **Cita OSINT vs. cita humana:** `RagResponse.citations[]` distingue
  `report_id` (informe OSINT → `ReportViewer`) de `analysis_id` (análisis humano
  → `AnalysisCitationCard`, sin PDF).
- **WebSocket de ingesta:** el navegador no permite cabecera `Authorization` en
  el handshake; `ingestProgressWs` usa la cookie de sesión de mismo origen. El
  contrato v2.0 define el payload (`IngestJob`) y los códigos de cierre
  (4401/4403/4404/4503).
- **Cytoscape** se importa dinámicamente dentro del efecto para preservar el SSR
  del App Router.

---

## 8. SPEC-GAP abiertos (no inventar: elevar al Orquestador)

- **SPEC-GAP-1 — Autenticación del handshake WebSocket.** El contrato v2.0 ya
  define payload y códigos de cierre de `WS /ingest/{job_id}/ws`, pero no cómo se
  autentica el handshake (Bearer no es posible en WebSocket). Se asume la cookie
  de sesión (same-origin); falta confirmación del backend.
- **SPEC-GAP-2 — Sin marca por nodo de "origen IA" / "verificado".** El contrato
  sólo expone `locked_by_analyst`. La UI deriva «evidencia IA sin verificar» de
  `locked_by_analyst !== true`. Si se quiere distinguir explícitamente un
  «borrador IA» de un simple nodo no bloqueado, hace falta un campo dedicado.
- **SPEC-GAP-3 — No hay `GET /analyses` (lista) ni `GET /analyses/{id}/requirements`.**
  El workspace lista `Analysis` e `IntelRequirement` vía
  `GET /graph/nodes?label=Analysis|IntelRequirement`; los RFIs de un expediente se
  muestran desde la sesión y, opcionalmente, desde `POST /context/preflight`
  (global). `PreflightResponse.requirements` no incluye `parent_analysis_id`, por
  lo que no puede filtrarse por expediente.
- **SPEC-GAP-4 — `conflict_detail` no expuesto.** Una `CONTRADICTION` debería
  mostrar `conflict_detail`, pero `CoherenceAlert` sólo lleva `description`; la
  arista `[:CONTRADICTS]` (que sí lo tiene, SPEC-01 §3.2) no es consultable por
  la API. La bandeja muestra `description` y **no** altera la tesis (Regla 9).
- **SPEC-GAP-5 (integración) — Backend aún en v1.0.** `apps/backend` sigue
  sirviendo `evidence_quote`/`text_evidence_span` y **no** publica las rutas
  `/analyses`, `/coherence`, `/graph/nodes/{id}/lock`, `/context/preflight`
  (fuera de la zona del Agente 4). El frontend ya consume el contrato **v2.0.0**;
  hasta que el backend migre, esas vistas devolverán `404`/`403`. Se reporta al
  Orquestador.

---

## 9. Definición de Done (verificación)

- [x] Las 4 vistas existen y son navegables (pestañas en `src/app/page.tsx`).
- [x] El grafo distingue visualmente `locked_by_analyst` (sólido) y evidencia de
      IA sin verificar (punteado) y contradicción (rojo).
- [x] El resaltado de evidencia usa `page_number` + span reales (`exact_quote`).
- [x] La bandeja resuelve alertas y refleja el cambio
      (`PATCH /coherence/alerts/{alert_id}/resolve`).
- [x] Cada llamada de red traza a un endpoint de `openapi.yaml` (27/27).
- [x] Las acciones se ocultan según el rol del operador (`can(action)`).

### Comandos de verificación ejecutados

```bash
cd apps/frontend
npm install
npm run gen:api          # regenera src/types/api.d.ts desde el contrato sellado
npx tsc --noEmit         # ✔ sin errores
npm run lint             # ✔ No ESLint warnings or errors
npm run build            # ✔ compila (Compiled successfully, 5/5 páginas)
```

> **Nota Windows/Node — `npm run build`.** En este host, `fs.readlink` sobre un
> fichero normal lanza `EISDIR` (en POSIX es `EINVAL`), y el snapshot de webpack
> y `@vercel/nft` lo tratan como error (`EISDIR ... readlink`). No es un defecto
> del código (compilación, tipos y lint pasan). En un host Linux/macOS o con un
> `readlink` que devuelva `EINVAL` para no-symlinks, `npm run build` termina sin
> incidencias. Verificación mínima sin workaround: `npx tsc --noEmit` +
> `npm run lint`.

---

## 10. Alcance y límites

- Zona de escritura respetada: **sólo `apps/frontend/**`**. No se modificaron
  `/specs`, `/contracts` (incl. `openapi.yaml`), `/apps/backend`,
  `/apps/informant-agent` ni `/deploy`.
- Los tipos se **generaron** desde el contrato sellado (`npm run gen:api`); no se
  inventaron endpoints ni campos.
