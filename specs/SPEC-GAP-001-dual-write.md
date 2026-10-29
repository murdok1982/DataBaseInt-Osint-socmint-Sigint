# SPEC-GAP-001 — Dual-write Neo4j → PostgreSQL (TASK-42…46)

> **Estado:** abierto · **Bloquea:** TASK-42, TASK-43, TASK-44
> **Origen:** Constitución, Ley 10 (fallo ruidoso): ante ambigüedad se detiene y eleva una SPEC-GAP.
> **Decide:** humano (propietario de `/specs`).

## 1. Contradicción detectada

La §13 de `SPEC.md` (estrategia strangler y mapeo de datos v2.2 → v3) apunta a tablas que el DDL v3.1 aprobado (`contracts/v3/db-contract-v3.1.sql`, sellado provisional 2026-10-29) **no contiene**:

| Destino citado en SPEC §13 | ¿Existe en v3.1? | Lo que la §13 necesita |
|---|---|---|
| `staging.raw_intel_ledger` | No (renombrado a `legacy_*`; vista de compatibilidad) | Destino de `Report` |
| `intel.intel_entities` / `intel.intel_relationships` | No (compatibilidad de solo lectura) | Destino de `Person`, `Organization`, `CyberAsset`, relaciones |
| `intel.coherence_alerts` | **No** | Destino de `CoherenceAlert` |
| `intel.intelligence_reports` | **No** | Destino de `Analysis` |
| `intel.eeis` | **No** | EEI de `IntelRequirement` |
| `staging.migration_quarantine` | **No** | Cuarentena M-01…M-03 (§13.4) |
| `external_references` | **No** | `registration_id` de `Organization` |
| `v_orphan_check` | **No** | Regla M-10 (bloquea el corte) |

Además, el grafo v2.2 tiene nodos (`Analysis`, `IntelRequirement`, `CoherenceAlert`, `User`, `ApiKey`) que el modelo v3.1 no representa todavía. Un adaptador no puede escribirlos sin inventar tablas o relaciones, lo que la constitución prohíbe.

El `GraphRepository` de Neo4j tiene unos 20 métodos de escritura. El repositorio PostgreSQL implementa hoy sólo lecturas y la decisión de propuestas (`apps/backend/app/db/postgres_v31_repository.py`).

## 2. Preguntas que requieren decisión

1. **Nodos analíticos.** ¿Dónde viven `Analysis`, `IntelRequirement` y `CoherenceAlert` en v3?
   - (a) Nuevas tablas `intel.analyses`, `intel.requirements`, `intel.coherence_alerts` en un DDL **v3.2**. Cambia un contrato sellado; requiere re-sello.
   - (b) Mapear a `intel.intelligence_events` con un tipo específico. Sin cambio de DDL, pero pierde estructura de análisis.
   - (c) Mantenerlos sólo en Neo4j durante el dual-write y cerrar el mapeo en una fase posterior. Es la opción de menor riesgo y la que recomiendo.
2. **Nombres de destino.** ¿Se reescribe el mapeo de §13.2 contra el modelo v3.1 (`intel.core_entities`, `intel.observables`, `intel.intelligence_relationships`, `staging.raw_evidence_vault`)? Recomiendo sí, con una tabla de equivalencias aprobada.
3. **Cuarentena y M-10.** ¿Se crean `staging.migration_quarantine` y `v_orphan_check` en v3.1 (cambio de contrato) o se implementan fuera de la base, en el ETL?
4. **Canario (fase C).** Con lectura 10 % en PostgreSQL y escritura doble, ¿qué se hace si las dos lecturas divergen? Propuesta: la lectura canario compara y registra la divergencia, nunca la sirve al analista.
5. **Dependencias.** TASK-42 depende de TASK-14 y TASK-29. Según `specs/TASKS.md` §0.0, TASK-29 está hecha y TASK-14 está "verificada manualmente", no automatizada en CI. ¿Basta esa verificación manual para desbloquear TASK-42, o se exige la verificación automatizada primero?
6. **Atomicidad.** La escritura en Neo4j y en PostgreSQL no es transaccional entre sí. ¿Cuál es la fuente de verdad mientras dure la fase B? Propuesta: el ledger de PostgreSQL es primario; la proyección a Neo4j es reconstruible y su fallo no pierde evidencia (criterio de TASK-42).

## 3. Lo que puede avanzar sin esta decisión

- Factoría `GRAPH_BACKEND=neo4j|postgres|dual` con el backend `neo4j` como comportamiento por defecto, sin cambios de lectura.
- Métricas de divergencia y pruebas de la factoría.

Esto **no** implementa dual-write; sólo deja el interruptor preparado.

## 4. Criterios de aceptación (TASK-42, sin cambios)

- Un fallo de proyección no pierde evidencia del ledger.
- Rollback a Neo4j con `GRAPH_BACKEND=neo4j`.
