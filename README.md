# OSINT Nexus Platform

Monorepo **Spec-Driven Development (SDD)** para una plataforma OSINT con grafo
de conocimiento, ingesta NLP de informes y dashboard interactivo.

> La especificación es la única fuente de verdad. El código se deriva de
> `/specs` a través de los contratos congelados en `/contracts`.

## Estructura

```
.antigravity/   Gobernanza de agentes (constitución + prompts maestros + refs Midjourney)
specs/          Especificaciones funcionales y técnicas (fuente de verdad)
contracts/      Contratos ejecutables compartidos (grafo, extracción, API)
apps/backend/         API + ingesta + Coherence Engine (Python, FastAPI, Celery)
apps/frontend/        Dashboard web (Next.js, Tailwind, Cytoscape, 4 vistas)
apps/informant-agent/ Informante IA (recolección OSINT M2M)
storage/raw_reports/      Informes originales íntegros (volumen cifrado)
storage/processed_chunks/ Chunks con coordenadas para subrayado
deploy/               Infraestructura VPS (Docker Compose, Nginx, hardening)
.opencode/      Agentes OpenCode equivalentes a los agentes SDD
```

## Ciclo SDD

1. **Esqueleto** — `/specs`, `/contracts`, `/.antigravity` creados.
2. **Congelar contratos** — generar y sellar los 3 contratos; cero código de app.
3. **Generación guiada** — los agentes programan `/apps` y `/deploy` leyendo
   los contratos sellados.
4. **Auditoría SDD** — verificación de conformidad antes del despliegue.

Guía completa del ciclo y reparto de agentes: `.antigravity/sdd-constitution.md`.

## Agentes

| Agente | Rol | Zona de escritura |
| :--- | :--- | :--- |
| 0 Orchestrator | Gobierna fases y sella contratos | `/specs`, `/contracts` (sello) |
| 1 Graph DB | Ontología, coherencia y Entity Resolution | `app/db`, `entity_resolver.py` |
| 2 Ingestion NLP | Pre-Flight, extracción y Coherence Engine | `app/services/**`, `app/schemas/**`, `app/workers/**` |
| 3 DevOps/Sec | API, RBAC (humano + M2M), VPS hardening | `app/api`, `app/core`, `/deploy` |
| 4 Frontend UI | Grafo, dossier, workspace, bandeja | `/apps/frontend` |
| 5 Informant AI | Recolección OSINT M2M | `/apps/informant-agent` |

Prompts: `.antigravity/agent-*.prompt.md` (Antigravity) y `.opencode/agents/*.md`
(OpenCode). El Agente 0 es el **Manager Agent** que coordina a los 5 agentes
técnicos (grafo de dependencias y handoffs en su prompt).

Ontología: **11 labels** (5 entidades OSINT + `Report` + `Analysis`,
`IntelRequirement`, `CoherenceAlert` + `User`/`ApiKey`) y **16 relaciones** — ver
`specs/01-graph-ontology.spec.md`.

**Motor de Coherencia Simbiótica:** el Informante IA (`AI_INFORMANT`) consulta el
contexto (Pre-Flight) antes de extraer y referencia los Análisis humanos
(`CORROBORATES`/`CONTRADICTS`/`EXTENDS`/`ANSWERS_REQUIREMENT`); un humano con
`locked_by_analyst` nunca es sobrescrito por la automatización.

Referencia visual UI/UX: `.antigravity/midjourney-ui-prompts.md`.

## Estado actual

- [x] Fase 1 — Esqueleto SDD.
- [x] Fase 2 — Contratos congelados y sellados (**v2.0.0**, ver `contracts/SEAL.md`).
- [x] Fase 3 — Generación/actualización a v2.0 (**backend 334 tests · informant-agent 36 tests · frontend compila**).
- [x] Fase 4 — Auditoría SDD (**12/12 checks**) y test E2E del ciclo simbiótico. Informe: `.antigravity/audit/2026-10-17-sdd-v2.0.md`.
- [ ] Despliegue en VPS (pendiente de entorno con daemon Docker).

> **Contratos SELLADOS v2.0.0** (hashes en `contracts/SEAL.md`). El código se
> genera contra ellos; cualquier cambio de contrato exige nueva spec y nuevo sello.

### Notas de la migración v2.0
- **Coherencia Humano–IA:** Pre-Flight (`/context/preflight`) + Coherence Engine
  (`CORROBORATES`/`CONTRADICTS`/`EXTENDS`/`ANSWERS_REQUIREMENT`) + `CoherenceAlert`.
- **Inmutabilidad analítica:** `locked_by_analyst` protegido en el repositorio (`NodeLockedError` → `409`).
- **M2M:** el Informante IA se autentica por `X-API-Key`; `/ingest/informant` exige `X-Preflight-Token` (sin él → `409`).
- Rutas troncales sin 503 artificial; un 503 solo aparece si falta una dependencia real.
- Pendiente de verificación con servicios vivos: **Neo4j/Redis/Celery** y la **imagen Docker del informant-agent** (daemon no disponible en este entorno).

### Notas de integración (Fase 3)
- Las rutas troncales (`/graph/*`, `/reports/*`, `/ingest`, `/search/rag`, `/info`)
  ya no devuelven 503 artificial; un 503 solo aparece si falta una dependencia real.
- El sistema arranca en **modo recuperación** (`LLM_PROVIDER=null`, por defecto):
  opera sin API key de LLM. Un proveedor real se configura por entorno.
- Pendiente de verificación con servicios vivos: **Neo4j/Redis/Celery** reales y la
  compilación del **frontend** (`npm install` + `npm run build`).
