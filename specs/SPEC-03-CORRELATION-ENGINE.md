# SPEC-03 — Motor de Correlación y Endpoints Analíticos

> **Estado: DRAFT v0.1.0 — PROPUESTA, no aprobada.**
> Depende de: `SPEC-01-DATA-MODEL.md` (tablas/índices) y `SPEC-02-HERMES-HARNESS-INGESTION.md` (tools/estado).
> Aprobador requerido: usuario. Sin aprobación no se modifica `apps/backend/app/api/**`, `app/services/**` ni `contracts/openapi.yaml`.

---

## 1. Contexto y objetivo

El motor de correlación responde tres preguntas operativas que hoy no se pueden contestar:

1. **Colocalización SIGINT-SOCMINT:** ¿qué emisiones RF/IMSI/MAC ocurrieron en el mismo lugar y ventana temporal que eventos SOCMINT/OSINT?
2. **Expansión de subgrafos multicapa:** ¿qué entidades y relaciones conectan un conjunto de raíces, filtrando por disciplina, Almirantazgo, TLP y confianza, y reconstruyendo el estado en una fecha?
3. **Detección de evasión de alias:** ¿qué identidades están rotando handles, cambiando de plataforma o reutilizando alias para ocultar continuidad?

Las tres consultas se ejecutan en PostgreSQL, nunca en el cliente, y devuelven **evidencia enlazada** y **motivos** para que el analista pueda auditar el resultado. Ninguna escritura se produce desde este motor.

---

## 2. Convenciones de API

| Elemento | Regla |
|---|---|
| Prefijo | `/api/v1` (consistente con el frontend actual) |
| Autenticación | JWT humano o `X-API-Key` de agente según operación |
| RBAC | `VIEWER` lectura limitada; `ANALYST` lectura completa; `ADMIN` administración; agentes sólo su misión |
| TLP | `intel.tlp_rank(evento) <= intel.tlp_rank(clearance)`; nunca sedevuelve un dato por encima |
| Paginación | `limit` 1–500; `cursor` opaco; `truncated` explícito |
| Errores | Sobre `{"error":{"code","message","details","request_id"}}` |
| Auditoría | Cada consulta registra `audit.events` con hash de parámetros y conteo |
| Rate limit | 30 req/min por usuario; 120 req/min por agente; `429` + `Retry-After` |
| Timeout | `statement_timeout = 5s`; `lock_timeout = 1s` |
| Cache | Sólo respuestas no sensibles, TTL ≤ 60 s; nunca TLP:RED |

### 2.1 Códigos de error

| Código | HTTP | Descripción |
|---|---|---|
| `INVALID_GEOMETRY` | 422 | Lat/lon fuera de rango o radio inválido |
| `INVALID_WINDOW` | 422 | `from >= to` o ventana > 30 días |
| `TLP_DENIED` | 403 | Clearance insuficiente |
| `MISSION_FORBIDDEN` | 403 | El usuario/agente no pertenece a la misión |
| `TOO_MANY_NODES` | 413 | `max_nodes` excedido; usar paginación |
| `QUERY_TIMEOUT` | 504 | `statement_timeout` |
| `RATE_LIMITED` | 429 | Límite excedido |
| `INTERNAL` | 500 | Error no clasificado |

---

## 3. Consulta 1 — Colocalización SIGINT-SOCMINT

### 3.1 Endpoint

```http
POST /api/v1/correlation/colocation
operationId: findSigintSocmintColocation
```

### 3.2 Request

```json
{
  "mission_id": "0192f4c2-7d1a-7c3e-9a11-2f6b1c8d9e01",
  "center": { "lat": 40.4168, "lon": -3.7038 },
  "radius_m": 500,
  "window": {
    "from": "2026-10-01T00:00:00Z",
    "to": "2026-10-02T00:00:00Z"
  },
  "disciplines": ["SIGINT", "SOCMINT"],
  "min_confidence": 60,
  "max_tlp": "TLP:AMBER",
  "max_time_delta_s": 900,
  "limit": 200
}
```

| Campo | Tipo | Regla |
|---|---|---|
| `mission_id` | UUID | obligatorio; el usuario debe tener acceso |
| `center` | `{lat,lon}` | obligatorio; `lat ∈ [-90,90]`, `lon ∈ [-180,180]` |
| `radius_m` | number | 10–5000; por defecto 500 |
| `window.from/to` | date-time | obligatorio; `to > from`; máximo 30 días |
| `disciplines` | array | debe incluir `SIGINT` y al menos una de `SOCMINT`/`OSINT` |
| `min_confidence` | int | 0–100; por defecto 60 |
| `max_tlp` | enum | el usuario no puede pedir más que su clearance |
| `max_time_delta_s` | int | 0–86400; por defecto 900 |
| `limit` | int | 1–500 |

### 3.3 Response

```json
{
  "query": {
    "mission_id": "…",
    "center": { "lat": 40.4168, "lon": -3.7038 },
    "radius_m": 500,
    "window": { "from": "…", "to": "…" }
  },
  "clusters": [
    {
      "cluster_id": "coloc-0192…",
      "center": { "lat": 40.4169, "lon": -3.7037 },
      "radius_m": 180,
      "score": 0.91,
      "disciplines": ["SIGINT", "SOCMINT"],
      "events": [
        {
          "event_id": "…",
          "discipline": "SIGINT",
          "event_type": "RF_EMISSION",
          "event_time": "2026-10-01T21:14:03Z",
          "distance_m": 42,
          "confidence": 82,
          "tlp": "TLP:AMBER",
          "entity_id": "…",
          "observable_id": "…",
          "masked_selector": "IMSI:***345",
          "evidence_ids": ["…"]
        },
        {
          "event_id": "…",
          "discipline": "SOCMINT",
          "event_type": "SOCIAL_POST",
          "event_time": "2026-10-01T21:17:40Z",
          "distance_m": 58,
          "confidence": 74,
          "tlp": "TLP:GREEN",
          "entity_id": "…",
          "observable_id": "…",
          "evidence_ids": ["…"]
        }
      ],
      "time_delta_s": 217,
      "evidence_ids": ["…", "…"]
    }
  ],
  "truncated": false,
  "next_cursor": null,
  "suppressed": 0
}
```

### 3.4 SQL canónico

```sql
WITH params AS (
    SELECT
        $1::uuid  AS mission_id,
        $2::float8 AS center_lat,
        $3::float8 AS center_lon,
        $4::float8 AS radius_m,
        $5::timestamptz AS win_from,
        $6::timestamptz AS win_to,
        $7::smallint AS min_confidence,
        $8::text AS clearance,
        $9::int AS max_delta_s
),
center AS (
    SELECT ST_SetSRID(ST_MakePoint(center_lon, center_lat), 4326)::geography AS g
    FROM params
),
sigint AS (
    SELECT e.event_id, e.event_time, e.location, e.confidence,
           e.tlp_classification, e.entity_id, e.observable_id,
           e.source_evidence_id, e.payload
    FROM intel.intelligence_events e, params p, center c
    WHERE e.mission_id = p.mission_id
      AND e.discipline = 'SIGINT'
      AND e.revoked = false
      AND e.event_time >= p.win_from
      AND e.event_time <  p.win_to
      AND e.confidence >= p.min_confidence
      AND e.location IS NOT NULL
      AND intel.tlp_rank(e.tlp_classification::text) <= intel.tlp_rank(p.clearance)
      AND ST_DWithin(e.location, c.g, p.radius_m)
),
socmint AS (
    SELECT e.event_id, e.event_time, e.location, e.confidence,
           e.tlp_classification, e.entity_id, e.observable_id,
           e.source_evidence_id, e.payload
    FROM intel.intelligence_events e, params p, center c
    WHERE e.mission_id = p.mission_id
      AND e.discipline IN ('SOCMINT','OSINT')
      AND e.revoked = false
      AND e.event_time >= p.win_from
      AND e.event_time <  p.win_to
      AND e.confidence >= p.min_confidence
      AND e.location IS NOT NULL
      AND intel.tlp_rank(e.tlp_classification::text) <= intel.tlp_rank(p.clearance)
      AND ST_DWithin(e.location, c.g, p.radius_m)
),
pairs AS (
    SELECT
        s.event_id AS sigint_event_id,
        o.event_id AS socmint_event_id,
        s.entity_id AS sigint_entity_id,
        o.entity_id AS socmint_entity_id,
        s.observable_id AS sigint_observable_id,
        o.observable_id AS socmint_observable_id,
        ST_Distance(s.location, o.location) AS distance_m,
        abs(extract(epoch FROM (s.event_time - o.event_time))) AS delta_s,
        (s.confidence + o.confidence) / 2.0 AS avg_confidence,
        s.event_time AS sigint_time,
        o.event_time AS socmint_time,
        s.tlp_classification AS sigint_tlp,
        o.tlp_classification AS socmint_tlp,
        s.source_evidence_id AS sigint_evidence,
        o.source_evidence_id AS socmint_evidence
    FROM sigint s
    JOIN socmint o
      ON ST_DWithin(s.location, o.location, (SELECT radius_m FROM params))
     AND abs(extract(epoch FROM (s.event_time - o.event_time)))
         <= (SELECT max_delta_s FROM params)
    WHERE s.event_id < o.event_id
),
scored AS (
    SELECT *,
           greatest(0, least(1,
               0.50 * (1 - distance_m / (SELECT radius_m FROM params))
             + 0.30 * (1 - delta_s / greatest((SELECT max_delta_s FROM params), 1))
             + 0.20 * (avg_confidence / 100.0)
           )) AS score
    FROM pairs
)
SELECT *
FROM scored
ORDER BY score DESC, delta_s ASC
LIMIT $10;
```

### 3.5 Agrupación en clusters

El endpoint agrupa pares por proximidad y tiempo:

```sql
-- Seudocódigo: en la implementación se materializa en una CTE.
-- 1. Ordenar pares por score DESC.
-- 2. Asignar cluster_id al primer par no asignado.
-- 3. Absorber pares con distancia <= 2*radius_m y delta_s <= max_delta_s.
-- 4. Calcular centroide (ST_Centroid) y radio (ST_MaxDistance).
```

**Reglas:**

- `ST_DWithin` usa el índice GiST `idx_events_location_gist`.
- No se devuelven pares con `distance_m = 0` y `delta_s = 0` como “coincidencia fuerte” si ambos eventos comparten el mismo `evidence_id`; se marcan `same_evidence=true` y no se usan para corroboración.
- La consulta no mezcla misiones.
- Los selectores sensibles se enmascaran (`IMSI:***345`) salvo que el rol pueda verlos; el campo `masked_selector` es el único expuesto por defecto.
- `suppressed` cuenta filas ocultas por TLP; no se revela su contenido.

---

## 4. Consulta 2 — Subgrafo multicapa

### 4.1 Endpoint

```http
POST /api/v1/graph/subgraph
operationId: getMultiLayerSubgraph
```

### 4.2 Request

```json
{
  "mission_id": "…",
  "root_ids": ["…", "…"],
  "depth": 2,
  "direction": "BOTH",
  "disciplines": ["OSINT", "SOCMINT", "SIGINT"],
  "admiralty": { "min": "C3", "max": "A1" },
  "min_confidence": 55,
  "max_tlp": "TLP:AMBER",
  "as_of": "2026-10-01T00:00:00Z",
  "include_staged": false,
  "max_nodes": 300,
  "max_edges": 1200,
  "cursor": null
}
```

| Campo | Regla |
|---|---|
| `root_ids` | 1–20 UUIDs; deben existir y ser visibles |
| `depth` | 1–5; por defecto 2 |
| `direction` | `OUT`, `IN`, `BOTH`; por defecto `BOTH` |
| `disciplines` | array; vacío = todas |
| `admiralty.min/max` | códigos válidos; rango inclusivo |
| `min_confidence` | 0–100 |
| `max_tlp` | no puede exceder clearance |
| `as_of` | fecha de reconstrucción; por defecto `now()` |
| `include_staged` | sólo `ADMIN`/`ANALYST`; `false` por defecto |
| `max_nodes` | 1–500; 413 si se excede |
| `max_edges` | 1–2000 |

### 4.3 SQL canónico

```sql
WITH RECURSIVE params AS (
    SELECT
        $1::uuid[] AS roots,
        $2::int AS max_depth,
        $3::text AS direction,
        $4::text[] AS disciplines,
        $5::smallint AS min_confidence,
        $6::text AS clearance,
        $7::timestamptz AS as_of
),
walk AS (
    SELECT r.entity_id, 0 AS depth, ARRAY[r.entity_id] AS path
    FROM unnest((SELECT roots FROM params)) AS r(entity_id)
    UNION ALL
    SELECT
        CASE WHEN e.source_entity_id = w.entity_id
             THEN e.target_entity_id ELSE e.source_entity_id END AS entity_id,
        w.depth + 1,
        w.path || CASE WHEN e.source_entity_id = w.entity_id
                       THEN e.target_entity_id ELSE e.source_entity_id END
    FROM walk w
    JOIN intel.intelligence_relationships e
      ON ( (e.source_entity_id = w.entity_id AND (SELECT direction FROM params) IN ('OUT','BOTH'))
        OR (e.target_entity_id = w.entity_id AND (SELECT direction FROM params) IN ('IN','BOTH')) )
    WHERE w.depth < (SELECT max_depth FROM params)
      AND e.revoked = false
      AND e.validation_state = 'ANALYST_VERIFIED'
      AND e.valid_from <= (SELECT as_of FROM params)
      AND (e.valid_until IS NULL OR e.valid_until > (SELECT as_of FROM params))
      AND e.confidence_score >= (SELECT min_confidence FROM params)
      AND (cardinality((SELECT disciplines FROM params)) = 0
           OR e.discipline = ANY((SELECT disciplines FROM params)))
      AND intel.tlp_rank(e.tlp_classification::text) <= intel.tlp_rank((SELECT clearance FROM params))
      AND NOT (CASE WHEN e.source_entity_id = w.entity_id
                    THEN e.target_entity_id ELSE e.source_entity_id END = ANY(w.path))
),
reached AS (
    SELECT entity_id, min(depth) AS depth
    FROM walk
    GROUP BY entity_id
    ORDER BY depth ASC
    LIMIT (SELECT max_nodes FROM params)
)
SELECT r.entity_id, r.depth, e2.*
FROM reached r
JOIN intel.core_entities e2 ON e2.entity_id = r.entity_id
WHERE e2.revoked = false
  AND e2.valid_from <= (SELECT as_of FROM params)
  AND (e2.valid_until IS NULL OR e2.valid_until > (SELECT as_of FROM params))
  AND intel.tlp_rank(e2.tlp_classification::text) <= intel.tlp_rank((SELECT clearance FROM params));
```

### 4.4 Aristas

```sql
WITH reached AS ( /* misma CTE que 4.3 */ )
SELECT e.*
FROM intel.intelligence_relationships e
WHERE e.source_entity_id IN (SELECT entity_id FROM reached)
  AND e.target_entity_id IN (SELECT entity_id FROM reached)
  AND e.revoked = false
  AND e.validation_state = 'ANALYST_VERIFIED'
  AND e.valid_from <= $as_of
  AND (e.valid_until IS NULL OR e.valid_until > $as_of)
  AND e.confidence_score >= $min_confidence
  AND (cardinality($disciplines) = 0 OR e.discipline = ANY($disciplines))
  AND intel.tlp_rank(e.tlp_classification::text) <= intel.tlp_rank($clearance)
LIMIT $max_edges;
```

### 4.5 Response

```json
{
  "nodes": [
    {
      "entity_id": "…",
      "stix_id": "identity--…",
      "stix_type": "identity",
      "canonical_name": "…",
      "primary_discipline": "SOCMINT",
      "admiralty": "B2",
      "confidence": 78,
      "tlp": "TLP:AMBER",
      "valid_from": "…",
      "valid_until": null,
      "depth": 1,
      "observables": [
        { "type": "TELEGRAM_ID", "masked_value": "tg:***42", "valid_from": "…" }
      ],
      "evidence_ids": ["…"]
    }
  ],
  "edges": [
    {
      "relationship_id": "…",
      "source_entity_id": "…",
      "target_entity_id": "…",
      "relationship_type": "COMMUNICATES_WITH",
      "discipline": "SOCMINT",
      "confidence": 71,
      "admiralty": "C3",
      "tlp": "TLP:AMBER",
      "valid_from": "…",
      "valid_until": null,
      "event_id": null,
      "evidence_ids": ["…"]
    }
  ],
  "truncated": false,
  "next_cursor": null,
  "stats": { "nodes": 42, "edges": 88, "max_depth_reached": 2, "suppressed": 0 }
}
```

**Reglas:**

- El resultado no incluye entidades `AGENT_STAGED` salvo `include_staged=true` y rol autorizado; en ese caso se marcan `staged=true` y no se mezclan con el grafo validado.
- `max_nodes` se aplica antes de cargar atributos/evidencias para evitar OOM.
- `truncated=true` si se alcanzó `max_nodes`/`max_edges`; `next_cursor` apunta al último `(depth, entity_id)`.
- La profundidad real puede ser menor si el grafo se agota.
- Los ciclos se cortan con `path`.

---

## 5. Consulta 3 — Detección de evasión de alias

### 5.1 Endpoint

```http
POST /api/v1/analytics/alias-evasion
operationId: detectAliasEvasion
```

### 5.2 Request

```json
{
  "mission_id": "…",
  "entity_id": null,
  "window": {
    "from": "2026-09-01T00:00:00Z",
    "to": "2026-10-01T00:00:00Z"
  },
  "min_aliases": 3,
  "min_platforms": 2,
  "max_gap_hours": 48,
  "similarity_threshold": 0.55,
  "max_tlp": "TLP:AMBER",
  "limit": 100
}
```

### 5.3 SQL canónico

```sql
WITH params AS (
    SELECT $1::uuid AS mission_id,
           $2::uuid AS entity_id,
           $3::timestamptz AS win_from,
           $4::timestamptz AS win_to,
           $5::int AS min_aliases,
           $6::int AS min_platforms,
           $7::int AS max_gap_hours,
           $8::float8 AS similarity_threshold,
           $9::text AS clearance
),
alias_windows AS (
    SELECT
        o.entity_id,
        o.observable_id,
        a.alias_id,
        a.alias_type,
        a.normalized_value,
        a.platform,
        a.observed_from,
        a.observed_until,
        lag(a.normalized_value) OVER (
            PARTITION BY o.entity_id, a.alias_type ORDER BY a.observed_from
        ) AS prev_value,
        lag(a.observed_from) OVER (
            PARTITION BY o.entity_id, a.alias_type ORDER BY a.observed_from
        ) AS prev_from,
        lag(a.platform) OVER (
            PARTITION BY o.entity_id, a.alias_type ORDER BY a.observed_from
        ) AS prev_platform
    FROM intel.observable_alias_history a
    JOIN intel.observables o ON o.observable_id = a.observable_id
    JOIN intel.core_entities e ON e.entity_id = o.entity_id
    CROSS JOIN params p
    WHERE a.revoked = false
      AND a.observed_from >= p.win_from
      AND a.observed_from <  p.win_to
      AND (p.entity_id IS NULL OR o.entity_id = p.entity_id)
      AND intel.tlp_rank(a.tlp_classification::text) <= intel.tlp_rank(p.clearance)
),
rotation AS (
    SELECT
        entity_id, observable_id, alias_id, alias_type, normalized_value,
        platform, observed_from, observed_until, prev_value, prev_from,
        extract(epoch FROM (observed_from - prev_from)) / 3600.0 AS gap_hours
    FROM alias_windows
    WHERE prev_from IS NOT NULL
      AND normalized_value <> prev_value
      AND extract(epoch FROM (observed_from - prev_from)) / 3600.0
          <= (SELECT max_gap_hours FROM params)
),
rotation_signals AS (
    SELECT
        entity_id,
        observable_id,
        'RAPID_ALIAS_ROTATION'::text AS signal_type,
        count(*) AS occurrences,
        max(gap_hours) AS max_gap_hours,
        array_agg(DISTINCT normalized_value) AS aliases,
        array_agg(DISTINCT platform) FILTER (WHERE platform IS NOT NULL) AS platforms,
        min(observed_from) AS first_seen,
        max(observed_from) AS last_seen,
        least(1.0, count(*)::numeric / 5.0) AS score
    FROM rotation
    GROUP BY entity_id, observable_id
    HAVING count(*) >= (SELECT min_aliases FROM params)
),
cross_platform AS (
    SELECT
        entity_id,
        observable_id,
        'CROSS_PLATFORM_REUSE'::text AS signal_type,
        count(*) AS occurrences,
        NULL::numeric AS max_gap_hours,
        array_agg(DISTINCT normalized_value) AS aliases,
        array_agg(DISTINCT platform) FILTER (WHERE platform IS NOT NULL) AS platforms,
        min(observed_from) AS first_seen,
        max(observed_from) AS last_seen,
        least(1.0, count(DISTINCT platform)::numeric / 3.0) AS score
    FROM alias_windows
    GROUP BY entity_id, observable_id
    HAVING count(DISTINCT platform) >= (SELECT min_platforms FROM params)
),
collision AS (
    SELECT
        NULL::uuid AS entity_id,
        NULL::uuid AS observable_id,
        'ALIAS_COLLISION'::text AS signal_type,
        count(DISTINCT o.entity_id) AS occurrences,
        NULL::numeric AS max_gap_hours,
        array_agg(DISTINCT a.normalized_value) AS aliases,
        array_agg(DISTINCT a.platform) FILTER (WHERE a.platform IS NOT NULL) AS platforms,
        min(a.observed_from) AS first_seen,
        max(a.observed_from) AS last_seen,
        least(1.0, count(DISTINCT o.entity_id)::numeric / 4.0) AS score
    FROM intel.observable_alias_history a
    JOIN intel.observables o ON o.observable_id = a.observable_id
    CROSS JOIN params p
    WHERE a.revoked = false
      AND a.observed_from >= p.win_from
      AND a.observed_from < p.win_to
      AND intel.tlp_rank(a.tlp_classification::text) <= intel.tlp_rank(p.clearance)
    GROUP BY a.normalized_value
    HAVING count(DISTINCT o.entity_id) > 1
)
SELECT * FROM rotation_signals
UNION ALL
SELECT * FROM cross_platform
UNION ALL
SELECT * FROM collision
ORDER BY score DESC, last_seen DESC
LIMIT $10;
```

### 5.4 Response

```json
{
  "signals": [
    {
      "signal_id": "alias-…",
      "signal_type": "RAPID_ALIAS_ROTATION",
      "entity_id": "…",
      "observable_id": "…",
      "score": 0.86,
      "occurrences": 6,
      "max_gap_hours": 3.5,
      "first_seen": "…",
      "last_seen": "…",
      "aliases": ["@a", "@b", "@c"],
      "platforms": ["telegram"],
      "evidence_ids": ["…"],
      "explanation": "6 cambios de handle en 18 horas; patrón compatible con evasión."
    }
  ],
  "truncated": false,
  "suppressed": 0
}
```

**Reglas:**

- `ALIAS_COLLISION` no prueba que dos entidades sean la misma; sólo que comparten un alias. Se devuelve como señal, no como fusión.
- La rotación de un `DISPLAY_NAME` no es tan fuerte como la de un `ACCOUNT_ID`/`HANDLE`; el score se pondera por `alias_type`.
- Un alias con `observed_until` nulo y `observed_from` reciente no es “evasión” por sí solo.
- La consulta no devuelve valores completos si el TLP/clearance no lo permite; usa `masked_value`.
- Los `evidence_ids` se obtienen de `observable_alias_history.source_evidence_id` y de `evidence_links`.

---

## 6. Índices y rendimiento

| Consulta | Índices críticos |
|---|---|
| Colocalización | `idx_events_location_gist`, `idx_events_mission_time`, `idx_events_discipline_time`, `idx_events_tlp` |
| Subgrafo | `idx_intel_rel_period`, `idx_intel_rel_source_target`, `idx_intel_rel_discipline`, `idx_core_entities_confidence` |
| Alias | `idx_alias_observable_time`, `idx_alias_normalized_trgm`, `idx_alias_platform`, `idx_observables_selector` |
| Evidencia | `idx_evidence_links_evidence`, `idx_raw_vault_hash` |

### 6.1 Reglas de coste

1. `statement_timeout = 5s`; `idle_in_transaction_session_timeout = 10s`.
2. `max_nodes`/`max_edges` se aplican antes de `jsonb_agg`.
3. `EXPLAIN (ANALYZE, BUFFERS)` en CI con un dataset sintético de 10k eventos; se rechaza un plan con `Seq Scan` en `intelligence_events` para filtros por misión/tiempo.
4. Las consultas de mapa pueden usar una vista materializada `api.mv_event_hotspots` refrescada cada 5 min; nunca datos TLP:RED.
5. Cache de respuesta sólo si `max_tlp <= TLP:GREEN` y el usuario lo autoriza; TTL 60 s.
6. Paginación por cursor `(score, event_id)` o `(depth, entity_id)`; no `OFFSET` grande.

### 6.2 Plan de prueba

```bash
cd apps/backend
python -m pytest tests/v3/correlation -q
python -m pytest tests/v3/correlation/test_query_plans.py -q
```

`test_query_plans.py` ejecuta `EXPLAIN` contra PostgreSQL de Testcontainers y verifica que se usan los índices esperados. No se acepta un test sólo con dobles.

---

## 7. Seguridad

| Control | Regla |
|---|---|
| RLS | Todas las tablas tienen RLS; las vistas son `security_invoker` |
| TLP | `max_tlp` se intersecta con el clearance; nunca se amplía |
| Misión | El usuario/agente sólo consulta su misión; `DIRECTOR_CELL` puede consultar todas |
| Selectores | `IMSI`, `IMEI`, `MAC`, `MSISDN` se enmascaran por defecto; revelar exige `ADMIN` + auditoría |
| Coordenadas | TLP:RED no devuelve coordenadas exactas; sólo geohash/radio |
| Auditoría | `audit.events` con `query_hash`, `result_count`, `suppressed`, `mission_id` |
| Rate limit | 30/min humano, 120/min agente; `429` |
| SQL injection | Consultas parametrizadas; no interpolación de texto |
| DoS | `max_nodes`, `max_edges`, `limit`, `statement_timeout` |
| Fuga por conteo | `suppressed` sólo cuenta; no se revela el contenido |

---

## 8. Escenarios de aceptación (Gherkin)

```gherkin
Feature: Correlación multi-INT

  Scenario: Colocalización válida
    Given una emisión SIGINT a las 21:14:03 en (40.4168,-3.7038)
    And un post SOCMINT a las 21:17:40 a 58 metros
    When un ANALYST consulta colocation con radio 500 m y ventana 15 min
    Then se devuelve un cluster con ambos eventos
    And el score es >= 0.70
    And se incluyen los evidence_ids de ambos

  Scenario: Colocalización fuera de ventana
    Given los mismos eventos separados por 2 horas
    When se consulta con max_time_delta_s = 900
    Then no se devuelve el par
    And no se inventa una relación

  Scenario: TLP denegado
    Given un evento TLP:RED
    When un VIEWER con clearance TLP:GREEN consulta la zona
    Then el evento no aparece
    And suppressed >= 1
    And no se filtra el contenido

  Scenario: Subgrafo con filtros
    Given un grafo con OSINT, SOCMINT y SIGINT
    When se pide depth 2 con discipline SOCMINT y min_confidence 60
    Then sólo se devuelven nodos/aristas que cumplen
    And se respeta max_nodes
    And los nodos AGENT_STAGED no aparecen

  Scenario: Subgrafo as_of
    Given una relación válida del 1 al 10 de octubre
    When se consulta as_of 5 de octubre
    Then la relación aparece
    When se consulta as_of 15 de octubre
    Then la relación no aparece

  Scenario: Evasión de alias
    Given un observable con 6 handles en 18 horas
    When se consulta alias-evasion
    Then se devuelve RAPID_ALIAS_ROTATION con score > 0.7
    And se listan los evidence_ids

  Scenario: Colisión de alias no es fusión
    Given dos entidades que usan el mismo handle en plataformas distintas
    When se consulta alias-evasion
    Then se devuelve ALIAS_COLLISION
    And no se crea ALIAS_OF automáticamente
```

---

## 9. Trazabilidad

| Requisito base | Este spec | Tarea |
|---|---|---|
| R-07 | Alias evasion / ALIAS_OF | TASK-32 |
| R-11 | `as_of` + validez temporal | TASK-33 |
| R-12 | TLP en consultas | TASK-34 |
| R-17 | Búsqueda híbrida existente | TASK-31 |
| R-24 | Sin exposición de DB | TASK-34 |
| R-25 | Rate limit | TASK-34 |
| DM-E9 | `as_of` | TASK-33 |
| DM-E11 | Eventos SIGINT | TASK-28 |
| AUD-16 | PostGIS | TASK-13, TASK-27 |
| AUD-19 | Subgrafo multicapa | TASK-30 |
| AUD-20 | Colocalización/evasión | TASK-31, TASK-32 |

### 9.1 SPEC-GAP

- **SPEC-GAP-C-001:** definición exacta de “colocalización” cuando la precisión es > radio. Propuesta: `distance_m <= radius_m + accuracy_m` y marcar `accuracy_limited=true`.
- **SPEC-GAP-C-002:** límites de `as_of` histórico y datos corregidos. Propuesta: `as_of` sólo sobre `valid_from/valid_until`; las revocaciones se evalúan con `revoked_at`.
- **SPEC-GAP-C-003:** política de cache de subgrafos TLP:AMBER. Propuesta: no cachear; sólo TLP:GREEN.
- **SPEC-GAP-C-004:** algoritmo de clustering (DBSCAN vs. unión de pares). Propuesta: unión de pares por radio/tiempo, documentada y testeada.

**No se implementa hasta aprobación.**
