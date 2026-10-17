# SPEC-02: Ingesta, Pre-Flight Context Check y Motor de Coherencia (v2.0)

> Spec rectora del **Agente 2 (Ingesta, Pre-Flight y Coherence Engine)**.
> Contrato derivado: `/contracts/extraction-schema.json`.
> Tecnologías: Python, FastAPI, Celery/Redis, LLMs.

## 1. Flujo en 3 fases (Informante IA)

### Fase 0 — Consulta Pre-Vuelo (`Pre-Flight Context Check`)
Obligatoria (Regla 3: **prohibición de ingesta ciega**).
1. El Informante IA extrae palabras clave preliminares del documento.
2. Llama a `POST /api/v1/context/preflight`.
3. El backend devuelve:
   - a) `Analysis` **activos** que tocan esas entidades/temáticas (`id`, `thesis_summary`).
   - b) `IntelRequirement` **abiertos** (`status: OPEN`).
   - c) **Diccionario de entidades canónicas** (`canonical_name`, `id`, `label`).

### Fase 1 — Extracción Guiada por Contexto
4. El Informante IA procesa el documento **inyectando el contexto** recibido y
   produce un JSON validado contra `/contracts/extraction-schema.json`
   (tripletas + `analytical_context_links`).

### Fase 2 — Evaluación del Motor de Coherencia (backend)
5. Guarda el archivo físico en `/storage/raw_reports/{report_id}` y `chunk`s con
   coordenadas en `/storage/processed_chunks`.
6. `CORROBORATES` → incrementa `coherence_score` del `Analysis` y enlaza evidencias.
7. `CONTRADICTS` → **mantiene intacta** `conclusion_body` del Analista, crea la
   arista `[:CONTRADICTS]` y genera un `CoherenceAlert` **prioritario** con arista
   `[:RAISED_FOR]` hacia el `Analysis` afectado. La **difusión al Analista autor**
   se resuelve por el vínculo `Analysis.author_analyst_id`: la bandeja de
   coherencia de cada analista muestra las alertas de sus propios expedientes (no
   requiere que el motor conozca al autor).
8. `ANSWERS_REQUIREMENT` → enlaza el informe al `IntelRequirement` y notifica al
   Analista (validación manual del cierre). `EXTENDS`/`CITES_ANALYSIS` → enlaces.

## 2. Contrato de Salida del Informante IA

Raíz: `{ report_metadata, extracted_triplets[], analytical_context_links[] }`.

- `extracted_triplets[]`: `{ subject, predicate, object, provenance }`.
- `analytical_context_links[]`: `{ target_id, link_category, confidence,
  coherence_justification, exact_quote }` con
  `link_category ∈ {CORROBORATES, CONTRADICTS, EXTENDS, ANSWERS_REQUIREMENT}`.

**Reglas heredadas (v1.1.0) que siguen vigentes:**
- `predicate` es un **objeto** (`{type, confidence}`), no un `dict` libre
  (`additionalProperties: false`).
- `char_start`/`char_end` son offsets **relativos a la página** (`page_number`).
- `provenance` exige `exact_quote` **literal**; no se inventa ni deduce.
- Si `confidence` se **omite** → `DEFAULT_CONFIDENCE = 0.50`; si viene **fuera de
  rango** → se **rechaza** (nunca se corrige).
- `status` de la tripleta es **derivado** por el pipeline (umbral 0.50); el LLM no lo emite.

**`report_id` es asignado por el servidor** al recibir el informe (el contrato de
salida no lo incluye); el backend lo inyecta en cada arista persistida.

**Esquema Pydantic obligatorio (validación previa a persistir):**
```python
from pydantic import BaseModel, Field
from typing import Literal
from uuid import UUID

EntityLabel = Literal["Person", "Organization", "CyberAsset", "Location", "Event"]
Relation = Literal["CONTROLS", "FINANCED", "USES_ASSET", "LOCATED_AT",
                   "ASSOCIATED_WITH", "PARTICIPATED_IN"]
LinkCategory = Literal["CORROBORATES", "CONTRADICTS", "EXTENDS", "ANSWERS_REQUIREMENT"]

class Entity(BaseModel):
    label: EntityLabel
    name: str
    existing_canonical_id: str | None = None
    attributes: dict = Field(default_factory=dict)

class Predicate(BaseModel):
    type: Relation
    confidence: float = Field(ge=0.0, le=1.0)

class Provenance(BaseModel):
    page_number: int = Field(ge=1)
    char_start: int | None = None
    char_end: int | None = None
    exact_quote: str = Field(min_length=1)

class Triplet(BaseModel):
    subject: Entity
    predicate: Predicate
    object: Entity
    provenance: Provenance

class AnalyticalContextLink(BaseModel):
    target_id: str
    link_category: LinkCategory
    confidence: float = Field(ge=0.0, le=1.0)
    coherence_justification: str = Field(min_length=1)
    exact_quote: str = Field(min_length=1)
```
El JSON Schema de `contracts/extraction-schema.json` y este modelo deben ser
equivalentes (el contrato manda).

## 3. Umbral de confianza
- `confidence < 0.50` → estado `PENDING_HUMAN_REVIEW` (no impacta el grafo).
- `confidence ≥ 0.50` → impacto automático vía Entity Resolution (SPEC-01 §4).

## 4. Desduplicación Pre-Ingesta
1. **Normalización** (lowercase, sin acentos, expandir/colapsar iniciales).
2. **Match exacto** por identificador único (`value`, `registration_id`).
3. **Match vectorial/fonético** sobre `canonical_name` + `aliases` (`entity_names_ft`).
4. **Decisión**: `≥ 0.88` fusionar (delega en `entity_resolver.py`, Agente 1);
   `0.70–0.88` → `PENDING_HUMAN_REVIEW`; `< 0.70` crear.
5. **`existing_canonical_id`**: si el Informante IA lo aporta (Fase 0), se usa como
   candidato de máxima prioridad y **se respeta `locked_by_analyst`** (SPEC-01 §4).

## 5. Arquitectura de Código (FastAPI + Celery/Redis)
- `POST /api/v1/ingest` (analista, multipart) y `POST /api/v1/ingest/informant`
  (IA, JSON del contrato + API Key M2M) encolan la tarea y devuelven `202` + `job_id`.
- Worker Celery (**Agente 2**, `app/workers/`):
  `preflight → chunk → extract → validate → coherence → resolve → persist`.
  Si no hay broker, la API usa `BackgroundTasks` (fallback).
- Estado/progreso del job en Redis; `GET /api/v1/ingest/{job_id}` y WS
  `/api/v1/ingest/{job_id}/ws`.
- **Coherence Engine:** `app/services/coherence_engine.py` (dueño Agente 2),
  aplica §1 Fase 2.
- **Pre-Flight service:** `app/services/preflight.py` (dueño Agente 2),
  resuelve §1 Fase 0.
- Idempotencia por `report_id` (reingesta no duplica).

## 6. Criterios de Aceptación
- [ ] La salida valida al 100% contra `extraction-schema.json` (y Pydantic).
- [ ] La ingesta del Informante IA **sin** pre-vuelo previo se rechaza (`409`).
- [ ] `CONTRADICTS` **nunca** altera `conclusion_body`; crea `CoherenceAlert` + arista.
- [ ] `CORROBORATES` incrementa `coherence_score` y enlaza evidencias.
- [ ] `ANSWERS_REQUIREMENT` enlaza y notifica al Analista.
- [ ] `exact_quote` verificable en el texto original (página y span).
- [ ] Reingesta del mismo `report_id` idempotente.
- [ ] Umbral 0.50 y cortes 0.88/0.70 deterministas y testeados.
- [ ] La subida responde `202`; el progreso llega por WS.
