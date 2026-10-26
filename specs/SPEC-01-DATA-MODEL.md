# SPEC-01 — Modelo de Datos Multi-INT, Custodia y Fusión

> **Estado: APROBADA v3.1 (2026-10-26) — implementación autorizada por el usuario.**
> Fase SDD: especificación de datos (previa a implementación).
> Autorizado por: `SPEC.md` v3.0.0 (base), `AUDIT.md`, `specs/00-AUDIT.md`.
> Sustituye/complementa: `contracts/v3/db-contract.sql` (v3.0.0) en la materia de observables, eventos y selectores.
> Aprobador requerido: usuario. Sin aprobación **no** se modifica `/apps`, `/deploy` ni `/scripts`.

---

## 1. Contexto y objetivo operativo

La plataforma debe operar una célula de inteligencia multi-INT (OSINT, SOCMINT, SIGINT, HUMINT) con dos capas de datos inseparablemente auditables:

1. **Capa 1 — Custodia:** `staging.raw_evidence_vault` conserva los bytes exactos capturados, su SHA-256, TLP, base legal, cadena de custodia y hash chain. Es append-only y no se expone a la API sin RBAC/TLP.
2. **Capa 2 — Inteligencia validada:** `intel.core_entities`, `intel.observables`, `intel.intelligence_events` y `intel.intelligence_relationships` contienen sólo aserciones con evidencia y aprobación humana. Sólo `ATENEA` (`svc_atenea`) escribe esta capa.

La regla central es:

> **Un alias es un dato mutable; un selector es un hecho inmutable.**
> El nombre visible (`display_name`, `@handle`) nunca identifica por sí solo a una entidad. La identidad se ancla en selectores técnicos verificables (`account_id`, `IMSI`, `IMEI`, `MAC`, `phone_e164`, `wallet`, etc.) con ventana temporal y evidencia.

Este documento define el DDL propuesto, las invariantes, los triggers, las vistas de fusión y la migración desde el esquema v3.0.0. La implementación se planifica en `TASKS.md`; **este documento no ejecuta DDL**.

---

## 2. Decisiones de diseño

| ID | Decisión | Justificación |
|---|---|---|
| DM-D1 | Renombrar físicamente las tablas a los nombres solicitados y mantener **vistas de compatibilidad** con los nombres v3.0.0 | Evita dos fuentes de verdad; la app v2.2 sigue leyendo durante la transición |
| DM-D2 | Separar `observables` (selector inmutable) de `observable_alias_history` (alias mutable con ventana) | Permite resolver identidad y detectar evasión de alias |
| DM-D3 | `core_entities` no almacena `aliases[]`; los alias se consultan en `observable_alias_history` | Elimina la mutabilidad Silenciosa del array |
| DM-D4 | `AGENT_STAGED` vive en `staging.agent_proposals`; el grafo validado sólo acepta `ANALYST_VERIFIED` | La cuarentena no contamina la capa 2 |
| DM-D5 | `intelligence_events` usa `geography(Point,4326)` y GiST | Habilita `ST_DWithin` y correlación SIGINT-SOCMINT |
| DM-D6 | Toda aserción validada exige al menos un `evidence_id` existente | Cumple R-03/R-04 y evita inteligencia huérfana |
| DM-D7 | No hay `DELETE`; se usa `revoked=true` + motivo + actor | Cumple R-15 y cadena de custodia |
| DM-D8 | TLP se evalúa con `intel.tlp_rank` y RLS; nunca se degrada sin autorización auditada | Cumple R-12 |
| DM-D9 | Los selectores sensibles se almacenan cifrados/mascarados según TLP y clearance | Protección HUMINT/SIGINT |
| DM-D10 | La migración es incremental y reversible; `GRAPH_BACKEND` permite volver a Neo4j | Continuidad operativa |

### 2.1 Correspondencia con el esquema v3.0.0

| Nombre solicitado | Nombre v3.0.0 actual | Estrategia |
|---|---|---|
| `staging.raw_evidence_vault` | `staging.raw_intel_ledger` | `ALTER TABLE ... RENAME`; vista `raw_intel_ledger` de compatibilidad |
| `audit.hermes_agent_runs` | `audit.agent_runs` | rename + columnas de modelo/prompt/tools |
| `intel.core_entities` | `intel.intel_entities` | rename; se elimina `aliases[]` de la tabla física |
| `intel.observables` | — | tabla nueva |
| `intel.observable_alias_history` | `intel.entity_aliases` (parcial) | tabla nueva; backfill desde `entity_aliases` |
| `intel.intelligence_events` | — | tabla nueva |
| `intel.intelligence_relationships` | `intel.intel_relationships` | rename + columnas de disciplina/estado/evento |
| `intel.evidence_links` | `intel.evidence_links` | extender a observable/alias/evento |
| `staging.agent_proposals` | `intel.entity_resolution_candidates` (parcial) | tabla nueva general; vista de compatibilidad |
| `intel.entity_merge_log` | — | tabla nueva para `ALIAS_OF` y fusiones |

> **Compatibilidad:** las vistas con los nombres antiguos se crean con `security_invoker=true` y `SELECT`; la app v2.2 no puede escribir por ellas. Las funciones `intel.upsert_entity` y `intel.upsert_relationship` se reescriben en la migración para apuntar a las tablas nuevas.

---

## 3. Extensiones, esquemas y tipos

```sql
-- Requisitos de motor: PostgreSQL 16
CREATE EXTENSION IF NOT EXISTS pgcrypto;       -- digest()
CREATE EXTENSION IF NOT EXISTS pg_trgm;        -- similitud de alias
CREATE EXTENSION IF NOT EXISTS btree_gist;     -- exclusiones temporales
CREATE EXTENSION IF NOT EXISTS vector;         -- pgvector
CREATE EXTENSION IF NOT EXISTS postgis;        -- geografía y ST_DWithin

CREATE SCHEMA IF NOT EXISTS staging;           -- capa 1: evidencia cruda
CREATE SCHEMA IF NOT EXISTS intel;             -- capa 2: grafo validado
CREATE SCHEMA IF NOT EXISTS humint;            -- compartimento HUMINT
CREATE SCHEMA IF NOT EXISTS audit;             -- auditoría append-only
CREATE SCHEMA IF NOT EXISTS api;               -- vistas de lectura

REVOKE CREATE ON SCHEMA public FROM PUBLIC;
```

### 3.1 Tipos nuevos

```sql
CREATE TYPE intel.discipline_enum AS ENUM (
    'OSINT', 'SOCMINT', 'SIGINT', 'HUMINT', 'TECHINT', 'FININT', 'GEOINT', 'MIXED'
);

CREATE TYPE intel.observable_type_enum AS ENUM (
    'ACCOUNT_ID', 'USERNAME', 'DISPLAY_NAME', 'PHONE_E164', 'EMAIL',
    'WALLET', 'DOMAIN', 'IPV4', 'IPV6', 'URL', 'MAC_ADDRESS',
    'IMSI', 'IMEI', 'ICCID', 'MSISDN', 'DEVICE_FINGERPRINT',
    'TELEGRAM_ID', 'X_USER_ID', 'DISCORD_ID', 'SIGNAL_ID',
    'COORDINATE', 'OTHER'
);

CREATE TYPE intel.alias_type_enum AS ENUM (
    'HANDLE', 'DISPLAY_NAME', 'PHONE', 'EMAIL', 'WALLET', 'URL',
    'ACCOUNT_ID', 'DEVICE_ID', 'OTHER'
);

CREATE TYPE intel.event_type_enum AS ENUM (
    'RF_EMISSION', 'IMSI_ATTACH', 'MAC_OBSERVATION', 'CALL', 'SMS',
    'SOCIAL_POST', 'PROFILE_UPDATE', 'MESSAGE', 'REPORT', 'SIGHTING',
    'CYBER_EVENT', 'OTHER'
);

CREATE TYPE intel.source_type_enum AS ENUM (
    'OSINT', 'SOCMINT', 'SIGINT', 'HUMINT', 'MANUAL', 'PUBLIC_REGISTRY', 'LEGACY'
);

CREATE TYPE intel.proposal_type_enum AS ENUM (
    'OBSERVABLE', 'RELATIONSHIP', 'EVENT', 'ENTITY_MERGE', 'ALIAS'
);

CREATE TYPE intel.proposal_status_enum AS ENUM (
    'AGENT_STAGED', 'ANALYST_VERIFIED', 'APPLIED', 'REJECTED', 'EXPIRED'
);

CREATE TYPE intel.validation_state_enum AS ENUM (
    'ANALYST_VERIFIED', 'REVOKED'
);
```

> `intel.admiralty_rating`, `staging.tlp_enum`, `intel.agent_type_enum` y `staging.collector_agent_enum` se conservan de v3.0.0.

---

## 4. Auditoría de runs y tool calls

> **Orden de migración:** `intel.agent_registry`, `intel.missions` y `intel.pirs` deben existir antes de esta tabla y antes de `raw_evidence_vault`.

```sql
CREATE TABLE audit.hermes_agent_runs (
    run_id              uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    agent_id            uuid REFERENCES intel.agent_registry(agent_id)
                        ON DELETE RESTRICT,
    agent_type          intel.agent_type_enum NOT NULL,
    mission_id          uuid REFERENCES intel.missions(mission_id)
                        ON DELETE RESTRICT,
    pir_id              uuid REFERENCES intel.pirs(pir_id) ON DELETE RESTRICT,
    model_provider      text,
    model_name          text,
    prompt_template_id  text,
    prompt_sha256       text CHECK (prompt_sha256 IS NULL OR prompt_sha256 ~ '^[0-9a-f]{64}$'),
    tool_catalog_sha256 text CHECK (tool_catalog_sha256 IS NULL OR tool_catalog_sha256 ~ '^[0-9a-f]{64}$'),
    input_sha256        text CHECK (input_sha256 IS NULL OR input_sha256 ~ '^[0-9a-f]{64}$'),
    output_sha256       text CHECK (output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
    status              text NOT NULL DEFAULT 'RUNNING'
                        CHECK (status IN ('RUNNING','COMPLETED','FAILED','ABORTED')),
    started_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    tokens_in           integer CHECK (tokens_in IS NULL OR tokens_in >= 0),
    tokens_out          integer CHECK (tokens_out IS NULL OR tokens_out >= 0),
    cost_usd            numeric(12,6) CHECK (cost_usd IS NULL OR cost_usd >= 0),
    trace_id            text,
    error               text,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT run_finished_chk
        CHECK (status = 'RUNNING' OR finished_at IS NOT NULL)
);

CREATE INDEX idx_hermes_runs_agent_time
    ON audit.hermes_agent_runs (agent_type, started_at DESC);
CREATE INDEX idx_hermes_runs_mission_time
    ON audit.hermes_agent_runs (mission_id, started_at DESC);
CREATE INDEX idx_hermes_runs_trace
    ON audit.hermes_agent_runs (trace_id);

CREATE TABLE audit.hermes_tool_calls (
    tool_call_id       uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    run_id             uuid NOT NULL REFERENCES audit.hermes_agent_runs(run_id)
                       ON DELETE RESTRICT,
    agent_type         intel.agent_type_enum NOT NULL,
    mission_id         uuid NOT NULL REFERENCES intel.missions(mission_id)
                       ON DELETE RESTRICT,
    tool_name          text NOT NULL CHECK (length(btrim(tool_name)) > 0),
    arguments          jsonb NOT NULL,
    arguments_sha256   text NOT NULL CHECK (arguments_sha256 ~ '^[0-9a-f]{64}$'),
    result             jsonb,
    result_sha256      text CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
    status             text NOT NULL
                       CHECK (status IN ('OK','STAGED','DUPLICATE','NEEDS_REVIEW','REJECTED','ABORTED','ERROR')),
    latency_ms         integer CHECK (latency_ms IS NULL OR latency_ms >= 0),
    error              text,
    created_at         timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX idx_hermes_tool_calls_run
    ON audit.hermes_tool_calls (run_id, created_at);
CREATE INDEX idx_hermes_tool_calls_tool
    ON audit.hermes_tool_calls (tool_name, created_at DESC);
CREATE INDEX idx_hermes_tool_calls_status
    ON audit.hermes_tool_calls (status, created_at DESC);
```

> `audit.hermes_tool_calls` es inmutable: no se actualiza ni se borra. `audit.hermes_agent_runs` sólo puede pasar de `RUNNING` a un estado terminal y completar `finished_at`; el resto de campos queda auditado.

---

## 5. Capa 1 — `staging.raw_evidence_vault`

### 5.1 DDL

```sql
CREATE TABLE staging.raw_evidence_vault (
    evidence_id          uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    ledger_seq           bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    mission_id           uuid NOT NULL REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    pir_id               uuid NOT NULL REFERENCES intel.pirs(pir_id) ON DELETE RESTRICT,
    collector_agent      staging.collector_agent_enum NOT NULL,
    collector_run_id     uuid REFERENCES audit.hermes_agent_runs(run_id) ON DELETE RESTRICT,
    discipline           intel.discipline_enum NOT NULL,
    source_type          intel.source_type_enum NOT NULL,
    source_platform      text NOT NULL CHECK (length(btrim(source_platform)) > 0),
    source_uri           text NOT NULL CHECK (length(btrim(source_uri)) > 0),
    idempotency_key      text NOT NULL CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    raw_payload          jsonb NOT NULL,
    raw_payload_bytes    bytea NOT NULL,
    payload_sha256       text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    hash_algorithm       text NOT NULL DEFAULT 'SHA-256' CHECK (hash_algorithm = 'SHA-256'),
    canonicalization     text NOT NULL DEFAULT 'RFC8785-JCS'
                         CHECK (canonicalization = 'RFC8785-JCS'),
    captured_at_utc      timestamptz NOT NULL,
    observed_at_utc      timestamptz,
    ingested_at          timestamptz NOT NULL DEFAULT now(),
    tlp_classification   staging.tlp_enum NOT NULL,
    collection_method    text,
    legal_basis          text NOT NULL CHECK (length(btrim(legal_basis)) > 0),
    confidence_hint      numeric(3,2) CHECK (confidence_hint BETWEEN 0 AND 1),
    storage_uri          text,
    retention_until      timestamptz,
    legal_hold           boolean NOT NULL DEFAULT false,
    prev_hash            text,
    record_hash          text,
    metadata             jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT raw_vault_future_capture_chk
        CHECK (captured_at_utc <= now() + interval '5 minutes'),
    CONSTRAINT raw_vault_idempotency_uq
        UNIQUE (collector_agent, idempotency_key)
);

CREATE INDEX idx_raw_vault_pir_time
    ON staging.raw_evidence_vault (pir_id, captured_at_utc DESC);
CREATE INDEX idx_raw_vault_hash
    ON staging.raw_evidence_vault (payload_sha256);
CREATE INDEX idx_raw_vault_agent
    ON staging.raw_evidence_vault (collector_agent, captured_at_utc DESC);
CREATE INDEX idx_raw_vault_discipline
    ON staging.raw_evidence_vault (discipline, captured_at_utc DESC);
CREATE INDEX idx_raw_vault_tlp
    ON staging.raw_evidence_vault (tlp_classification, captured_at_utc DESC);
```

### 5.2 Adjuntos

```sql
CREATE TABLE staging.raw_evidence_attachments (
    attachment_id   uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    evidence_id     uuid NOT NULL REFERENCES staging.raw_evidence_vault(evidence_id)
                    ON DELETE RESTRICT,
    filename        text NOT NULL,
    mime_type       text NOT NULL,
    size_bytes      bigint NOT NULL CHECK (size_bytes >= 0),
    sha256          text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_uri     text NOT NULL CHECK (length(btrim(storage_uri)) > 0),
    immutable_uri   text,           -- object-lock / WORM
    created_at      timestamptz NOT NULL DEFAULT now(),
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_raw_attachments_evidence
    ON staging.raw_evidence_attachments (evidence_id);
```

### 5.3 Invariantes de custodia

1. `payload_sha256 = encode(digest(raw_payload_bytes,'sha256'),'hex')`.
2. `raw_payload` debe ser el JSON canónico de `raw_payload_bytes`; la canonicalización RFC 8785 se valida en el harness antes de insertar y se registra en `canonicalization`.
3. La misma `(collector_agent, idempotency_key)` con bytes distintos **debe** fallar con `409 IDEMPOTENCY_CONFLICT`; nunca devolver silenciosamente la evidencia anterior.
4. `record_hash = SHA-256(prev_hash || evidence_id || payload_sha256 || captured_at_utc || collector_agent)`.
5. `captured_at_utc` no puede estar más de 5 minutos en el futuro.
6. `legal_basis` es obligatorio y no puede ser genérico vacío.
7. `TLP` no puede degradarse después de la captura.
8. No hay `UPDATE`, `DELETE` ni `TRUNCATE`; sólo `INSERT` y lectura.

### 5.4 Triggers

```sql
CREATE OR REPLACE FUNCTION staging.fn_raw_evidence_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'raw_evidence_vault es append-only' USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_raw_vault_no_update_delete
BEFORE UPDATE OR DELETE ON staging.raw_evidence_vault
FOR EACH ROW EXECUTE FUNCTION staging.fn_raw_evidence_immutable();

CREATE TRIGGER trg_raw_vault_no_truncate
BEFORE TRUNCATE ON staging.raw_evidence_vault
FOR EACH STATEMENT EXECUTE FUNCTION staging.fn_raw_evidence_immutable();

CREATE OR REPLACE FUNCTION staging.fn_raw_evidence_chain()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_prev text;
    v_hash text;
BEGIN
    IF encode(digest(NEW.raw_payload_bytes, 'sha256'), 'hex') <> NEW.payload_sha256 THEN
        RAISE EXCEPTION 'Hash de evidencia no coincide con los bytes' USING ERRCODE = '23514';
    END IF;

    PERFORM pg_advisory_xact_lock(hashtext('staging.raw_evidence_vault'));
    SELECT record_hash INTO v_prev
    FROM staging.raw_evidence_vault
    ORDER BY ledger_seq DESC
    LIMIT 1;

    NEW.prev_hash := v_prev;
    NEW.record_hash := encode(digest(
        coalesce(v_prev, '') || NEW.evidence_id::text || NEW.payload_sha256 ||
        to_char(NEW.captured_at_utc AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') ||
        NEW.collector_agent::text,
        'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_raw_vault_chain
BEFORE INSERT ON staging.raw_evidence_vault
FOR EACH ROW EXECUTE FUNCTION staging.fn_raw_evidence_chain();
```

### 5.5 Verificación de cadena

```sql
CREATE OR REPLACE FUNCTION staging.verify_evidence_chain(
    p_from_seq bigint DEFAULT 1,
    p_to_seq   bigint DEFAULT NULL
) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, staging, public
AS $$
WITH ordered AS (
    SELECT ledger_seq, evidence_id, payload_sha256, captured_at_utc,
           collector_agent, prev_hash, record_hash,
           lag(record_hash) OVER (ORDER BY ledger_seq) AS expected_prev,
           encode(digest(
               coalesce(lag(record_hash) OVER (ORDER BY ledger_seq), '') ||
               evidence_id::text || payload_sha256 ||
               to_char(captured_at_utc AT TIME ZONE 'UTC',
                       'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') ||
               collector_agent::text,
               'sha256'), 'hex') AS expected_hash
    FROM staging.raw_evidence_vault
    WHERE ledger_seq >= p_from_seq
      AND (p_to_seq IS NULL OR ledger_seq <= p_to_seq)
)
SELECT coalesce(bool_and(
           prev_hash IS NOT DISTINCT FROM expected_prev
           AND record_hash = expected_hash
       ), true)
FROM ordered;
$$;
```

> La verificación se expone por `GET /api/v1/evidence/{id}/verify` (TASK-10) y por job programado. No basta con verificar el hash del payload.

---

## 6. Capa 2 — Entidades, observables y alias

### 6.1 `intel.core_entities`

```sql
CREATE TABLE intel.core_entities (
    entity_id          uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    stix_id            text NOT NULL UNIQUE,
    stix_type          text NOT NULL REFERENCES intel.stix_types(stix_type)
                       ON DELETE RESTRICT,
    canonical_name     text NOT NULL CHECK (length(btrim(canonical_name)) > 0),
    primary_discipline intel.discipline_enum NOT NULL DEFAULT 'MIXED',
    properties         jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence         smallint NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    admiralty_rating   intel.admiralty_rating NOT NULL,
    tlp_classification staging.tlp_enum NOT NULL,
    validation_state   intel.validation_state_enum NOT NULL
                       DEFAULT 'ANALYST_VERIFIED',
    valid_from         timestamptz NOT NULL DEFAULT now(),
    valid_until        timestamptz,
    first_seen_at      timestamptz,
    last_seen_at       timestamptz,
    revoked            boolean NOT NULL DEFAULT false,
    revoked_at         timestamptz,
    revoked_reason     text,
    created_by_agent   intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                       CHECK (created_by_agent = 'ATENEA'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    updated_at         timestamptz NOT NULL DEFAULT now(),
    embedding          vector(1024),
    search_tsv         tsvector,
    CONSTRAINT core_entities_valid_range_chk
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT core_entities_revoked_chk
        CHECK ((NOT revoked) OR (revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0)),
    CONSTRAINT core_entities_stix_id_chk
        CHECK (stix_id LIKE stix_type || '--%'),
    CONSTRAINT core_entities_no_staged_chk
        CHECK (validation_state <> 'REVOKED' OR revoked)
);

CREATE INDEX idx_core_entities_stix
    ON intel.core_entities (stix_type, stix_id);
CREATE INDEX idx_core_entities_name_trgm
    ON intel.core_entities USING gin (canonical_name gin_trgm_ops);
CREATE INDEX idx_core_entities_tsv
    ON intel.core_entities USING gin (search_tsv);
CREATE INDEX idx_core_entities_embedding
    ON intel.core_entities USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_core_entities_valid
    ON intel.core_entities (valid_from, valid_until);
CREATE INDEX idx_core_entities_tlp
    ON intel.core_entities (tlp_classification, primary_discipline);
CREATE INDEX idx_core_entities_confidence
    ON intel.core_entities (primary_discipline, confidence DESC);
```

> **No hay columna `aliases[]`.** Los alias se consultan en `intel.observable_alias_history`. Una vista de compatibilidad `intel.intel_entities` reconstruye `aliases` con `array_agg` para la app v2.2.

### 6.2 `intel.observables`

```sql
CREATE TABLE intel.observables (
    observable_id      uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    entity_id          uuid NOT NULL REFERENCES intel.core_entities(entity_id)
                       ON DELETE RESTRICT,
    observable_type    intel.observable_type_enum NOT NULL,
    immutable_value    text NOT NULL CHECK (length(btrim(immutable_value)) > 0),
    normalized_value   text NOT NULL CHECK (length(btrim(normalized_value)) > 0),
    value_hash         text NOT NULL CHECK (value_hash ~ '^[0-9a-f]{64}$'),
    confidence         smallint NOT NULL DEFAULT 50 CHECK (confidence BETWEEN 0 AND 100),
    admiralty_rating   intel.admiralty_rating NOT NULL DEFAULT 'F6',
    tlp_classification staging.tlp_enum NOT NULL,
    valid_from         timestamptz NOT NULL DEFAULT now(),
    valid_until        timestamptz,
    revoked            boolean NOT NULL DEFAULT false,
    revoked_at         timestamptz,
    revoked_reason     text,
    created_by_agent   intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                       CHECK (created_by_agent = 'ATENEA'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    metadata           jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT observables_valid_range_chk
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT observables_revoked_chk
        CHECK ((NOT revoked) OR (revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0)),
    CONSTRAINT observables_value_hash_chk
        CHECK (value_hash = encode(digest(normalized_value, 'sha256'), 'hex'))
);

-- Un selector no puede apuntar a dos entidades en la misma ventana temporal.
ALTER TABLE intel.observables
ADD CONSTRAINT observables_no_overlap
EXCLUDE USING gist (
    observable_type WITH =,
    value_hash WITH =,
    tstzrange(valid_from, valid_until, '[)') WITH &&
) WHERE (revoked = false);

CREATE UNIQUE INDEX uq_observables_active_selector
    ON intel.observables (observable_type, value_hash)
    WHERE revoked = false AND valid_until IS NULL;

CREATE INDEX idx_observables_entity
    ON intel.observables (entity_id, observable_type);
CREATE INDEX idx_observables_selector
    ON intel.observables (observable_type, value_hash);
CREATE INDEX idx_observables_normalized_trgm
    ON intel.observables USING gin (normalized_value gin_trgm_ops);
CREATE INDEX idx_observables_tlp
    ON intel.observables (tlp_classification);
```

**Regla de oro:** el `value_hash` se calcula sobre el valor normalizado y canónico del selector, no sobre el alias. Para `PHONE_E164`, normalizar a E.164; para `MAC_ADDRESS`, mayúsculas y separadores canónicos; para `IMSI`, dígitos sin espacios; para `URL`, esquema/host en minúsculas y path normalizado.

### 6.3 `intel.observable_alias_history`

```sql
CREATE TABLE intel.observable_alias_history (
    alias_id           uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    observable_id      uuid NOT NULL REFERENCES intel.observables(observable_id)
                       ON DELETE RESTRICT,
    alias_type         intel.alias_type_enum NOT NULL,
    alias_value        text NOT NULL CHECK (length(btrim(alias_value)) > 0),
    normalized_value   text NOT NULL CHECK (length(btrim(normalized_value)) > 0),
    platform           text,
    observed_from      timestamptz NOT NULL,
    observed_until     timestamptz,
    source_evidence_id uuid NOT NULL REFERENCES staging.raw_evidence_vault(evidence_id)
                       ON DELETE RESTRICT,
    confidence         smallint NOT NULL DEFAULT 50 CHECK (confidence BETWEEN 0 AND 100),
    tlp_classification staging.tlp_enum NOT NULL,
    revoked            boolean NOT NULL DEFAULT false,
    revoked_at         timestamptz,
    revoked_reason     text,
    created_by_agent   intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                       CHECK (created_by_agent = 'ATENEA'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT alias_valid_range_chk
        CHECK (observed_until IS NULL OR observed_until > observed_from),
    CONSTRAINT alias_revoked_chk
        CHECK ((NOT revoked) OR (revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0))
);

-- Un alias no puede estar activo dos veces para el mismo observable en la misma ventana.
ALTER TABLE intel.observable_alias_history
ADD CONSTRAINT alias_no_overlap
EXCLUDE USING gist (
    observable_id WITH =,
    alias_type WITH =,
    normalized_value WITH =,
    tstzrange(observed_from, observed_until, '[)') WITH &&
) WHERE (revoked = false);

CREATE INDEX idx_alias_observable_time
    ON intel.observable_alias_history (observable_id, observed_from DESC);
CREATE INDEX idx_alias_normalized_trgm
    ON intel.observable_alias_history USING gin (normalized_value gin_trgm_ops);
CREATE INDEX idx_alias_platform
    ON intel.observable_alias_history (platform, normalized_value);
CREATE INDEX idx_alias_evidence
    ON intel.observable_alias_history (source_evidence_id);
```

**Cierre de alias:** para cerrar una ventana se usa la función controlada:

```sql
CREATE OR REPLACE FUNCTION intel.close_observable_alias(
    p_alias_id uuid,
    p_until timestamptz,
    p_reason text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, intel, audit, public
AS $$
BEGIN
    IF session_user <> 'svc_atenea'
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Solo ATENEA puede cerrar alias' USING ERRCODE = '42501';
    END IF;
    IF p_reason IS NULL OR length(btrim(p_reason)) = 0 THEN
        RAISE EXCEPTION 'Motivo obligatorio' USING ERRCODE = '23514';
    END IF;
    UPDATE intel.observable_alias_history
       SET observed_until = p_until,
           revoked = true,
           revoked_at = now(),
           revoked_reason = p_reason
     WHERE alias_id = p_alias_id
       AND observed_until IS NULL;
    PERFORM audit.log_event(
        'AGENT', session_user, 'ALIAS_CLOSED', 'OBSERVABLE_ALIAS',
        p_alias_id::text, NULL, NULL, 'SUCCESS',
        jsonb_build_object('until', p_until, 'reason', p_reason)
    );
END;
$$;
```

> El `UPDATE` directo está prohibido por trigger; esta función es la única puerta.

---

## 7. `intel.intelligence_events` (SIGINT/SOCMINT/OSINT)

```sql
CREATE TABLE intel.intelligence_events (
    event_id           uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    stix_id            text NOT NULL UNIQUE,
    event_type         intel.event_type_enum NOT NULL,
    discipline         intel.discipline_enum NOT NULL,
    mission_id         uuid NOT NULL REFERENCES intel.missions(mission_id)
                       ON DELETE RESTRICT,
    pir_id             uuid REFERENCES intel.pirs(pir_id) ON DELETE RESTRICT,
    entity_id          uuid REFERENCES intel.core_entities(entity_id)
                       ON DELETE RESTRICT,
    observable_id      uuid REFERENCES intel.observables(observable_id)
                       ON DELETE RESTRICT,
    event_time         timestamptz NOT NULL,
    observed_at        timestamptz NOT NULL DEFAULT now(),
    location           geography(Point,4326),
    accuracy_m         numeric(10,2) CHECK (accuracy_m IS NULL OR accuracy_m >= 0),
    source_evidence_id uuid NOT NULL REFERENCES staging.raw_evidence_vault(evidence_id)
                       ON DELETE RESTRICT,
    payload            jsonb NOT NULL DEFAULT '{}'::jsonb,
    admiralty_rating   intel.admiralty_rating NOT NULL,
    confidence         smallint NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    tlp_classification staging.tlp_enum NOT NULL,
    validation_state   intel.validation_state_enum NOT NULL
                       DEFAULT 'ANALYST_VERIFIED',
    revoked            boolean NOT NULL DEFAULT false,
    revoked_at         timestamptz,
    revoked_reason     text,
    created_by_agent   intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                       CHECK (created_by_agent = 'ATENEA'),
    created_at         timestamptz NOT NULL DEFAULT now(),
    search_tsv         tsvector,
    CONSTRAINT events_stix_id_chk CHECK (stix_id LIKE 'x-osint-event--%'),
    CONSTRAINT events_geo_chk
        CHECK (location IS NULL OR ST_SRID(location::geometry) = 4326),
    CONSTRAINT events_revoked_chk
        CHECK ((NOT revoked) OR (revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0)),
    CONSTRAINT events_time_chk
        CHECK (observed_at >= event_time - interval '1 day')
);

CREATE INDEX idx_events_location_gist
    ON intel.intelligence_events USING gist (location);
CREATE INDEX idx_events_time_brin
    ON intel.intelligence_events USING brin (event_time);
CREATE INDEX idx_events_mission_time
    ON intel.intelligence_events (mission_id, event_time DESC);
CREATE INDEX idx_events_discipline_time
    ON intel.intelligence_events (discipline, event_time DESC);
CREATE INDEX idx_events_entity_time
    ON intel.intelligence_events (entity_id, event_time DESC);
CREATE INDEX idx_events_observable_time
    ON intel.intelligence_events (observable_id, event_time DESC);
CREATE INDEX idx_events_payload_gin
    ON intel.intelligence_events USING gin (payload jsonb_path_ops);
CREATE INDEX idx_events_tsv
    ON intel.intelligence_events USING gin (search_tsv);
CREATE INDEX idx_events_tlp
    ON intel.intelligence_events (tlp_classification, event_time DESC);
```

**Reglas:**

- Un evento SIGINT puede no tener `entity_id` resuelto todavía; debe tener `observable_id` o `payload` con selector. Si no tiene ninguno, se rechaza.
- La `location` es `NULL` si no hay geolocalización; `accuracy_m` es la incertidumbre.
- `event_time` es el hecho; `observed_at` es la captura. No se confunden.
- El evento no se actualiza; se revoca.
- Los selectores sensibles se guardan en `payload` cifrado o en `observables` con `tlp` y máscara de API.

---

## 8. `intel.intelligence_relationships`

```sql
CREATE TABLE intel.intelligence_relationships (
    relationship_id       uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    stix_id               text NOT NULL UNIQUE,
    stix_type             text NOT NULL DEFAULT 'relationship'
                          CHECK (stix_type = 'relationship'),
    relationship_type     text NOT NULL REFERENCES intel.relationship_types(relationship_type)
                          ON DELETE RESTRICT,
    stix_relationship_type text GENERATED ALWAYS AS
                          (lower(replace(relationship_type, '_', '-'))) STORED,
    source_entity_id      uuid NOT NULL REFERENCES intel.core_entities(entity_id)
                          ON DELETE RESTRICT,
    target_entity_id      uuid NOT NULL REFERENCES intel.core_entities(entity_id)
                          ON DELETE RESTRICT,
    discipline            intel.discipline_enum NOT NULL DEFAULT 'MIXED',
    event_id              uuid REFERENCES intel.intelligence_events(event_id)
                          ON DELETE RESTRICT,
    properties            jsonb NOT NULL DEFAULT '{}'::jsonb,
    admiralty_rating      intel.admiralty_rating NOT NULL,
    tlp_classification    staging.tlp_enum NOT NULL,
    confidence_score      smallint NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
    valid_from            timestamptz NOT NULL DEFAULT now(),
    valid_until           timestamptz,
    last_verified_at      timestamptz,
    validation_state      intel.validation_state_enum NOT NULL
                          DEFAULT 'ANALYST_VERIFIED',
    revoked               boolean NOT NULL DEFAULT false,
    revoked_at            timestamptz,
    revoked_reason        text,
    created_by_agent      intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                          CHECK (created_by_agent = 'ATENEA'),
    created_at            timestamptz NOT NULL DEFAULT now(),
    updated_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT rel_valid_range_chk
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT rel_no_self_chk
        CHECK (source_entity_id <> target_entity_id),
    CONSTRAINT rel_stix_id_chk
        CHECK (stix_id LIKE 'relationship--%'),
    CONSTRAINT rel_revoked_chk
        CHECK ((NOT revoked) OR (revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0))
);

ALTER TABLE intel.intelligence_relationships
ADD CONSTRAINT rel_no_temporal_overlap
EXCLUDE USING gist (
    source_entity_id WITH =,
    target_entity_id WITH =,
    relationship_type WITH =,
    tstzrange(valid_from, valid_until, '[)') WITH &&
) WHERE (revoked = false);

CREATE INDEX idx_intel_rel_source_target
    ON intel.intelligence_relationships (source_entity_id, target_entity_id);
CREATE INDEX idx_intel_rel_type_valid
    ON intel.intelligence_relationships (relationship_type, valid_from, valid_until);
CREATE INDEX idx_intel_rel_period
    ON intel.intelligence_relationships USING gist (
        source_entity_id, target_entity_id, relationship_type,
        tstzrange(valid_from, valid_until, '[)')
    );
CREATE INDEX idx_intel_rel_discipline
    ON intel.intelligence_relationships (discipline, confidence_score DESC);
CREATE INDEX idx_intel_rel_tlp
    ON intel.intelligence_relationships (tlp_classification, confidence_score DESC);
CREATE INDEX idx_intel_rel_event
    ON intel.intelligence_relationships (event_id);
```

**Reglas:**

- `ALIAS_OF` es una relación simétrica entre entidades; no reemplaza el `entity_merge_log`.
- Una relación debe tener al menos una evidencia en `intel.evidence_links`; el trigger de validación lo exige.
- Una relación revocada no se borra; el índice de exclusión sólo aplica a `revoked = false`.
- `event_id` permite explicar una relación derivada de una emisión/evento concreto.

---

## 9. Evidencia, propuestas y fusiones

### 9.1 `intel.evidence_links` extendido

```sql
CREATE TABLE intel.evidence_links (
    link_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id        uuid REFERENCES intel.core_entities(entity_id) ON DELETE RESTRICT,
    relationship_id  uuid REFERENCES intel.intelligence_relationships(relationship_id)
                     ON DELETE RESTRICT,
    observable_id    uuid REFERENCES intel.observables(observable_id) ON DELETE RESTRICT,
    alias_id         uuid REFERENCES intel.observable_alias_history(alias_id)
                     ON DELETE RESTRICT,
    event_id         uuid REFERENCES intel.intelligence_events(event_id)
                     ON DELETE RESTRICT,
    evidence_id      uuid NOT NULL REFERENCES staging.raw_evidence_vault(evidence_id)
                     ON DELETE RESTRICT,
    linked_by_agent  intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                     CHECK (linked_by_agent = 'ATENEA'),
    linked_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT evidence_links_one_target_chk
        CHECK (num_nonnulls(entity_id, relationship_id, observable_id, alias_id, event_id) = 1)
);

CREATE UNIQUE INDEX uq_evidence_links_entity
    ON intel.evidence_links (entity_id, evidence_id) WHERE entity_id IS NOT NULL;
CREATE UNIQUE INDEX uq_evidence_links_rel
    ON intel.evidence_links (relationship_id, evidence_id) WHERE relationship_id IS NOT NULL;
CREATE UNIQUE INDEX uq_evidence_links_observable
    ON intel.evidence_links (observable_id, evidence_id) WHERE observable_id IS NOT NULL;
CREATE UNIQUE INDEX uq_evidence_links_alias
    ON intel.evidence_links (alias_id, evidence_id) WHERE alias_id IS NOT NULL;
CREATE UNIQUE INDEX uq_evidence_links_event
    ON intel.evidence_links (event_id, evidence_id) WHERE event_id IS NOT NULL;
CREATE INDEX idx_evidence_links_evidence
    ON intel.evidence_links (evidence_id);
```

### 9.2 `staging.agent_proposals` — cuarentena `AGENT_STAGED`

```sql
CREATE TABLE staging.agent_proposals (
    proposal_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    run_id             uuid NOT NULL REFERENCES audit.hermes_agent_runs(run_id)
                       ON DELETE RESTRICT,
    agent_type         intel.agent_type_enum NOT NULL,
    mission_id         uuid NOT NULL REFERENCES intel.missions(mission_id)
                       ON DELETE RESTRICT,
    pir_id             uuid REFERENCES intel.pirs(pir_id) ON DELETE RESTRICT,
    proposal_type      intel.proposal_type_enum NOT NULL,
    payload            jsonb NOT NULL,
    payload_sha256     text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    idempotency_key    text NOT NULL CHECK (idempotency_key ~ '^[0-9a-f]{64}$'),
    evidence_ids       uuid[] NOT NULL DEFAULT '{}'::uuid[],
    status             intel.proposal_status_enum NOT NULL DEFAULT 'AGENT_STAGED',
    confidence         smallint CHECK (confidence BETWEEN 0 AND 100),
    admiralty_rating   intel.admiralty_rating,
    tlp_classification staging.tlp_enum NOT NULL,
    validation_errors  jsonb NOT NULL DEFAULT '[]'::jsonb,
    target_entity_id   uuid REFERENCES intel.core_entities(entity_id)
                       ON DELETE RESTRICT,
    applied_object_id  uuid,
    created_at         timestamptz NOT NULL DEFAULT now(),
    expires_at         timestamptz,
    reviewed_by        text,
    reviewed_at        timestamptz,
    decision_reason    text,
    CONSTRAINT proposal_staged_chk
        CHECK (status <> 'AGENT_STAGED' OR (reviewed_by IS NULL AND reviewed_at IS NULL)),
    CONSTRAINT proposal_review_chk
        CHECK (status NOT IN ('ANALYST_VERIFIED','APPLIED')
               OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL)),
    CONSTRAINT proposal_reject_reason_chk
        CHECK (status <> 'REJECTED' OR length(btrim(coalesce(decision_reason,''))) > 0),
    CONSTRAINT proposal_idempotency_uq
        UNIQUE (agent_type, idempotency_key)
);

CREATE INDEX idx_agent_proposals_status
    ON staging.agent_proposals (status, created_at DESC);
CREATE INDEX idx_agent_proposals_mission
    ON staging.agent_proposals (mission_id, status, created_at DESC);
CREATE INDEX idx_agent_proposals_run
    ON staging.agent_proposals (run_id);
CREATE INDEX idx_agent_proposals_evidence
    ON staging.agent_proposals USING gin (evidence_ids);
```

**Máquina de estados:**

```text
AGENT_STAGED ──approve──> ANALYST_VERIFIED ──apply──> APPLIED
      │                         │
      ├──reject──> REJECTED     └──reject──> REJECTED
      └──expire──> EXPIRED
```

- `AGENT_STAGED` significa: el harness ha hablado, pero **nada** se ha escrito en `core_entities`, `observables`, `intelligence_events` ni `intelligence_relationships`.
- `ANALYST_VERIFIED` requiere usuario humano con rol `ANALYST`/`ADMIN` y un `reviewed_by` no vacío.
- `APPLIED` sólo lo marca `ATENEA` después de escribir la capa 2; guarda `applied_object_id`.
- Un `AGENT_STAGED` con `expires_at` vencido pasa a `EXPIRED` por job; no se borra.
- Los errores de esquema/policy se guardan en `validation_errors` y el estado queda `REJECTED` (no se reintenta a ciegas).

### 9.3 `intel.entity_merge_log`

```sql
CREATE TABLE intel.entity_merge_log (
    merge_id          uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    source_entity_id  uuid NOT NULL REFERENCES intel.core_entities(entity_id)
                      ON DELETE RESTRICT,
    target_entity_id  uuid NOT NULL REFERENCES intel.core_entities(entity_id)
                      ON DELETE RESTRICT,
    method            text NOT NULL CHECK (method IN ('EXACT_SELECTOR','ALIAS_HISTORY','TRIGRAM','EMBEDDING','HUMAN')),
    score             numeric(5,4) CHECK (score BETWEEN 0 AND 1),
    evidence_ids      uuid[] NOT NULL DEFAULT '{}'::uuid[],
    status            text NOT NULL DEFAULT 'PROPOSED'
                      CHECK (status IN ('PROPOSED','APPROVED','REJECTED')),
    proposed_by_agent intel.agent_type_enum NOT NULL,
    reviewed_by       text,
    reviewed_at       timestamptz,
    reason            text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT merge_no_self_chk CHECK (source_entity_id <> target_entity_id)
);

CREATE INDEX idx_merge_source ON intel.entity_merge_log (source_entity_id);
CREATE INDEX idx_merge_target ON intel.entity_merge_log (target_entity_id);
```

Al aprobar una fusión, `ATENEA` debe:

1. Crear/reutilizar la relación `ALIAS_OF` con evidencia.
2. Reapuntar observables y aristas al `target_entity_id` mediante funciones controladas.
3. Marcar `source_entity_id.revoked = true`, `revoked_at`, `revoked_reason = 'MERGED_INTO <target>'`.
4. Registrar `entity_merge_log.status = 'APPROVED'` y `audit.events`.
5. **Nunca** borrar la entidad fuente.

---

## 10. Vistas de fusión y lectura

```sql
CREATE VIEW api.v_entity_fusion
WITH (security_invoker = true) AS
SELECT
    e.entity_id,
    e.stix_id,
    e.stix_type,
    e.canonical_name,
    e.primary_discipline,
    e.confidence,
    e.admiralty_rating,
    e.tlp_classification,
    e.valid_from,
    e.valid_until,
    e.revoked,
    coalesce(
        jsonb_agg(DISTINCT jsonb_build_object(
            'type', o.observable_type,
            'value', o.immutable_value,
            'hash', o.value_hash,
            'valid_from', o.valid_from,
            'valid_until', o.valid_until
        )) FILTER (WHERE o.observable_id IS NOT NULL),
        '[]'::jsonb
    ) AS observables,
    coalesce(
        jsonb_agg(DISTINCT jsonb_build_object(
            'type', a.alias_type,
            'value', a.alias_value,
            'platform', a.platform,
            'from', a.observed_from,
            'until', a.observed_until
        )) FILTER (WHERE a.alias_id IS NOT NULL),
        '[]'::jsonb
    ) AS aliases
FROM intel.core_entities e
LEFT JOIN intel.observables o ON o.entity_id = e.entity_id AND o.revoked = false
LEFT JOIN intel.observable_alias_history a ON a.observable_id = o.observable_id
     AND a.revoked = false
GROUP BY e.entity_id;

CREATE VIEW api.v_observable_current_alias
WITH (security_invoker = true) AS
SELECT DISTINCT ON (observable_id)
    observable_id, alias_id, alias_type, alias_value, normalized_value,
    platform, observed_from, observed_until, confidence
FROM intel.observable_alias_history
WHERE revoked = false
  AND (observed_until IS NULL OR observed_until > now())
ORDER BY observable_id, observed_from DESC;

CREATE VIEW api.v_entity_timeline
WITH (security_invoker = true) AS
SELECT
    entity_id,
    event_id AS item_id,
    'EVENT'::text AS item_type,
    event_time AS occurred_at,
    discipline,
    event_type::text AS subtype,
    confidence,
    tlp_classification,
    location,
    source_evidence_id
FROM intel.intelligence_events
WHERE revoked = false
UNION ALL
SELECT
    source_entity_id AS entity_id,
    relationship_id AS item_id,
    'RELATIONSHIP'::text AS item_type,
    valid_from AS occurred_at,
    discipline,
    relationship_type AS subtype,
    confidence_score AS confidence,
    r.tlp_classification,
    NULL::geography AS location,
    NULL::uuid AS source_evidence_id
FROM intel.intelligence_relationships r
WHERE revoked = false;
```

> Las vistas usan `security_invoker=true` para que RLS de las tablas subyacentes se aplique al rol que consulta. La API nunca concede `SELECT` directo a `intel.*`; sólo a `api.*`.

---

## 11. Triggers de integridad

### 11.0 Búsqueda y campos derivados

```sql
CREATE OR REPLACE FUNCTION intel.fn_core_entities_search()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv := to_tsvector(
        'spanish',
        coalesce(NEW.canonical_name, '') || ' ' ||
        coalesce(NEW.properties->>'description', '') || ' ' ||
        coalesce(NEW.properties->>'summary', '')
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_core_entities_search
BEFORE INSERT OR UPDATE OF canonical_name, properties ON intel.core_entities
FOR EACH ROW EXECUTE FUNCTION intel.fn_core_entities_search();

CREATE OR REPLACE FUNCTION intel.fn_events_search()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    NEW.search_tsv := to_tsvector(
        'spanish',
        coalesce(NEW.event_type::text, '') || ' ' ||
        coalesce(NEW.payload->>'text', '') || ' ' ||
        coalesce(NEW.payload->>'summary', '') || ' ' ||
        coalesce(NEW.payload->>'description', '')
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_events_search
BEFORE INSERT OR UPDATE OF event_type, payload ON intel.intelligence_events
FOR EACH ROW EXECUTE FUNCTION intel.fn_events_search();
```

### 11.0.1 Inmutabilidad de selectores y eventos

```sql
CREATE OR REPLACE FUNCTION intel.fn_guard_observable_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.observable_type IS DISTINCT FROM OLD.observable_type
       OR NEW.immutable_value IS DISTINCT FROM OLD.immutable_value
       OR NEW.normalized_value IS DISTINCT FROM OLD.normalized_value
       OR NEW.value_hash IS DISTINCT FROM OLD.value_hash
       OR NEW.entity_id IS DISTINCT FROM OLD.entity_id THEN
        RAISE EXCEPTION 'El selector es inmutable; revoque y cree uno nuevo'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_observables_guard_update
BEFORE UPDATE ON intel.observables
FOR EACH ROW EXECUTE FUNCTION intel.fn_guard_observable_update();

CREATE OR REPLACE FUNCTION intel.fn_guard_alias_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.observable_id IS DISTINCT FROM OLD.observable_id
       OR NEW.alias_type IS DISTINCT FROM OLD.alias_type
       OR NEW.alias_value IS DISTINCT FROM OLD.alias_value
       OR NEW.normalized_value IS DISTINCT FROM OLD.normalized_value
       OR NEW.platform IS DISTINCT FROM OLD.platform
       OR NEW.observed_from IS DISTINCT FROM OLD.observed_from
       OR NEW.source_evidence_id IS DISTINCT FROM OLD.source_evidence_id THEN
        RAISE EXCEPTION 'El alias histórico es inmutable; cierre la ventana y cree otra'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_alias_guard_update
BEFORE UPDATE ON intel.observable_alias_history
FOR EACH ROW EXECUTE FUNCTION intel.fn_guard_alias_update();

CREATE OR REPLACE FUNCTION intel.fn_guard_event_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.event_type IS DISTINCT FROM OLD.event_type
       OR NEW.discipline IS DISTINCT FROM OLD.discipline
       OR NEW.mission_id IS DISTINCT FROM OLD.mission_id
       OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
       OR NEW.observable_id IS DISTINCT FROM OLD.observable_id
       OR NEW.event_time IS DISTINCT FROM OLD.event_time
       OR NEW.observed_at IS DISTINCT FROM OLD.observed_at
       OR NEW.location IS DISTINCT FROM OLD.location
       OR NEW.source_evidence_id IS DISTINCT FROM OLD.source_evidence_id
       OR NEW.payload IS DISTINCT FROM OLD.payload THEN
        RAISE EXCEPTION 'El evento validado es inmutable; revoque y cree otro'
            USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_events_guard_update
BEFORE UPDATE ON intel.intelligence_events
FOR EACH ROW EXECUTE FUNCTION intel.fn_guard_event_update();
```

```sql
CREATE OR REPLACE FUNCTION intel.fn_no_delete()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'Borrado físico prohibido; use revocación' USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_core_entities_no_delete
BEFORE DELETE ON intel.core_entities
FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE TRIGGER trg_observables_no_delete
BEFORE DELETE ON intel.observables
FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE TRIGGER trg_alias_no_delete
BEFORE DELETE ON intel.observable_alias_history
FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE TRIGGER trg_events_no_delete
BEFORE DELETE ON intel.intelligence_events
FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE TRIGGER trg_relationships_no_delete
BEFORE DELETE ON intel.intelligence_relationships
FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE TRIGGER trg_merge_log_no_delete
BEFORE DELETE ON intel.entity_merge_log
FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

-- Adjuntos y tool calls: append-only absoluto
CREATE TRIGGER trg_raw_attachments_no_update_delete
BEFORE UPDATE OR DELETE ON staging.raw_evidence_attachments
FOR EACH ROW EXECUTE FUNCTION staging.fn_raw_evidence_immutable();

CREATE TRIGGER trg_hermes_tool_calls_no_update_delete
BEFORE UPDATE OR DELETE ON audit.hermes_tool_calls
FOR EACH ROW EXECUTE FUNCTION staging.fn_raw_evidence_immutable();

CREATE TRIGGER trg_hermes_tool_calls_no_truncate
BEFORE TRUNCATE ON audit.hermes_tool_calls
FOR EACH STATEMENT EXECUTE FUNCTION staging.fn_raw_evidence_immutable();
```

### 11.1 TLP no downgrade

```sql
CREATE OR REPLACE FUNCTION intel.fn_tlp_no_downgrade()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.tlp_classification IS DISTINCT FROM NEW.tlp_classification
       AND intel.tlp_rank(NEW.tlp_classification) < intel.tlp_rank(OLD.tlp_classification)
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Degradación TLP no autorizada' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_core_entities_tlp
BEFORE UPDATE OF tlp_classification ON intel.core_entities
FOR EACH ROW EXECUTE FUNCTION intel.fn_tlp_no_downgrade();

CREATE TRIGGER trg_observables_tlp
BEFORE UPDATE OF tlp_classification ON intel.observables
FOR EACH ROW EXECUTE FUNCTION intel.fn_tlp_no_downgrade();

CREATE TRIGGER trg_events_tlp
BEFORE UPDATE OF tlp_classification ON intel.intelligence_events
FOR EACH ROW EXECUTE FUNCTION intel.fn_tlp_no_downgrade();

CREATE TRIGGER trg_intel_rel_tlp
BEFORE UPDATE OF tlp_classification ON intel.intelligence_relationships
FOR EACH ROW EXECUTE FUNCTION intel.fn_tlp_no_downgrade();
```

### 11.2 Validación de propuestas

```sql
CREATE OR REPLACE FUNCTION staging.fn_proposal_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = NEW.status THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'AGENT_STAGED'
       AND NEW.status IN ('ANALYST_VERIFIED','REJECTED','EXPIRED') THEN
        RETURN NEW;
    END IF;
    IF OLD.status = 'ANALYST_VERIFIED'
       AND NEW.status IN ('APPLIED','REJECTED') THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Transición de propuesta inválida: % -> %', OLD.status, NEW.status
        USING ERRCODE = '23514';
END;
$$;

CREATE TRIGGER trg_proposal_transition
BEFORE UPDATE OF status ON staging.agent_proposals
FOR EACH ROW EXECUTE FUNCTION staging.fn_proposal_transition();
```

### 11.3 Evidencia obligatoria

```sql
CREATE OR REPLACE FUNCTION intel.fn_require_evidence()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_count integer;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM staging.raw_evidence_vault
        WHERE evidence_id = NEW.evidence_id
    ) THEN
        RAISE EXCEPTION 'Evidencia inexistente' USING ERRCODE = '23503';
    END IF;

    IF NEW.entity_id IS NOT NULL THEN
        SELECT count(*) INTO v_count FROM intel.core_entities
        WHERE entity_id = NEW.entity_id;
    ELSIF NEW.relationship_id IS NOT NULL THEN
        SELECT count(*) INTO v_count FROM intel.intelligence_relationships
        WHERE relationship_id = NEW.relationship_id;
    ELSIF NEW.observable_id IS NOT NULL THEN
        SELECT count(*) INTO v_count FROM intel.observables
        WHERE observable_id = NEW.observable_id;
    ELSIF NEW.alias_id IS NOT NULL THEN
        SELECT count(*) INTO v_count FROM intel.observable_alias_history
        WHERE alias_id = NEW.alias_id;
    ELSIF NEW.event_id IS NOT NULL THEN
        SELECT count(*) INTO v_count FROM intel.intelligence_events
        WHERE event_id = NEW.event_id;
    ELSE
        RAISE EXCEPTION 'Aserción sin target' USING ERRCODE = '23514';
    END IF;

    IF v_count = 0 THEN
        RAISE EXCEPTION 'Target de evidencia inexistente' USING ERRCODE = '23503';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_evidence_link_integrity
BEFORE INSERT ON intel.evidence_links
FOR EACH ROW EXECUTE FUNCTION intel.fn_require_evidence();
```

> La obligación “toda aserción tiene evidencia” se aplica en `intel.upsert_*` y en la aplicación; el trigger anterior protege la tabla de enlaces. La validación de que una entidad/relación/evento tenga al menos un enlace se ejecuta en la función de escritura (`ATENEA`), no en un `AFTER INSERT` diferido, para evitar ventanas inconsistentes.

---

## 12. RLS, roles y permisos

```sql
-- Roles de login (sin contraseña; el gestor de secretos la inyecta)
DO $$
BEGIN
    CREATE ROLE svc_hermes LOGIN;
    CREATE ROLE svc_aracne LOGIN;
    CREATE ROLE svc_argos LOGIN;
    CREATE ROLE svc_atenea LOGIN;
    CREATE ROLE svc_director_cell LOGIN;
    CREATE ROLE api_reader LOGIN;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

GRANT USAGE ON SCHEMA staging, intel, api TO svc_hermes, svc_aracne, svc_argos, svc_atenea, api_reader;
GRANT USAGE ON SCHEMA intel, api TO svc_director_cell;
GRANT SELECT ON api.v_entity_fusion, api.v_observable_current_alias, api.v_entity_timeline
    TO api_reader, svc_director_cell, svc_atenea;

-- Las vistas usan security_invoker; por tanto el rol lector necesita SELECT
-- sobre las tablas base, siempre protegido por RLS. No se concede INSERT/UPDATE/DELETE.
GRANT SELECT ON
    intel.core_entities, intel.observables, intel.observable_alias_history,
    intel.intelligence_events, intel.intelligence_relationships,
    staging.raw_evidence_vault, staging.raw_evidence_attachments
TO api_reader, svc_director_cell;

REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA intel FROM api_reader;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA staging FROM api_reader;
```

> La API no recibe credenciales de owner. `api_reader` sólo puede leer lo que RLS/TLP permita; las escrituras pasan por funciones `SECURITY DEFINER` y roles `svc_*`.

### 12.1 Políticas TLP

```sql
ALTER TABLE intel.core_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.observables ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.observable_alias_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.intelligence_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.intelligence_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.raw_evidence_vault ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.agent_proposals ENABLE ROW LEVEL SECURITY;

CREATE POLICY core_entities_tlp_read ON intel.core_entities
FOR SELECT USING (
    intel.tlp_rank(tlp_classification) <=
    intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
);

CREATE POLICY core_entities_atenea_all ON intel.core_entities
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

CREATE POLICY observables_tlp_read ON intel.observables
FOR SELECT USING (
    intel.tlp_rank(tlp_classification) <=
    intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
);

CREATE POLICY observables_atenea_all ON intel.observables
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

CREATE POLICY alias_tlp_read ON intel.observable_alias_history
FOR SELECT USING (
    intel.tlp_rank(tlp_classification) <=
    intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
);

CREATE POLICY alias_atenea_all ON intel.observable_alias_history
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

CREATE POLICY events_tlp_read ON intel.intelligence_events
FOR SELECT USING (
    intel.tlp_rank(tlp_classification) <=
    intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
);

CREATE POLICY events_atenea_all ON intel.intelligence_events
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

CREATE POLICY relationships_tlp_read ON intel.intelligence_relationships
FOR SELECT USING (
    intel.tlp_rank(tlp_classification::text) <=
    intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
);

CREATE POLICY relationships_atenea_all ON intel.intelligence_relationships
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

CREATE POLICY raw_vault_collector_own ON staging.raw_evidence_vault
FOR SELECT USING (
    session_user IN ('svc_hermes','svc_aracne','svc_argos')
    AND collector_agent = CASE session_user
        WHEN 'svc_hermes' THEN 'HERMES_HUMINT'::staging.collector_agent_enum
        WHEN 'svc_aracne' THEN 'ARACNE_SOCMINT'::staging.collector_agent_enum
        WHEN 'svc_argos'  THEN 'ARGOS_OSINT'::staging.collector_agent_enum
    END
);

CREATE POLICY raw_vault_read_tlp ON staging.raw_evidence_vault
FOR SELECT USING (
    intel.tlp_rank(tlp_classification::text) <=
    intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
);

CREATE POLICY raw_vault_atenea_all ON staging.raw_evidence_vault
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

ALTER TABLE staging.raw_evidence_attachments ENABLE ROW LEVEL SECURITY;

CREATE POLICY raw_attachments_read ON staging.raw_evidence_attachments
FOR SELECT USING (
    EXISTS (
        SELECT 1 FROM staging.raw_evidence_vault v
        WHERE v.evidence_id = raw_evidence_attachments.evidence_id
          AND intel.tlp_rank(v.tlp_classification::text) <=
              intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
    )
);

CREATE POLICY raw_attachments_atenea_all ON staging.raw_evidence_attachments
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

CREATE POLICY proposals_atenea_all ON staging.agent_proposals
FOR ALL USING (session_user = 'svc_atenea')
WITH CHECK (session_user = 'svc_atenea');

CREATE POLICY proposals_collector_read ON staging.agent_proposals
FOR SELECT USING (
    session_user IN ('svc_hermes','svc_aracne','svc_argos')
    AND agent_type::text = CASE session_user
        WHEN 'svc_hermes' THEN 'HERMES'
        WHEN 'svc_aracne' THEN 'ARACNE'
        WHEN 'svc_argos'  THEN 'ARGOS'
    END
);
```

> La API humana no usa `svc_*`; usa `api_reader` con `SET LOCAL osint.clearance` y `SET LOCAL osint.analyst_id`, y llama a funciones `SECURITY DEFINER` de aprobación. Ningún rol de agente puede leer el compartimento HUMINT de otro.

---

## 13. Migración desde v3.0.0

### 13.1 Fases

| Fase | Acción | Rollback |
|---|---|---|
| M0 | Crear `intel.missions`, `intel.pirs` y `audit.hermes_agent_runs` antes de las tablas que los referencian | orden inverso |
| M1 | Crear extensiones `postgis`, `vector`, `btree_gist`, `pg_trgm` | `DROP EXTENSION` sólo en entorno de pruebas |
| M2 | Renombrar tablas v3 a nombres nuevos | rename inverso |
| M3 | Crear tablas nuevas (`observables`, `alias_history`, `events`, `proposals`, `merge_log`, `tool_calls`) | `DROP TABLE` en orden inverso |
| M4 | Backfill de alias desde `intel.entity_aliases` a `observable_alias_history` | tabla de staging temporal |
| M5 | Crear vistas de compatibilidad `intel.intel_entities`, `intel.intel_relationships`, `staging.raw_intel_ledger`, `audit.agent_runs` | `DROP VIEW` |
| M6 | Reescribir funciones `upsert_entity`, `upsert_relationship`, `append_evidence` | versión anterior en `migrations/rollback` |
| M7 | Validar conteos y hashes; sellar contrato v3.1 | `GRAPH_BACKEND=neo4j` |

### 13.2 Backfill de alias

```sql
INSERT INTO intel.observable_alias_history (
    observable_id, alias_type, alias_value, normalized_value, platform,
    observed_from, source_evidence_id, confidence, tlp_classification
)
SELECT o.observable_id,
       CASE ea.alias_type
           WHEN 'handle' THEN 'HANDLE'::intel.alias_type_enum
           WHEN 'name' THEN 'DISPLAY_NAME'::intel.alias_type_enum
           WHEN 'phone' THEN 'PHONE'::intel.alias_type_enum
           WHEN 'email' THEN 'EMAIL'::intel.alias_type_enum
           WHEN 'wallet' THEN 'WALLET'::intel.alias_type_enum
           WHEN 'domain' THEN 'URL'::intel.alias_type_enum
           ELSE 'OTHER'::intel.alias_type_enum
       END,
       ea.alias,
       ea.normalized_value,
       NULL,
       ea.created_at,
       ea.source_evidence_id,
       ea.confidence,
       e.tlp_classification
FROM intel.entity_aliases ea
JOIN intel.core_entities e ON e.entity_id = ea.entity_id
JOIN LATERAL (
    SELECT o.observable_id
    FROM intel.observables o
    WHERE o.entity_id = ea.entity_id
    ORDER BY o.created_at
    LIMIT 1
) o ON true
WHERE ea.source_evidence_id IS NOT NULL;
```

> Si una entidad no tiene ningún observable, el backfill se detiene y se eleva `SPEC-GAP-DM-001`; no se inventa un selector.

### 13.3 Compatibilidad

```sql
CREATE VIEW staging.raw_intel_ledger
WITH (security_invoker = true) AS
SELECT * FROM staging.raw_evidence_vault;

CREATE VIEW audit.agent_runs
WITH (security_invoker = true) AS
SELECT * FROM audit.hermes_agent_runs;

CREATE VIEW intel.intel_entities
WITH (security_invoker = true) AS
SELECT e.entity_id, e.stix_id, e.stix_type, e.canonical_name AS name,
       coalesce(array_agg(DISTINCT a.alias_value) FILTER (WHERE a.alias_id IS NOT NULL),
                '{}'::text[]) AS aliases,
       e.properties, e.confidence, e.admiralty_rating, e.tlp_classification,
       e.valid_from, e.valid_until, e.first_seen_at, e.last_seen_at,
       e.revoked, e.revoked_at, e.revoked_reason, e.created_by_agent,
       e.created_at, e.updated_at, e.embedding, e.search_tsv
FROM intel.core_entities e
LEFT JOIN intel.observables o ON o.entity_id = e.entity_id AND o.revoked = false
LEFT JOIN intel.observable_alias_history a ON a.observable_id = o.observable_id
     AND a.revoked = false
GROUP BY e.entity_id;
```

> Las vistas son de **sólo lectura**. `intel.upsert_entity` usa `core_entities`; la app v2.2 que espere `aliases[]` sigue leyendo, pero no puede escribir el array.

---

## 14. Requisitos EARS de este modelo

| ID | Requisito EARS | Test |
|---|---|---|
| **DM-E1** | **WHEN** se captura evidencia, **THE SYSTEM SHALL** almacenar bytes, SHA-256, TLP, base legal y hash chain antes de cualquier LLM. | `T-DM-E1` |
| **DM-E2** | **IF** `payload_sha256` no coincide con los bytes, **THEN THE SYSTEM SHALL** rechazar con `23514`. | `T-DM-E2` |
| **DM-E3** | **WHEN** se repite `(collector_agent, idempotency_key)` con bytes distintos, **THE SYSTEM SHALL** responder `409 IDEMPOTENCY_CONFLICT`. | `T-DM-E3` |
| **DM-E4** | **WHEN** se valida una entidad/relación/evento, **THE SYSTEM SHALL** exigir al menos un `evidence_id` existente. | `T-DM-E4` |
| **DM-E5** | **WHEN** se registra un selector, **THE SYSTEM SHALL** calcular `value_hash` y **SHALL NOT** permitir dos entidades activas para el mismo selector en la misma ventana. | `T-DM-E5` |
| **DM-E6** | **WHEN** cambia un alias, **THE SYSTEM SHALL** cerrar la ventana anterior y abrir una nueva; **SHALL NOT** sobrescribir el histórico. | `T-DM-E6` |
| **DM-E7** | **WHEN** se detecta un alias/selector coincidente, **THE SYSTEM SHALL** proponer `ALIAS_OF`/fusión, no duplicar. | `T-DM-E7` |
| **DM-E8** | **IF** se intenta `DELETE`, **THEN THE SYSTEM SHALL** rechazar con `42501` y exigir revocación. | `T-DM-E8` |
| **DM-E9** | **WHEN** se consulta con `as_of`, **THE SYSTEM SHALL** devolver sólo aserciones con `valid_from <= as_of < valid_until` y no revocadas. | `T-DM-E9` |
| **DM-E10** | **WHEN** se lee por API, **THE SYSTEM SHALL** aplicar RLS/TLP y **SHALL NOT** exponer `raw_evidence_vault` sin permiso. | `T-DM-E10` |
| **DM-E11** | **WHEN** se registra un evento SIGINT, **THE SYSTEM SHALL** exigir `observable_id` o selector en `payload`, y `location` con SRID 4326 si existe. | `T-DM-E11` |
| **DM-E12** | **WHEN** se aprueba una propuesta, **THE SYSTEM SHALL** pasar `AGENT_STAGED → ANALYST_VERIFIED → APPLIED` y auditar cada transición. | `T-DM-E12` |

---

## 15. Escenarios de aceptación (Gherkin)

```gherkin
Feature: Custodia y selectores inmutables

  Scenario: Rechazo de evidencia alterada
    Given un payload de 120 bytes y su SHA-256 correcto
    When un colector intenta insertar bytes distintos con el hash anterior
    Then PostgreSQL rechaza la transacción con SQLSTATE 23514
    And el ledger no gana ninguna fila

  Scenario: Idempotencia con colisión
    Given una evidencia ya registrada con idempotency_key K
    When el mismo colector reenvía K con un payload distinto
    Then la API responde 409 IDEMPOTENCY_CONFLICT
    And se crea un audit.event de tipo IDEMPOTENCY_COLLISION
    And no se devuelve la evidencia anterior como si fuera válida

  Scenario: Selector duplicado en la misma ventana
    Given una entidad E1 con IMSI 123456789012345
    When ATENEA intenta asignar el mismo IMSI a E2 en una ventana solapada
    Then PostgreSQL rechaza con SQLSTATE 23P01
    And E2 no queda creada

  Scenario: Historial de alias
    Given un observable con alias "@viejo" observado del 1 al 10 de marzo
    When se registra "@nuevo" el 11 de marzo
    Then el alias "@viejo" conserva observed_until = 10 de marzo
    And la consulta as_of 5 de marzo devuelve "@viejo"
    And la consulta actual devuelve "@nuevo"

  Scenario: Fusión sin borrado
    Given dos entidades E1 y E2 con el mismo selector
    When ATENEA aprueba una fusión E1 -> E2
    Then se crea ALIAS_OF con evidencia
    And E1.revoked = true con motivo MERGED_INTO E2
    And no se ejecuta DELETE
```

---

## 16. Trazabilidad y preguntas abiertas

| Requisito base | Este spec | Tarea |
|---|---|---|
| R-01, R-02 | DM-E1, DM-E2, DM-E8 | TASK-06, TASK-09, TASK-10 |
| R-03, R-04 | DM-E4 | TASK-14 |
| R-05, R-06 | Almirantazgo/confianza en `core_entities` | TASK-14 |
| R-07 | DM-E5, DM-E7 | TASK-08, TASK-19 |
| R-08 | OPSEC en `humint` (se conserva) | TASK-22 |
| R-09, R-10 | Roles `svc_*` y RLS | TASK-24 |
| R-11 | `valid_from/valid_until` + exclusiones | TASK-07, TASK-13 |
| R-12 | TLP + RLS | TASK-24 |
| R-15 | DM-E8 | TASK-14 |
| R-19 | `audit.events` + hash chain | TASK-10 |
| R-20 | DM-E3 | TASK-18 |
| R-27 | Sólo funciones `SECURITY DEFINER` | TASK-16 |

### 16.1 SPEC-GAP

- **SPEC-GAP-DM-001:** no hay una regla aprobada para mapear entidades sin ningún selector técnico (p. ej. una persona mencionada sólo por nombre). Propuesta: esas entidades quedan en `AGENT_STAGED` y no pasan a `core_entities` hasta que exista un selector o una decisión humana documentada.
- **SPEC-GAP-DM-002:** política de cifrado de selectores sensibles (IMSI/MAC) en reposo y en API. Propuesta: cifrado a nivel de columna con KMS y máscara por TLP.
- **SPEC-GAP-DM-003:** retención/WORM de adjuntos. Propuesta: object store con object-lock y `immutable_uri`; la DB sólo guarda referencia y hash.
- **SPEC-GAP-DM-004:** `as_of` y revocaciones: una entidad revocada no debe aparecer en consultas históricas posteriores a la revocación, pero sí antes. Propuesta: `revoked_at` se usa como cota; validar con tests.

**No se implementa hasta aprobación de estos gaps.**
