# SPEC-00: Visión Global, Arquitectura Global y Ciclo Simbiótico

## 1. Propósito
Plataforma OSINT que transforma informes no estructurados en un **grafo de
conocimiento trazable**, y que hace **coherente** la recolección automática de
una IA (**Informante IA**) con el trabajo estratégico de los **Analistas
Humanos**, sin que la máquina sobrescriba nunca la verdad verificada por el humano.

## 2. Componentes
- **Frontend** (Next.js + Tailwind + Cytoscape): grafo, dossier, workspace analítico, bandeja de coherencia.
- **Backend** (Python + FastAPI + Celery): API, RBAC, **Pre-Flight Context Check**, **Coherence Engine**, GraphRAG.
- **Grafo** (Neo4j / Memgraph): entidades, documentos, expedientes y alertas.
- **Informant Agent** (`/apps/informant-agent`): servicio autónomo de recolección OSINT con cliente M2M.
- **Redis**: cola Celery y caché de jobs.
- **Storage**: `/storage/raw_reports`, `/storage/processed_chunks`.

## 3. Ciclo Simbiótico Analista ↔ Informante IA
```
Analista crea Analysis (tesis) + IntelRequirement (RFI)
        │
        ▼
Informante IA: Pre-Flight (/context/preflight)  ──►  Analysis activos + RFIs + entidades canónicas
        │
        ▼
Extracción guiada por contexto  ──►  extraction-schema.json (tripletas + analytical_context_links)
        │
        ▼
Coherence Engine (backend):
   CORROBORATES → +coherence_score
   CONTRADICTS  → arista + CoherenceAlert (no toca la tesis)
   ANSWERS_REQUIREMENT → cierra la brecha (notifica al Analista)
        │
        ▼
Analista resuelve alertas y actualiza la conclusión (única autoridad sobre la verdad)
```

## 4. Límites (out of scope)
- No recolección ofensiva ni escaneo de sistemas de terceros.
- La IA no concluye estratégicamente: solo extrae y referencia evidencia.
- Ninguna automatización altera datos con `locked_by_analyst: true`.

## 5. Principios no funcionales
- **Trazabilidad doble** (fuente física + contexto analítico).
- **Inmutabilidad analítica** (`locked_by_analyst`).
- **Aislamiento de red** (la DB nunca se expone).
- **Contratos primero** (interfaces congeladas y selladas).

## 6. Criterios de aceptación global
- [ ] Cualquier nodo/relación se rastrea hasta informe + cita exacta.
- [ ] Ningún proceso de IA altera un nodo con `locked_by_analyst: true`.
- [ ] Toda ingesta del Informante IA pasa por Pre-Flight (Regla 3).
- [ ] Ningún puerto de DB publicado en producción.

## 7. Mapa de specs
| Spec | Cubre |
| :--- | :--- |
| SPEC-01 | Ontología (entidades, Analysis, IntelRequirement, CoherenceAlert) y Entity Resolution |
| SPEC-02 | Pre-Flight, extracción referenciada y Coherence Engine |
| SPEC-03 | API REST/WS (Analistas) y M2M (Informante IA); GraphRAG |
| SPEC-04 | Seguridad: JWT/Argon2id + API Keys M2M + RBAC + VPS |
| SPEC-05 | UI: grafo, dossier PDF, workspace analítico, bandeja de coherencia |
| SPEC-06 | Operación y cumplimiento: bitácora de auditoría, retención, backups, observabilidad, rotación de secretos |

Trazabilidad criterio de aceptación → evidencia: `specs/TRACEABILITY.md`
(verificada por `scripts/audit_sdd.py`).
