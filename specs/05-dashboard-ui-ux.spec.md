# SPEC-05: Dashboard UI/UX — 4 Vistas (v2.0)

> Spec rectora del **Agente 4 (Frontend UI)**. Consume `/contracts/openapi.yaml`.
> La UI nunca conoce Cypher ni la base de datos: solo consume la API.
>
> **Referencia visual:** `.antigravity/midjourney-ui-prompts.md` (estilo Palantir
> Gotham / Maltego / Recorded Future).

## 0. Las 4 vistas obligatorias (v2.0)

1. **Grafo interactivo** — diferenciando visualmente entidades tocadas por el
   Analista vs. evidencias del Informante IA.
2. **Visor Split-View** — informe original (PDF/texto) con la cita exacta subrayada.
3. **Workspace de Análisis Humano** — creación/edición de `Analysis` e `IntelRequirement`.
4. **Bandeja de Coherencia** — aprobar/rechazar alertas y contradicciones de la IA.

## 1. Componentes y rutas (`src/`)
| Componente | Ruta | Vistas |
| :--- | :--- | :--- |
| Grafo interactivo | `components/graph/` | 1 |
| Dossier split-view + visor PDF | `components/dossier/` | 2 |
| Workspace analítico | `components/analysis/` | 3 |
| Bandeja de coherencia | `components/coherence/` | 4 |
| Ingesta drag & drop | `components/ingest/` | transversal |
| Consola GraphRAG | `components/search/` | transversal |
| Auth/sesión + 2FA | `context/` | — |
| Cliente API | `services/` | tipos derivados de OpenAPI |

## 2. Grafo interactivo (Vista 1)
- Motor **Cytoscape**. Layout y estilo por `label`; **reales vs. borrador IA** por
  estilo: nodos/aristas con `locked_by_analyst: true` → borde sólido; evidencia
  de IA sin verificar → borde punteado; contradicción → rojo.
- Filtros: `label`, rango temporal (`first_seen`/`last_seen`), `min_confidence`.
- Selección de nodo → abre Dossier (#2) sin perder el grafo.
- Acción *Bloquear* (`locked_by_analyst`) visible solo para `ANALYST`/`ADMIN`.

## 3. Visor Split-View (Vista 2)
- Panel izquierdo: ficha de entidad (propiedades + `risk_score` + `aliases`) y
  lista de análisis que la investigan (`INVESTIGATES`).
- Panel derecho: **visor de PDF** que subraya `exact_quote` usando `page_number`
  y coordenadas de `/api/v1/reports/{id}/chunks`.
- Cada relación muestra su `evidence_quote` y enlace al informe origen.
- Distingue cita de **informe OSINT** de cita de **análisis humano**.

## 4. Workspace de Análisis (Vista 3)
- Alta/edición de `Analysis` (`thesis_summary`, `conclusion_body`, `status`).
- Creación de `IntelRequirement` (RFI) con `priority`.
- Muestra `coherence_score` e historial de evidencias (`CORROBORATES`/`EXTENDS`).
- La IA **no** edita aquí: el Analista es la única autoridad.

## 5. Bandeja de Coherencia (Vista 4)
- Lista `GET /api/v1/coherence/alerts` (`PENDING_ANALYST` primero).
- Cada alerta muestra `alert_type`, `description`, el `Analysis` afectado y la
  entidad implicada; botones **Aprobar**/**Descartar**
  (`PATCH /coherence/alerts/{id}/resolve`).
- Una `CONTRADICTION` muestra la discrepancia (`conflict_detail`) sin alterar la tesis.

## 6. Ingesta (transversal)
- Zona drag & drop → `POST /api/v1/ingest` (analista).
- Progreso en vivo por WS `/api/v1/ingest/{job_id}/ws`.
- Tripletas de baja confianza (`PENDING_HUMAN_REVIEW`) visibles vía `pending_review`.

## 7. Accesibilidad y UX
- Estados vacíos, de carga y de error explícitos.
- Las acciones no permitidas por rol (SPEC-04 §1) se **ocultan**; la seguridad
  real vive en el backend.
- Contraste AA; navegación por teclado en grafo, dossier y bandeja.

## 8. Criterios de Aceptación
- [ ] Las 4 vistas existen y son navegables.
- [ ] El grafo distingue visualmente nodos `locked_by_analyst` y evidencia de IA.
- [ ] El resaltado de evidencia usa `page_number` + span reales.
- [ ] La bandeja resuelve alertas y refleja el cambio.
- [ ] Cada llamada de red traza a un endpoint de `openapi.yaml`.
- [ ] Las acciones se ocultan/aplican según el rol del operador.
