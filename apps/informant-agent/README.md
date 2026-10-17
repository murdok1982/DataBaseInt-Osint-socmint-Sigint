# Agente 5 — Informant Agent (`AI_INFORMANT`)

Servicio autónomo de **recolección OSINT** de OSINT Nexus. Es un cliente **M2M**
(API Key `X-API-Key`) que alimenta el grafo **solo a través de la API**; **nunca**
escribe en la base de datos ni en otros `/apps`.

- Specs rectoras: `specs/02-ingestion-pipeline.spec.md`, `specs/03-api-and-graphrag.spec.md`.
- Contrato de salida (**sellado, solo lectura**): `contracts/extraction-schema.json` (v2.0).
- Contrato de API: `contracts/openapi.yaml` (v2.0).
- Zona de escritura: `apps/informant-agent/**`.

## Flujo (protocolo obligatorio)

```
                 ┌──────────────────────────── Fase 0 (obligatoria) ───────────────────────────┐
 fuente OSINT →  │ POST /api/v1/context/preflight  {keywords[], entity_hints[]}  (X-API-Key)    │
 (Collector)     │ ← {preflight_token, analyses[], requirements[], canonical_entities[]}         │
                 └──────────────────────────────────────────────────────────────────────────────┘
                        │
                        ▼
   Fase 1  Extracción guiada por contexto (inyecta el contexto recibido)
           · tripletas {subject, predicate, object, provenance{page_number, exact_quote}}
           · usa existing_canonical_id del diccionario canónico cuando coincide
           · analytical_context_links {target_id, link_category, confidence,
                                        coherence_justification, exact_quote}
                        │
                        ▼
   Validación 100% contra contracts/extraction-schema.json (jsonschema + Pydantic)
                        │
                        ▼
   Envío  POST /api/v1/ingest/informant  (multipart: file + payload)
          Headers: X-API-Key + X-Preflight-Token     → 202 {job_id, status, ...}
```

**Prohibición de ingesta ciega (Regla 3 / SPEC-03 §5).** El pipeline ejecuta
siempre el pre-vuelo antes de ingerir; si el `preflight_token` falta o está vacío,
**aborta sin contactar** `/ingest/informant`. El cliente HTTP aplica la misma
barrera: rechaza la ingesta sin un token emitido por un pre-vuelo previo
(`PreflightRequired`) **sin emitir ninguna petición**.

### Referencia a Análisis Humanos

`LinkAnalyzer` evalúa el documento frente a las `thesis_summary` y las preguntas de
los `IntelRequirement` abiertos y emite, por cada coincidencia:

| `link_category` | Cuándo |
| :--- | :--- |
| `CORROBORATES` | La evidencia respalda la tesis (solapamiento + marcadores de confirmación). |
| `CONTRADICTS` | Datos verificables que chocan con la tesis (marcador de negación/refutación). |
| `EXTENDS` | Aborda el tema y amplía con nueva evidencia, sin confirmar ni refutar. |
| `ANSWERS_REQUIREMENT` | Responde a la pregunta de un `IntelRequirement` (`target_id` = RFI). |

La tesis **nunca** se sobrescribe: el servicio solo produce un *vínculo* con
`coherence_justification`; la decisión (`CoherenceAlert`, `coherence_score`, etc.)
es del backend (SPEC-02 §1 Fase 2).

### Rigor de evidencia

Toda tripleta y todo vínculo llevan `exact_quote` **literal** + `page_number`. El
extractor **descarta** cualquier relación cuyo `quote` no aparezca literalmente en
la página indicada (no se inventa ni deduce).

## Estructura

```
apps/informant-agent/
├── informant_agent/
│   ├── api_client.py      # Cliente M2M (preflight + ingest) con guarda anti-ingesta-ciega
│   ├── pipeline.py        # Orquestación preflight → extraer → validar → ingerir
│   ├── extractor.py       # Extracción guiada por contexto + grounding + IDs canónicos
│   ├── coherence.py       # LinkAnalyzer (CORROBORATES/CONTRADICTS/EXTENDS/ANSWERS_REQUIREMENT)
│   ├── contract.py        # Carga/valida el contrato sellado con jsonschema
│   ├── models.py          # Modelos Pydantic espejo de los contratos sellados
│   ├── config.py          # Settings por variables de entorno
│   ├── testing.py         # FakeInformantApi (mock sin red)
│   ├── cli.py             # CLI (`run`, `extract`)
│   └── collectors/        # Interfaz Collector + LocalJsonCollector + TextDirCollector
└── tests/                 # Tests (contrato, protocolo, coherencia, grounding, recolectores)
```

## Variables de entorno

| Variable | Descripción | Por defecto |
| :--- | :--- | :--- |
| `INFORMANT_API_BASE_URL` | Base de la API. | `http://localhost:8000` |
| `INFORMANT_API_KEY` | API Key M2M `AI_INFORMANT` (header `X-API-Key`). **Obligatoria** para la API real. | — |
| `INFORMANT_SOURCE_DIR` | Directorio de fuentes abiertas. | — |
| `INFORMANT_TIMEOUT` | Timeout HTTP (segundos). | `30` |
| `INFORMANT_CONTRACT_SCHEMA` | Ruta al contrato sellado (si no, `contracts/extraction-schema.json`). | autodetectado |

> **Cero secretos en el repo (SPEC-04 §2):** la API Key se aporta por entorno/CLI.

## Uso

```bash
# Dependencias
pip install -r requirements.txt

# Ciclo completo sobre un directorio de fuentes (*.json), con pre-vuelo y envío M2M
export INFORMANT_API_KEY=...            # Windows: $env:INFORMANT_API_KEY="..."
python -m informant_agent run --source-dir ./sources

# Extracción + validación SIN red (contexto vacío o un PreflightResponse.json)
python -m informant_agent extract --source-dir ./sources
python -m informant_agent extract --source-dir ./sources --context preflight.json
```

### Formato de fuente (recolector JSON)

```json
{
  "title": "Informe X",
  "source_type": "NEWS",
  "source_url_or_origin": "https://...",
  "reliability_rating": "B",
  "keywords": ["Acme Corp", "State Bank"],
  "pages": [{ "page_number": 1, "text": "State Bank financed Acme Corp in 2021." }],
  "assertions": [
    { "subject_name": "State Bank", "predicate": "FINANCED", "object_name": "Acme Corp",
      "quote": "State Bank financed Acme Corp", "page_number": 1, "confidence": 0.8 }
  ]
}
```

`source_type` ∈ `{OSINT_PDF, NEWS, TELEGRAM, RSS, PUBLIC_REGISTRY}` y
`reliability_rating` ∈ `{A..F}` (enum del contrato sellado). Si se omiten
`assertions`, el extractor detecta relaciones por patrones (ES/EN) sobre el texto.

### Extender los recolectores

Implementa la interfaz `informant_agent.collectors.base.Collector`
(`collect() -> Iterable[RawDocument]`) y pásala al pipeline. Ya hay dos ejemplos:
`LocalJsonCollector` (`*.json`) y `TextDirCollector` (`*.txt`).

## Tests

```bash
cd apps/informant-agent
python -m pytest -q
```

Cubren: validación 100% contra el contrato sellado, **prohibición de ingesta
ciega** (pipeline y cliente HTTP con `httpx.MockTransport`, sin red), que un
documento que **contradice** una tesis produce `CONTRADICTS` **sin editar la
tesis**, el grounding de `exact_quote`/`page_number`, la aplicación de
`existing_canonical_id` y los recolectores.

## SPEC-GAP

- **Divergencia backend ↔ contrato sellado (fuera de mi zona).**
  `apps/backend/app/schemas/extraction.py` espeja un contrato **antiguo** (7 labels
  + 9 predicados, raíz `{report_id, triplets}`), mientras el contrato **sellado**
  `contracts/extraction-schema.json` (v2.0, 2026-10-17) define **5 labels** +
  **6 predicados** y raíz `{report_metadata, extracted_triplets,
  analytical_context_links}`. Este servicio sigue el contrato **sellado** (manda,
  SPEC-02 §2). Requiere reconciliación por el Orquestador/Agente 2.
- **`source_type` entre contratos.** `openapi.yaml` (`Report.source_type`) incluye
  `MANUAL`, ausente en `extraction-schema.json`. El Informante IA no puede emitir
  `MANUAL` (reservado a subida humana); se documenta para evitar ambigüedad.
