-- =====================================================================
-- DB_CONTRACT.sql — Contrato físico v3.0.0 (SPEC aprobada)
-- Proyecto: OSINT Nexus Platform
-- Motor: PostgreSQL 16 + pgvector + pgcrypto + btree_gist + pg_trgm
-- Autorizado por: SPEC.md v3.0.0 (aprobada 2026-10-23)
-- Estado: DRAFT — NO SELLADO. La implementación del Hito 1 puede usarlo
--         para validación; el sello en contracts/SEAL.md es obligatorio antes
--         de operar en producción.
-- Convenciones:
--   * UUIDv7 generado por staging.uuid_v7() (RFC 9562).
--   * Timestamps siempre timestamptz (UTC).
--   * Hash SHA-256 en hex minúsculas.
--   * Ningún agente escribe tablas base: solo funciones SECURITY DEFINER.
--   * El ledger es append-only; el grafo validado no se borra: se revoca.
--   * Los roles de login (svc_*) se crean aquí SIN contraseña; la
--     contraseña se inyecta desde el gestor de secretos.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 0. Extensiones y esquemas
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- Endurecimiento: las extensiones viven en public, pero nadie puede crear
-- objetos all?. Los roles solo tienen USAGE/EXECUTE cuando se concede.
REVOKE CREATE ON SCHEMA public FROM PUBLIC;

CREATE SCHEMA IF NOT EXISTS staging;
CREATE SCHEMA IF NOT EXISTS intel;
CREATE SCHEMA IF NOT EXISTS humint;
CREATE SCHEMA IF NOT EXISTS audit;
CREATE SCHEMA IF NOT EXISTS api;

COMMENT ON SCHEMA staging IS 'Capa 1: evidencia cruda inmutable y cadena de custodia.';
COMMENT ON SCHEMA intel   IS 'Capa 2: grafo de inteligencia validada STIX 2.1.';
COMMENT ON SCHEMA humint  IS 'Compartimento HUMINT: identidades operativas y OPSEC.';
COMMENT ON SCHEMA audit   IS 'Auditoría append-only y ejecuciones de agente.';
COMMENT ON SCHEMA api     IS 'Vistas de lectura para la API (sin acceso a tablas base).';

-- ---------------------------------------------------------------------
-- 0.1 UUIDv7 (RFC 9562) — debe existir antes de los DEFAULT de las tablas
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION staging.uuid_v7()
RETURNS uuid
LANGUAGE plpgsql
VOLATILE
SET search_path = pg_catalog, staging, public
AS $$
DECLARE
    ts_ms      bytea;
    rand_bytes bytea;
BEGIN
    -- 48 bits de timestamp en milisegundos (big-endian, 6 bytes)
    ts_ms := substring(
        int8send((extract(epoch FROM clock_timestamp()) * 1000)::bigint)
        FROM 3
    );
    -- 80 bits aleatorios; se ajustan versión (7) y variante (10xx)
    rand_bytes := gen_random_bytes(10);
    rand_bytes := set_byte(rand_bytes, 0, (get_byte(rand_bytes, 0) & 15) | 112);
    rand_bytes := set_byte(rand_bytes, 2, (get_byte(rand_bytes, 2) & 63) | 128);
    RETURN encode(ts_ms || rand_bytes, 'hex')::uuid;
END;
$$;

COMMENT ON FUNCTION staging.uuid_v7() IS
    'UUIDv7 ordenado temporalmente (timestamp ms + aleatorio).';

-- ---------------------------------------------------------------------
-- 1. Tipos y dominios
-- ---------------------------------------------------------------------
CREATE TYPE staging.tlp_enum AS ENUM (
    'TLP:RED',
    'TLP:AMBER+STRICT',
    'TLP:AMBER',
    'TLP:GREEN',
    'TLP:CLEAR'
);

CREATE TYPE staging.collector_agent_enum AS ENUM (
    'HERMES_HUMINT',
    'ARACNE_SOCMINT',
    'ARGOS_OSINT',
    'MIGRATION_LEGACY'
);

CREATE TYPE intel.agent_type_enum AS ENUM (
    'DIRECTOR_CELL',
    'HERMES',
    'ARACNE',
    'ARGOS',
    'ATENEA'
);

CREATE DOMAIN intel.admiralty_rating AS text
    CHECK (VALUE ~ '^[A-F][1-6]$');

COMMENT ON DOMAIN intel.admiralty_rating IS
    'Código OTAN/Almirantazgo: fuente A-F, información 1-6.';

-- ---------------------------------------------------------------------
-- 2. Catálogos STIX y de relaciones
-- ---------------------------------------------------------------------
CREATE TABLE intel.stix_types (
    stix_type      text PRIMARY KEY,
    category       text NOT NULL CHECK (category IN ('SDO','SCO','SRO','META')),
    description    text NOT NULL,
    is_custom      boolean NOT NULL DEFAULT false
);

INSERT INTO intel.stix_types (stix_type, category, description, is_custom) VALUES
    ('identity',        'SDO', 'Persona, organización o grupo', false),
    ('user-account',    'SCO', 'Cuenta, handle o alias digital', false),
    ('threat-actor',    'SDO', 'Actor hostil o sujeto de interés', false),
    ('infrastructure',  'SDO', 'Infraestructura técnica', false),
    ('domain-name',     'SCO', 'Nombre de dominio', false),
    ('ipv4-addr',       'SCO', 'Dirección IPv4', false),
    ('ipv6-addr',       'SCO', 'Dirección IPv6', false),
    ('email-addr',      'SCO', 'Dirección de correo', false),
    ('x-osint-phone',   'SCO', 'Teléfono (extensión OSINT)', true),
    ('x-osint-wallet',  'SCO', 'Wallet de criptoactivos (extensión OSINT)', true),
    ('location',        'SDO', 'Ubicación geográfica', false),
    ('x-osint-event',   'SDO', 'Evento o incidente (extensión OSINT)', true),
    ('report',          'SDO', 'Informe de inteligencia', false),
    ('observed-data',   'SDO', 'Observación cruda', false),
    ('note',            'SDO', 'Anotación analítica', false),
    ('opinion',         'SDO', 'Opinión/valoración', false),
    ('indicator',       'SDO', 'Indicador de compromiso', false),
    ('malware',         'SDO', 'Malware', false),
    ('campaign',        'SDO', 'Campaña', false),
    ('relationship',    'SRO', 'Relación STIX', false)
ON CONFLICT (stix_type) DO NOTHING;

CREATE TABLE intel.relationship_types (
    relationship_type     text PRIMARY KEY,
    stix_equivalent       text NOT NULL,
    is_symmetric          boolean NOT NULL DEFAULT false,
    allowed_source_types  text[],
    allowed_target_types  text[],
    description           text NOT NULL
);

INSERT INTO intel.relationship_types
    (relationship_type, stix_equivalent, is_symmetric, allowed_source_types, allowed_target_types, description)
VALUES
    ('CONTROLS_ACCOUNT', 'controls-account', false, ARRAY['identity','threat-actor'], ARRAY['user-account'], 'Controla una cuenta/avatar'),
    ('COMMUNICATES_WITH','communicates-with', true, NULL, NULL, 'Se comunica con'),
    ('FINANCED_BY',      'financed-by',      false, ARRAY['identity','threat-actor','infrastructure'], ARRAY['identity','threat-actor'], 'Financiado por'),
    ('LOCATED_AT',       'located-at',       false, NULL, ARRAY['location'], 'Ubicado en'),
    ('ALIAS_OF',         'alias-of',         true,  NULL, NULL, 'Alias de'),
    ('AFFILIATED_WITH',  'affiliated-with',  true,  NULL, NULL, 'Afiliado con'),
    ('MEMBER_OF',        'member-of',        false, ARRAY['identity','threat-actor','user-account'], ARRAY['identity','threat-actor'], 'Miembro de'),
    ('OWNS',             'owns',             false, ARRAY['identity','threat-actor'], NULL, 'Posee'),
    ('USES',             'uses',             false, ARRAY['identity','threat-actor'], NULL, 'Usa'),
    ('TARGETS',          'targets',          false, ARRAY['identity','threat-actor','campaign'], NULL, 'Tiene como objetivo'),
    ('PARTICIPATED_IN',  'participated-in',  false, ARRAY['identity','threat-actor','user-account'], ARRAY['x-osint-event'], 'Participó en'),
    ('OBSERVED_AT',      'observed-at',      false, NULL, ARRAY['location','x-osint-event'], 'Observado en'),
    ('MENTIONS',         'mentions',         false, NULL, NULL, 'Menciona'),
    ('RELATED_TO',       'related-to',       true,  NULL, NULL, 'Relacionado con')
ON CONFLICT (relationship_type) DO NOTHING;

-- ---------------------------------------------------------------------
-- 3. Control: misiones, PIR, EEI, tasking
-- ---------------------------------------------------------------------
CREATE TABLE intel.missions (
    mission_id       uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    code             text NOT NULL UNIQUE,
    name             text NOT NULL,
    description      text,
    classification   staging.tlp_enum NOT NULL DEFAULT 'TLP:AMBER',
    status           text NOT NULL DEFAULT 'DRAFT'
                     CHECK (status IN ('DRAFT','ACTIVE','PAUSED','CLOSED','ARCHIVED')),
    opsec_policy     jsonb NOT NULL DEFAULT '{}'::jsonb,  -- snapshot; la política ejecutable vive en humint.opsec_policies
    legal_basis      text NOT NULL CHECK (length(btrim(legal_basis)) > 0),
    created_by       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT missions_closed_at_chk
        CHECK ((status IN ('CLOSED','ARCHIVED')) = (closed_at IS NOT NULL))
);

CREATE TABLE intel.pirs (
    pir_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    mission_id    uuid NOT NULL REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    code          text NOT NULL,
    question      text NOT NULL CHECK (length(btrim(question)) > 0),
    priority      smallint NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    status        text NOT NULL DEFAULT 'OPEN'
                  CHECK (status IN ('OPEN','IN_PROGRESS','ANSWERED','CANCELLED')),
    due_at        timestamptz,
    created_by    text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (mission_id, code)
);

CREATE TABLE intel.eeis (
    eei_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    pir_id        uuid NOT NULL REFERENCES intel.pirs(pir_id) ON DELETE RESTRICT,
    code          text NOT NULL,
    question      text NOT NULL CHECK (length(btrim(question)) > 0),
    indicators    jsonb NOT NULL DEFAULT '[]'::jsonb,
    status        text NOT NULL DEFAULT 'OPEN'
                  CHECK (status IN ('OPEN','IN_PROGRESS','ANSWERED','CANCELLED')),
    due_at        timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now(),
    updated_at    timestamptz NOT NULL DEFAULT now(),
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (pir_id, code)
);

CREATE TABLE intel.tasking_orders (
    task_id         uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    mission_id      uuid NOT NULL REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    pir_id          uuid NOT NULL REFERENCES intel.pirs(pir_id) ON DELETE RESTRICT,
    eei_id          uuid REFERENCES intel.eeis(eei_id) ON DELETE RESTRICT,
    assignee_agent  intel.agent_type_enum NOT NULL
                    CHECK (assignee_agent IN ('HERMES','ARACNE','ARGOS')),
    objective       text NOT NULL CHECK (length(btrim(objective)) > 0),
    constraints     jsonb NOT NULL DEFAULT '{}'::jsonb,
    status          text NOT NULL DEFAULT 'ISSUED'
                    CHECK (status IN ('ISSUED','ACCEPTED','IN_PROGRESS','COMPLETED','FAILED','CANCELLED')),
    issued_by       text NOT NULL,
    issued_at       timestamptz NOT NULL DEFAULT now(),
    accepted_at     timestamptz,
    due_at          timestamptz,
    completed_at    timestamptz,
    result_evidence_ids uuid[] NOT NULL DEFAULT '{}'::uuid[],
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_tasking_mission ON intel.tasking_orders (mission_id, status);
CREATE INDEX idx_tasking_pir     ON intel.tasking_orders (pir_id);
CREATE INDEX idx_tasking_eei     ON intel.tasking_orders (eei_id);

-- ---------------------------------------------------------------------
-- 4. Registro de agentes y mapeo de roles DB
-- ---------------------------------------------------------------------
CREATE TABLE intel.agent_registry (
    agent_id         uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    agent_type       intel.agent_type_enum NOT NULL UNIQUE,
    name             text NOT NULL,
    version          text NOT NULL,
    status           text NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('ACTIVE','PAUSED','DEGRADED','REVOKED')),
    capabilities     jsonb NOT NULL DEFAULT '[]'::jsonb,
    api_key_hash     text NOT NULL UNIQUE,
    db_role          text NOT NULL UNIQUE,
    last_heartbeat_at timestamptz,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE staging.agent_role_map (
    db_role          text PRIMARY KEY,
    collector_agent  staging.collector_agent_enum NOT NULL
);

INSERT INTO staging.agent_role_map (db_role, collector_agent) VALUES
    ('svc_hermes', 'HERMES_HUMINT'),
    ('svc_aracne', 'ARACNE_SOCMINT'),
    ('svc_argos',  'ARGOS_OSINT')
ON CONFLICT (db_role) DO NOTHING;

-- ---------------------------------------------------------------------
-- 5. Ejecuciones de agente (auditoría operacional)
-- ---------------------------------------------------------------------
CREATE TABLE audit.agent_runs (
    run_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    agent_type    intel.agent_type_enum NOT NULL,
    mission_id    uuid REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    task_id       uuid REFERENCES intel.tasking_orders(task_id) ON DELETE RESTRICT,
    status        text NOT NULL DEFAULT 'RUNNING'
                  CHECK (status IN ('RUNNING','COMPLETED','FAILED','CANCELLED')),
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    input_hash    text,
    output_hash   text,
    tokens_in     integer NOT NULL DEFAULT 0 CHECK (tokens_in >= 0),
    tokens_out    integer NOT NULL DEFAULT 0 CHECK (tokens_out >= 0),
    cost_usd      numeric(12,4) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    error         text,
    metadata      jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------
-- 6. Capa 1: ledger de evidencia cruda (append-only)
-- ---------------------------------------------------------------------
CREATE TABLE staging.raw_intel_ledger (
    evidence_id         uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    ledger_seq          bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
    mission_pir_id      uuid NOT NULL REFERENCES intel.pirs(pir_id) ON DELETE RESTRICT,
    collector_agent     staging.collector_agent_enum NOT NULL,
    collector_run_id    uuid REFERENCES audit.agent_runs(run_id) ON DELETE RESTRICT,
    idempotency_key     text NOT NULL CHECK (length(btrim(idempotency_key)) > 0),
    source_platform     text NOT NULL CHECK (length(btrim(source_platform)) > 0),
    source_uri          text NOT NULL CHECK (length(btrim(source_uri)) > 0),
    raw_payload         jsonb NOT NULL,
    raw_payload_bytes   bytea NOT NULL,
    payload_sha256      text NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
    captured_at_utc     timestamptz NOT NULL,
    ingested_at         timestamptz NOT NULL DEFAULT now(),
    tlp_classification  staging.tlp_enum NOT NULL,
    collection_method   text,
    legal_basis         text NOT NULL CHECK (length(btrim(legal_basis)) > 0),
    confidence_hint     numeric(3,2) CHECK (confidence_hint BETWEEN 0 AND 1),
    attachments         jsonb NOT NULL DEFAULT '[]'::jsonb,
    metadata            jsonb NOT NULL DEFAULT '{}'::jsonb,
    retention_until     timestamptz,
    legal_hold          boolean NOT NULL DEFAULT false,
    hash_algorithm      text NOT NULL DEFAULT 'SHA-256' CHECK (hash_algorithm = 'SHA-256'),
    canonicalization    text NOT NULL DEFAULT 'RFC8785-JCS' CHECK (canonicalization = 'RFC8785-JCS'),
    prev_hash           text,
    record_hash         text,
    CONSTRAINT ledger_idempotency_uq UNIQUE (collector_agent, idempotency_key)
);

CREATE INDEX idx_ledger_pir_time ON staging.raw_intel_ledger (mission_pir_id, captured_at_utc DESC);
CREATE INDEX idx_ledger_hash     ON staging.raw_intel_ledger (payload_sha256);
CREATE INDEX idx_ledger_agent    ON staging.raw_intel_ledger (collector_agent, captured_at_utc DESC);

CREATE TABLE staging.raw_intel_attachments (
    attachment_id  uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    evidence_id    uuid NOT NULL REFERENCES staging.raw_intel_ledger(evidence_id) ON DELETE RESTRICT,
    filename       text NOT NULL,
    mime_type      text NOT NULL,
    size_bytes     bigint NOT NULL CHECK (size_bytes >= 0),
    sha256         text NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
    storage_uri    text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    metadata       jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_attachments_evidence ON staging.raw_intel_attachments (evidence_id);

-- ---------------------------------------------------------------------
-- 7. Capa 2: grafo validado
-- ---------------------------------------------------------------------
CREATE TABLE intel.intel_entities (
    entity_id           uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    stix_id             text NOT NULL UNIQUE,
    stix_type           text NOT NULL REFERENCES intel.stix_types(stix_type) ON DELETE RESTRICT,
    name                text NOT NULL CHECK (length(btrim(name)) > 0),
    aliases             text[] NOT NULL DEFAULT '{}'::text[],
    properties          jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence          smallint NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    admiralty_rating    intel.admiralty_rating NOT NULL,
    tlp_classification  staging.tlp_enum NOT NULL,
    valid_from          timestamptz NOT NULL DEFAULT now(),
    valid_until         timestamptz,
    first_seen_at       timestamptz,
    last_seen_at        timestamptz,
    revoked             boolean NOT NULL DEFAULT false,
    revoked_at          timestamptz,
    revoked_reason      text,
    created_by_agent    intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                        CHECK (created_by_agent = 'ATENEA'),
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    embedding           vector(1024),
    search_tsv          tsvector,
    CONSTRAINT entities_valid_range_chk
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT entities_revoked_chk
        CHECK ((NOT revoked) OR (revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0)),
    CONSTRAINT entities_stix_id_chk
        CHECK (stix_id LIKE stix_type || '--%')
);

CREATE INDEX idx_entities_stix       ON intel.intel_entities (stix_type, stix_id);
CREATE INDEX idx_entities_name_trgm  ON intel.intel_entities USING gin (name gin_trgm_ops);
CREATE INDEX idx_entities_aliases    ON intel.intel_entities USING gin (aliases);
CREATE INDEX idx_entities_tsv        ON intel.intel_entities USING gin (search_tsv);
CREATE INDEX idx_entities_embedding  ON intel.intel_entities USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_entities_valid      ON intel.intel_entities (valid_from, valid_until);
CREATE INDEX idx_entities_tlp        ON intel.intel_entities (tlp_classification);

CREATE TABLE intel.intel_relationships (
    relationship_id      uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    stix_id              text NOT NULL UNIQUE,
    stix_type            text NOT NULL DEFAULT 'relationship'
                         CHECK (stix_type = 'relationship'),
    relationship_type    text NOT NULL REFERENCES intel.relationship_types(relationship_type) ON DELETE RESTRICT,
    stix_relationship_type text GENERATED ALWAYS AS (lower(replace(relationship_type, '_', '-'))) STORED,
    source_entity_id     uuid NOT NULL REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    target_entity_id     uuid NOT NULL REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    properties           jsonb NOT NULL DEFAULT '{}'::jsonb,
    admiralty_rating     intel.admiralty_rating NOT NULL,
    confidence_score     smallint NOT NULL CHECK (confidence_score BETWEEN 0 AND 100),
    valid_from           timestamptz NOT NULL DEFAULT now(),
    valid_until          timestamptz,
    revoked              boolean NOT NULL DEFAULT false,
    revoked_at           timestamptz,
    revoked_reason       text,
    created_by_agent     intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                         CHECK (created_by_agent = 'ATENEA'),
    created_at           timestamptz NOT NULL DEFAULT now(),
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT relationships_valid_range_chk
        CHECK (valid_until IS NULL OR valid_until > valid_from),
    CONSTRAINT relationships_revoked_chk
        CHECK ((NOT revoked) OR (revoked_at IS NOT NULL AND length(btrim(revoked_reason)) > 0)),
    CONSTRAINT relationships_no_self_chk
        CHECK (source_entity_id <> target_entity_id),
    CONSTRAINT relationships_stix_id_chk
        CHECK (stix_id LIKE 'relationship--%')
);

CREATE INDEX idx_rel_source_target ON intel.intel_relationships (source_entity_id, target_entity_id);
CREATE INDEX idx_rel_type_valid   ON intel.intel_relationships (relationship_type, valid_from, valid_until);
CREATE INDEX idx_rel_period       ON intel.intel_relationships
    USING gist (source_entity_id, target_entity_id, relationship_type,
                tstzrange(valid_from, valid_until, '[)'));
CREATE INDEX idx_rel_tlp          ON intel.intel_relationships (valid_from, valid_until);

CREATE TABLE intel.evidence_links (
    link_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id        uuid REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    relationship_id  uuid REFERENCES intel.intel_relationships(relationship_id) ON DELETE RESTRICT,
    evidence_id      uuid NOT NULL REFERENCES staging.raw_intel_ledger(evidence_id) ON DELETE RESTRICT,
    linked_by_agent  intel.agent_type_enum NOT NULL DEFAULT 'ATENEA'
                     CHECK (linked_by_agent = 'ATENEA'),
    linked_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT evidence_links_one_target_chk
        CHECK (num_nonnulls(entity_id, relationship_id) = 1),
    CONSTRAINT evidence_links_entity_uq UNIQUE (entity_id, evidence_id),
    CONSTRAINT evidence_links_rel_uq UNIQUE (relationship_id, evidence_id)
);

CREATE INDEX idx_evidence_links_evidence ON intel.evidence_links (evidence_id);

CREATE TABLE intel.entity_aliases (
    alias_id            uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    entity_id           uuid NOT NULL REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    alias               text NOT NULL CHECK (length(btrim(alias)) > 0),
    alias_type          text NOT NULL
                        CHECK (alias_type IN ('name','handle','email','phone','domain','wallet','other')),
    normalized_value    text NOT NULL,
    source_evidence_id  uuid REFERENCES staging.raw_intel_ledger(evidence_id) ON DELETE RESTRICT,
    confidence          smallint NOT NULL DEFAULT 50 CHECK (confidence BETWEEN 0 AND 100),
    created_at          timestamptz NOT NULL DEFAULT now(),
    UNIQUE (entity_id, alias_type, normalized_value)
);

CREATE INDEX idx_aliases_normalized ON intel.entity_aliases (alias_type, normalized_value);

CREATE TABLE intel.entity_resolution_candidates (
    candidate_id       uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    source_entity_id   uuid NOT NULL REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    target_entity_id   uuid NOT NULL REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    score              numeric(5,4) NOT NULL CHECK (score BETWEEN 0 AND 1),
    algorithm          text NOT NULL,
    admiralty_rating   intel.admiralty_rating NOT NULL DEFAULT 'F6',
    status             text NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING','ACCEPTED','REJECTED')),
    evidence_ids       uuid[] NOT NULL DEFAULT '{}'::uuid[],
    created_at         timestamptz NOT NULL DEFAULT now(),
    reviewed_by        text,
    reviewed_at        timestamptz,
    decision_reason    text,
    CONSTRAINT candidates_no_self_chk CHECK (source_entity_id <> target_entity_id)
);

CREATE TABLE intel.coherence_alerts (
    alert_id         uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    mission_id       uuid NOT NULL REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    entity_id        uuid REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    relationship_id  uuid REFERENCES intel.intel_relationships(relationship_id) ON DELETE RESTRICT,
    alert_type       text NOT NULL
                     CHECK (alert_type IN ('CONTRADICTION','DECEPTION','HALLUCINATION',
                                           'TEMPORAL_CONFLICT','SOURCE_CONFLICT',
                                           'HIGH_VALUE_CORROBORATION','NEW_ALIAS_MERGE')),
    severity         text NOT NULL DEFAULT 'MEDIUM'
                     CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    status           text NOT NULL DEFAULT 'PENDING_ANALYST'
                     CHECK (status IN ('PENDING_ANALYST','RESOLVED','DISMISSED')),
    details          jsonb NOT NULL DEFAULT '{}'::jsonb,
    evidence_ids     uuid[] NOT NULL DEFAULT '{}'::uuid[],
    created_at       timestamptz NOT NULL DEFAULT now(),
    resolved_by      text,
    resolved_at      timestamptz,
    resolution_note  text
);

CREATE INDEX idx_alerts_status ON intel.coherence_alerts (status, severity, created_at DESC);
CREATE INDEX idx_alerts_mission ON intel.coherence_alerts (mission_id, status);

CREATE TABLE intel.intelligence_reports (
    report_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    mission_id       uuid NOT NULL REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    title            text NOT NULL CHECK (length(btrim(title)) > 0),
    summary          text NOT NULL,
    tlp_classification staging.tlp_enum NOT NULL,
    status           text NOT NULL DEFAULT 'DRAFT'
                     CHECK (status IN ('DRAFT','PENDING_APPROVAL','APPROVED','RELEASED','REVOKED')),
    stix_bundle      jsonb,
    pdf_uri          text,
    created_by       text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    updated_at       timestamptz NOT NULL DEFAULT now(),
    approved_by      text,
    approved_at      timestamptz,
    released_at      timestamptz,
    revoked          boolean NOT NULL DEFAULT false,
    revoked_at       timestamptz,
    revoked_reason   text,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE intel.dissemination (
    dissemination_id uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    report_id        uuid NOT NULL REFERENCES intel.intelligence_reports(report_id) ON DELETE RESTRICT,
    recipient        text NOT NULL,
    channel          text NOT NULL,
    tlp_classification staging.tlp_enum NOT NULL,
    released_at      timestamptz NOT NULL DEFAULT now(),
    acknowledged_at  timestamptz,
    metadata         jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------
-- 8. Compartimento HUMINT
-- ---------------------------------------------------------------------
CREATE TABLE humint.sockpuppets (
    sockpuppet_id   uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    codename        text NOT NULL UNIQUE,
    platform        text NOT NULL,
    handle          text NOT NULL,
    legend_id       uuid,
    status          text NOT NULL DEFAULT 'ACTIVE'
                    CHECK (status IN ('ACTIVE','DORMANT','BURNED','RETIRED')),
    compartment     text NOT NULL,
    risk_score      smallint NOT NULL DEFAULT 0 CHECK (risk_score BETWEEN 0 AND 100),
    legend_encrypted bytea,
    created_by      text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_active_at  timestamptz,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (platform, handle)
);

CREATE TABLE humint.legends (
    legend_id       uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    sockpuppet_id   uuid NOT NULL UNIQUE REFERENCES humint.sockpuppets(sockpuppet_id) ON DELETE RESTRICT,
    backstory       text NOT NULL,
    consistent_facts jsonb NOT NULL DEFAULT '{}'::jsonb,
    verified_at     timestamptz,
    encrypted       boolean NOT NULL DEFAULT true,
    created_at      timestamptz NOT NULL DEFAULT now(),
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb
);

ALTER TABLE humint.sockpuppets
    ADD CONSTRAINT sockpuppets_legend_fk
    FOREIGN KEY (legend_id) REFERENCES humint.legends(legend_id) ON DELETE RESTRICT;

CREATE TABLE humint.interactions (
    interaction_id  uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    sockpuppet_id   uuid NOT NULL REFERENCES humint.sockpuppets(sockpuppet_id) ON DELETE RESTRICT,
    mission_id      uuid NOT NULL REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    target_selector text NOT NULL,
    platform        text NOT NULL,
    channel         text NOT NULL,
    started_at      timestamptz NOT NULL DEFAULT now(),
    ended_at        timestamptz,
    transcript_uri  text,
    transcript_sha256 text CHECK (transcript_sha256 IS NULL OR transcript_sha256 ~ '^[0-9a-f]{64}$'),
    elicitation_goal text NOT NULL,
    risk_level      text NOT NULL DEFAULT 'LOW'
                    CHECK (risk_level IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    opsec_decision  text NOT NULL DEFAULT 'ALLOW'
                    CHECK (opsec_decision IN ('ALLOW','ABORT','ESCALATE')),
    approved_by     text,
    evidence_id     uuid REFERENCES staging.raw_intel_ledger(evidence_id) ON DELETE RESTRICT,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT interactions_end_chk CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE INDEX idx_interactions_sockpuppet ON humint.interactions (sockpuppet_id, started_at DESC);
CREATE INDEX idx_interactions_mission ON humint.interactions (mission_id, started_at DESC);

CREATE TABLE humint.deception_assessments (
    assessment_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    interaction_id       uuid NOT NULL REFERENCES humint.interactions(interaction_id) ON DELETE RESTRICT,
    indicators           jsonb NOT NULL DEFAULT '[]'::jsonb,
    deception_probability numeric(4,3) NOT NULL CHECK (deception_probability BETWEEN 0 AND 1),
    model                text NOT NULL,
    assessed_at          timestamptz NOT NULL DEFAULT now(),
    evidence_ids         uuid[] NOT NULL DEFAULT '{}'::uuid[]
);

CREATE TABLE humint.psych_profiles (
    profile_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    target_entity_id  uuid NOT NULL REFERENCES intel.intel_entities(entity_id) ON DELETE RESTRICT,
    traits            jsonb NOT NULL DEFAULT '{}'::jsonb,
    confidence        smallint NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    assessed_at       timestamptz NOT NULL DEFAULT now(),
    evidence_ids      uuid[] NOT NULL DEFAULT '{}'::uuid[]
);

CREATE TABLE humint.opsec_policies (
    policy_id                 uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    mission_id                uuid NOT NULL REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    max_interactions_per_day  integer NOT NULL DEFAULT 20 CHECK (max_interactions_per_day > 0),
    max_messages_per_target   integer NOT NULL DEFAULT 5 CHECK (max_messages_per_target > 0),
    forbidden_topics          text[] NOT NULL DEFAULT '{}'::text[],
    exposure_rules            jsonb NOT NULL DEFAULT '{}'::jsonb,
    active                    boolean NOT NULL DEFAULT true,
    created_by                text NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),
    updated_at                timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_opsec_active_mission
    ON humint.opsec_policies (mission_id) WHERE active;

CREATE TABLE humint.opsec_events (
    event_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    mission_id      uuid REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    sockpuppet_id   uuid REFERENCES humint.sockpuppets(sockpuppet_id) ON DELETE RESTRICT,
    interaction_id  uuid REFERENCES humint.interactions(interaction_id) ON DELETE RESTRICT,
    policy_id       uuid REFERENCES humint.opsec_policies(policy_id) ON DELETE RESTRICT,
    severity        text NOT NULL CHECK (severity IN ('LOW','MEDIUM','HIGH','CRITICAL')),
    rule_code       text NOT NULL,
    description     text NOT NULL,
    action_taken    text NOT NULL DEFAULT 'ABORT'
                    CHECK (action_taken IN ('ALLOW','ABORT','ESCALATE')),
    occurred_at     timestamptz NOT NULL DEFAULT now(),
    resolved_at     timestamptz,
    metadata        jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX idx_opsec_events_time ON humint.opsec_events (occurred_at DESC, severity);

-- ---------------------------------------------------------------------
-- 9. Auditoría append-only
-- ---------------------------------------------------------------------
CREATE TABLE audit.events (
    event_id      uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    occurred_at   timestamptz NOT NULL DEFAULT now(),
    actor_type    text NOT NULL CHECK (actor_type IN ('HUMAN','AGENT','SYSTEM')),
    actor_id      text NOT NULL,
    action        text NOT NULL,
    object_type   text NOT NULL,
    object_id     text,
    mission_id    uuid REFERENCES intel.missions(mission_id) ON DELETE RESTRICT,
    request_id    text,
    source_ip     inet,
    user_agent    text,
    outcome       text NOT NULL CHECK (outcome IN ('SUCCESS','DENIED','ERROR')),
    details       jsonb NOT NULL DEFAULT '{}'::jsonb,
    prev_hash     text,
    record_hash   text
);

CREATE INDEX idx_audit_time ON audit.events USING brin (occurred_at);
CREATE INDEX idx_audit_actor ON audit.events (actor_type, actor_id, occurred_at DESC);
CREATE INDEX idx_audit_mission ON audit.events (mission_id, occurred_at DESC);

-- ---------------------------------------------------------------------
-- 10. Migración: cuarentena y reconciliación
-- ---------------------------------------------------------------------
CREATE TABLE staging.migration_quarantine (
    quarantine_id  uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    source_label   text NOT NULL,
    source_id      text NOT NULL,
    payload        jsonb NOT NULL,
    reason         text NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    reviewed_by    text,
    decision       text NOT NULL DEFAULT 'PENDING'
                   CHECK (decision IN ('PENDING','MIGRATED','DISCARDED','MANUAL'))
);

CREATE TABLE staging.migration_reconciliation (
    reconciliation_id uuid PRIMARY KEY DEFAULT staging.uuid_v7(),
    run_at            timestamptz NOT NULL DEFAULT now(),
    source_label      text NOT NULL,
    source_count      bigint NOT NULL,
    target_count      bigint NOT NULL,
    hash_mismatch     boolean NOT NULL DEFAULT false,
    details           jsonb NOT NULL DEFAULT '{}'::jsonb
);

-- ---------------------------------------------------------------------
-- 11. Funciones de identidad, TLP y Almirantazgo
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION staging.current_collector_agent()
RETURNS staging.collector_agent_enum
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, staging, public
AS $$
    SELECT m.collector_agent
    FROM staging.agent_role_map m
    WHERE m.db_role = session_user
$$;

COMMENT ON FUNCTION staging.current_collector_agent() IS
    'Deriva el colector del login DB (session_user); no acepta parámetros.';

CREATE OR REPLACE FUNCTION intel.tlp_rank(p_tlp text)
RETURNS integer
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE upper(coalesce(p_tlp, ''))
        WHEN 'TLP:CLEAR'        THEN 0
        WHEN 'TLP:GREEN'        THEN 1
        WHEN 'TLP:AMBER'        THEN 2
        WHEN 'TLP:AMBER+STRICT' THEN 3
        WHEN 'TLP:RED'          THEN 4
        ELSE -1
    END
$$;

CREATE OR REPLACE FUNCTION intel.tlp_marking_id(p_tlp staging.tlp_enum)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE p_tlp
        WHEN 'TLP:CLEAR'        THEN 'marking-definition--613f2e26-407d-48c7-9eca-b8e91df99dc9'
        WHEN 'TLP:GREEN'        THEN 'marking-definition--34098fce-860f-48ae-8e50-ebd3cc5e41da'
        WHEN 'TLP:AMBER'        THEN 'marking-definition--f88d31f6-486f-44da-b317-01333bde0b82'
        WHEN 'TLP:AMBER+STRICT' THEN 'marking-definition--826578e1-40ad-459f-bc73-ede076f81f37'
        WHEN 'TLP:RED'          THEN 'marking-definition--5e57c739-391a-4eb3-b6be-7d15ca92d5ed'
    END
$$;

CREATE OR REPLACE FUNCTION intel.relationship_tlp(p_relationship_id uuid)
RETURNS staging.tlp_enum
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, intel, public
AS $$
    SELECT CASE
             WHEN intel.tlp_rank(se.tlp_classification::text)
                  >= intel.tlp_rank(te.tlp_classification::text)
               THEN se.tlp_classification
             ELSE te.tlp_classification
           END
    FROM intel.intel_relationships r
    JOIN intel.intel_entities se ON se.entity_id = r.source_entity_id
    JOIN intel.intel_entities te ON te.entity_id = r.target_entity_id
    WHERE r.relationship_id = p_relationship_id
$$;

COMMENT ON FUNCTION intel.relationship_tlp(uuid) IS
    'TLP efectivo de una relación = máximo TLP de sus extremos; SECURITY DEFINER para RLS.';

CREATE OR REPLACE FUNCTION intel.admiralty_confidence(p_rating text)
RETURNS smallint
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    v_source integer;
    v_info   integer;
BEGIN
    IF p_rating IS NULL OR p_rating !~ '^[A-F][1-6]$' THEN
        RAISE EXCEPTION 'Almirantazgo inválido: %', p_rating USING ERRCODE = '23514';
    END IF;
    v_source := CASE substr(p_rating, 1, 1)
        WHEN 'A' THEN 100 WHEN 'B' THEN 80 WHEN 'C' THEN 60
        WHEN 'D' THEN 40  WHEN 'E' THEN 20 WHEN 'F' THEN 10
    END;
    v_info := CASE substr(p_rating, 2, 1)
        WHEN '1' THEN 100 WHEN '2' THEN 80 WHEN '3' THEN 60
        WHEN '4' THEN 40  WHEN '5' THEN 20 WHEN '6' THEN 10
    END;
    RETURN round(0.60 * v_source + 0.40 * v_info)::smallint;
END;
$$;

COMMENT ON FUNCTION intel.admiralty_confidence(text) IS
    'Confianza base 0-100 desde [A-F][1-6]: 60% fuente + 40% información.';

CREATE OR REPLACE FUNCTION intel.recalculate_confidence(
    p_entity_id       uuid DEFAULT NULL,
    p_relationship_id uuid DEFAULT NULL
) RETURNS smallint
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_rating         text;
    v_base           smallint;
    v_sources        integer;
    v_contradictions integer;
    v_confidence     smallint;
    v_mission_id     uuid;
BEGIN
    IF (p_entity_id IS NULL) = (p_relationship_id IS NULL) THEN
        RAISE EXCEPTION 'Indique exactamente entity_id o relationship_id'
            USING ERRCODE = '22023';
    END IF;
    IF session_user <> 'svc_atenea'
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Solo ATENEA puede recalcular confianza' USING ERRCODE = '42501';
    END IF;

    IF p_entity_id IS NOT NULL THEN
        SELECT admiralty_rating::text INTO v_rating
        FROM intel.intel_entities WHERE entity_id = p_entity_id;
        IF v_rating IS NULL THEN
            RAISE EXCEPTION 'Entidad inexistente: %', p_entity_id USING ERRCODE = '23503';
        END IF;

        SELECT count(DISTINCT l.collector_agent) INTO v_sources
        FROM intel.evidence_links el
        JOIN staging.raw_intel_ledger l ON l.evidence_id = el.evidence_id
        WHERE el.entity_id = p_entity_id;

        SELECT count(*) INTO v_contradictions
        FROM intel.coherence_alerts
        WHERE entity_id = p_entity_id
          AND alert_type = 'CONTRADICTION'
          AND severity IN ('HIGH','CRITICAL')
          AND status = 'PENDING_ANALYST';

        v_base := intel.admiralty_confidence(v_rating);
        v_confidence := greatest(0, least(100,
            v_base + least(10, 5 * v_sources) - least(30, 15 * v_contradictions)))::smallint;

        UPDATE intel.intel_entities
        SET confidence = v_confidence, updated_at = now()
        WHERE entity_id = p_entity_id;

        SELECT m.mission_id INTO v_mission_id
        FROM intel.evidence_links el
        JOIN staging.raw_intel_ledger l ON l.evidence_id = el.evidence_id
        JOIN intel.pirs p ON p.pir_id = l.mission_pir_id
        JOIN intel.missions m ON m.mission_id = p.mission_id
        WHERE el.entity_id = p_entity_id
        LIMIT 1;

        PERFORM audit.log_event(
            'AGENT', session_user, 'CONFIDENCE_RECALCULATED', 'INTEL_ENTITY',
            p_entity_id::text, v_mission_id, NULL, 'SUCCESS',
            jsonb_build_object('confidence', v_confidence, 'sources', v_sources,
                               'contradictions', v_contradictions)
        );
    ELSE
        SELECT admiralty_rating::text INTO v_rating
        FROM intel.intel_relationships WHERE relationship_id = p_relationship_id;
        IF v_rating IS NULL THEN
            RAISE EXCEPTION 'Relación inexistente: %', p_relationship_id USING ERRCODE = '23503';
        END IF;

        SELECT count(DISTINCT l.collector_agent) INTO v_sources
        FROM intel.evidence_links el
        JOIN staging.raw_intel_ledger l ON l.evidence_id = el.evidence_id
        WHERE el.relationship_id = p_relationship_id;

        SELECT count(*) INTO v_contradictions
        FROM intel.coherence_alerts
        WHERE relationship_id = p_relationship_id
          AND alert_type = 'CONTRADICTION'
          AND severity IN ('HIGH','CRITICAL')
          AND status = 'PENDING_ANALYST';

        v_base := intel.admiralty_confidence(v_rating);
        v_confidence := greatest(0, least(100,
            v_base + least(10, 5 * v_sources) - least(30, 15 * v_contradictions)))::smallint;

        UPDATE intel.intel_relationships
        SET confidence_score = v_confidence, updated_at = now()
        WHERE relationship_id = p_relationship_id;

        SELECT m.mission_id INTO v_mission_id
        FROM intel.evidence_links el
        JOIN staging.raw_intel_ledger l ON l.evidence_id = el.evidence_id
        JOIN intel.pirs p ON p.pir_id = l.mission_pir_id
        JOIN intel.missions m ON m.mission_id = p.mission_id
        WHERE el.relationship_id = p_relationship_id
        LIMIT 1;

        PERFORM audit.log_event(
            'AGENT', session_user, 'CONFIDENCE_RECALCULATED', 'INTEL_RELATIONSHIP',
            p_relationship_id::text, v_mission_id, NULL, 'SUCCESS',
            jsonb_build_object('confidence', v_confidence, 'sources', v_sources,
                               'contradictions', v_contradictions)
        );
    END IF;

    RETURN v_confidence;
END;
$$;

COMMENT ON FUNCTION intel.recalculate_confidence(uuid, uuid) IS
    'Recalcula confianza 0-100: base Almirantazgo + bonus por fuentes - penalización por contradicciones.';

-- ---------------------------------------------------------------------
-- 12. Auditoría append-only con hash chain
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION audit.log_event(
    p_actor_type  text,
    p_actor_id    text,
    p_action      text,
    p_object_type text,
    p_object_id   text,
    p_mission_id  uuid,
    p_request_id  text,
    p_outcome     text,
    p_details     jsonb
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_event_id uuid := staging.uuid_v7();
    v_prev     text;
    v_hash     text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('audit.events'));
    SELECT record_hash INTO v_prev
    FROM audit.events
    ORDER BY occurred_at DESC, event_id DESC
    LIMIT 1;

    v_hash := encode(digest(
        coalesce(v_prev, '') || v_event_id::text || p_action ||
        coalesce(p_object_id, '') || coalesce(p_details::text, '{}'),
        'sha256'), 'hex');

    INSERT INTO audit.events (
        event_id, actor_type, actor_id, action, object_type, object_id,
        mission_id, request_id, outcome, details, prev_hash, record_hash
    ) VALUES (
        v_event_id, p_actor_type, p_actor_id, p_action, p_object_type, p_object_id,
        p_mission_id, p_request_id, p_outcome, coalesce(p_details, '{}'::jsonb),
        v_prev, v_hash
    );
    RETURN v_event_id;
END;
$$;

-- ---------------------------------------------------------------------
-- 13. append_evidence — única puerta de escritura del ledger
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION staging.append_evidence(
    p_idempotency_key     text,
    p_mission_pir_id      uuid,
    p_source_platform     text,
    p_source_uri          text,
    p_raw_payload         jsonb,
    p_raw_payload_bytes   bytea,
    p_payload_sha256      text,
    p_captured_at_utc     timestamptz,
    p_tlp_classification  staging.tlp_enum,
    p_collection_method   text,
    p_legal_basis         text,
    p_confidence_hint     numeric DEFAULT NULL,
    p_attachments         jsonb DEFAULT '[]'::jsonb,
    p_metadata            jsonb DEFAULT '{}'::jsonb
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_agent          staging.collector_agent_enum;
    v_existing       uuid;
    v_pir_status     text;
    v_mission_status text;
    v_mission_id     uuid;
    v_evidence_id    uuid;
    v_captured       jsonb;
    v_prev           text;
    v_hash           text;
BEGIN
    v_agent := staging.current_collector_agent();
    IF v_agent IS NULL OR v_agent = 'MIGRATION_LEGACY' THEN
        RAISE EXCEPTION 'Rol % no autorizado para append_evidence', session_user
            USING ERRCODE = '42501';
    END IF;

    IF p_idempotency_key IS NULL OR length(btrim(p_idempotency_key)) = 0 THEN
        RAISE EXCEPTION 'idempotency_key obligatorio' USING ERRCODE = '22023';
    END IF;

    -- Idempotencia: misma clave + mismo agente devuelve la misma evidencia.
    SELECT evidence_id INTO v_existing
    FROM staging.raw_intel_ledger
    WHERE collector_agent = v_agent AND idempotency_key = p_idempotency_key;
    IF v_existing IS NOT NULL THEN
        RETURN v_existing;
    END IF;

    -- Misión activa y PIR existente.
    SELECT p.status, m.status, m.mission_id
      INTO v_pir_status, v_mission_status, v_mission_id
    FROM intel.pirs p
    JOIN intel.missions m ON m.mission_id = p.mission_id
    WHERE p.pir_id = p_mission_pir_id;

    IF v_pir_status IS NULL THEN
        RAISE EXCEPTION 'PIR inexistente: %', p_mission_pir_id USING ERRCODE = '23503';
    END IF;
    IF v_mission_status <> 'ACTIVE' THEN
        RAISE EXCEPTION 'Misión no activa para PIR %', p_mission_pir_id USING ERRCODE = '23514';
    END IF;

    -- El colector solo puede aportar evidencia a un PIR que tenga tasking vigente.
    IF NOT EXISTS (
        SELECT 1
        FROM intel.tasking_orders t
        WHERE t.mission_id = v_mission_id
          AND t.pir_id = p_mission_pir_id
          AND t.assignee_agent::text = CASE v_agent
                WHEN 'HERMES_HUMINT' THEN 'HERMES'
                WHEN 'ARACNE_SOCMINT' THEN 'ARACNE'
                WHEN 'ARGOS_OSINT'   THEN 'ARGOS'
              END
          AND t.status IN ('ISSUED','ACCEPTED','IN_PROGRESS')
    ) THEN
        RAISE EXCEPTION 'Sin tasking vigente para el PIR %', p_mission_pir_id
            USING ERRCODE = '42501';
    END IF;

    IF p_legal_basis IS NULL OR length(btrim(p_legal_basis)) = 0 THEN
        RAISE EXCEPTION 'legal_basis obligatorio' USING ERRCODE = '23514';
    END IF;

    -- Integridad criptográfica sobre los bytes exactos.
    IF p_payload_sha256 IS NULL OR p_payload_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'payload_sha256 inválido' USING ERRCODE = '23514';
    END IF;
    IF encode(digest(p_raw_payload_bytes, 'sha256'), 'hex') <> p_payload_sha256 THEN
        RAISE EXCEPTION 'Hash de evidencia no coincide con los bytes' USING ERRCODE = '23514';
    END IF;

    BEGIN
        v_captured := convert_from(p_raw_payload_bytes, 'UTF8')::jsonb;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION 'raw_payload_bytes no es JSON UTF-8 válido' USING ERRCODE = '22023';
    END;
    IF v_captured <> p_raw_payload THEN
        RAISE EXCEPTION 'raw_payload no corresponde a raw_payload_bytes' USING ERRCODE = '23514';
    END IF;

    IF jsonb_typeof(p_attachments) <> 'array' THEN
        RAISE EXCEPTION 'attachments debe ser un array JSON' USING ERRCODE = '22023';
    END IF;

    IF p_captured_at_utc > now() + interval '5 minutes' THEN
        RAISE EXCEPTION 'captured_at_utc en el futuro' USING ERRCODE = '22007';
    END IF;

    -- Cadena de custodia: serializa la inserción y encadena hashes.
    PERFORM pg_advisory_xact_lock(hashtext('staging.raw_intel_ledger'));
    SELECT record_hash INTO v_prev
    FROM staging.raw_intel_ledger
    ORDER BY ledger_seq DESC
    LIMIT 1;

    v_evidence_id := staging.uuid_v7();
    v_hash := encode(digest(
        coalesce(v_prev, '') || v_evidence_id::text || p_payload_sha256 ||
        to_char(p_captured_at_utc AT TIME ZONE 'UTC',
                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"') ||
        v_agent::text,
        'sha256'), 'hex');

    INSERT INTO staging.raw_intel_ledger (
        evidence_id, mission_pir_id, collector_agent, idempotency_key,
        source_platform, source_uri, raw_payload, raw_payload_bytes,
        payload_sha256, captured_at_utc, tlp_classification,
        collection_method, legal_basis, confidence_hint, attachments,
        metadata, prev_hash, record_hash
    ) VALUES (
        v_evidence_id, p_mission_pir_id, v_agent, p_idempotency_key,
        p_source_platform, p_source_uri, p_raw_payload, p_raw_payload_bytes,
        p_payload_sha256, p_captured_at_utc, p_tlp_classification,
        p_collection_method, p_legal_basis, p_confidence_hint,
        coalesce(p_attachments, '[]'::jsonb), coalesce(p_metadata, '{}'::jsonb),
        v_prev, v_hash
    );

    -- Los adjuntos viajan en el mismo acto de custodia; su hash es obligatorio.
    INSERT INTO staging.raw_intel_attachments (
        evidence_id, filename, mime_type, size_bytes, sha256, storage_uri, metadata
    )
    SELECT v_evidence_id,
           a->>'filename',
           a->>'mime_type',
           (a->>'size_bytes')::bigint,
           a->>'sha256',
           a->>'storage_uri',
           coalesce(a->'metadata', '{}'::jsonb)
    FROM jsonb_array_elements(coalesce(p_attachments, '[]'::jsonb)) AS a
    WHERE a ? 'filename';

    PERFORM audit.log_event(
        'AGENT', session_user, 'EVIDENCE_APPENDED', 'RAW_EVIDENCE',
        v_evidence_id::text, v_mission_id, NULL, 'SUCCESS',
        jsonb_build_object(
            'collector_agent', v_agent,
            'payload_sha256', p_payload_sha256,
            'tlp', p_tlp_classification
        )
    );

    RETURN v_evidence_id;
END;
$$;

CREATE OR REPLACE FUNCTION staging.verify_evidence(p_evidence_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, staging, public
AS $$
    SELECT payload_sha256 = encode(digest(raw_payload_bytes, 'sha256'), 'hex')
    FROM staging.raw_intel_ledger
    WHERE evidence_id = p_evidence_id
$$;

-- ---------------------------------------------------------------------
-- 14. upsert_entity — única puerta de escritura de entidades
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.upsert_entity(
    p_stix_type           text,
    p_name                text,
    p_admiralty_rating    text,
    p_tlp_classification  staging.tlp_enum,
    p_evidence_ids        uuid[],
    p_aliases             text[] DEFAULT NULL,
    p_properties          jsonb DEFAULT '{}'::jsonb,
    p_valid_from          timestamptz DEFAULT NULL,
    p_valid_until         timestamptz DEFAULT NULL,
    p_confidence_override smallint DEFAULT NULL,
    p_stix_id             text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_entity_id    uuid;
    v_stix_id      text;
    v_confidence   smallint;
    v_missing      integer;
    v_mission_id   uuid;
    v_existing_type text;
BEGIN
    IF session_user <> 'svc_atenea'
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Solo ATENEA puede escribir el grafo' USING ERRCODE = '42501';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM intel.stix_types WHERE stix_type = p_stix_type) THEN
        RAISE EXCEPTION 'stix_type inválido: %', p_stix_type USING ERRCODE = '23503';
    END IF;
    IF p_name IS NULL OR length(btrim(p_name)) = 0 THEN
        RAISE EXCEPTION 'name obligatorio' USING ERRCODE = '23514';
    END IF;
    IF p_evidence_ids IS NULL OR cardinality(p_evidence_ids) < 1 THEN
        RAISE EXCEPTION 'Entidad sin evidencia: supporting_evidence_ids vacío'
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*) INTO v_missing
    FROM unnest(p_evidence_ids) AS e
    LEFT JOIN staging.raw_intel_ledger l ON l.evidence_id = e
    WHERE l.evidence_id IS NULL;
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'Evidencia inexistente en supporting_evidence_ids'
            USING ERRCODE = '23503';
    END IF;

    IF p_valid_until IS NOT NULL
       AND p_valid_from IS NOT NULL
       AND p_valid_until <= p_valid_from THEN
        RAISE EXCEPTION 'valid_until debe ser > valid_from' USING ERRCODE = '23514';
    END IF;

    v_confidence := coalesce(
        p_confidence_override,
        intel.admiralty_confidence(p_admiralty_rating)
    )::smallint;
    IF v_confidence < 0 OR v_confidence > 100 THEN
        RAISE EXCEPTION 'confidence fuera de rango' USING ERRCODE = '23514';
    END IF;

    IF p_stix_id IS NULL THEN
        v_stix_id := p_stix_type || '--' || staging.uuid_v7()::text;

        INSERT INTO intel.intel_entities (
            stix_id, stix_type, name, aliases, properties, confidence,
            admiralty_rating, tlp_classification, valid_from, valid_until,
            first_seen_at, last_seen_at, created_by_agent
        ) VALUES (
            v_stix_id, p_stix_type, p_name, coalesce(p_aliases, '{}'::text[]),
            coalesce(p_properties, '{}'::jsonb), v_confidence, p_admiralty_rating,
            p_tlp_classification, coalesce(p_valid_from, now()), p_valid_until,
            coalesce(p_valid_from, now()), now(), 'ATENEA'
        ) RETURNING entity_id INTO v_entity_id;
    ELSE
        IF p_stix_id NOT LIKE p_stix_type || '--%' THEN
            RAISE EXCEPTION 'stix_id no corresponde a stix_type' USING ERRCODE = '23514';
        END IF;
        SELECT stix_type INTO v_existing_type
        FROM intel.intel_entities WHERE stix_id = p_stix_id;
        IF v_existing_type IS NULL THEN
            RAISE EXCEPTION 'Entidad inexistente: %', p_stix_id USING ERRCODE = '23503';
        END IF;
        IF v_existing_type <> p_stix_type THEN
            RAISE EXCEPTION 'stix_type inmutable para %', p_stix_id USING ERRCODE = '23514';
        END IF;

        UPDATE intel.intel_entities SET
            name = p_name,
            aliases = coalesce(p_aliases, aliases),
            properties = properties || coalesce(p_properties, '{}'::jsonb),
            confidence = v_confidence,
            admiralty_rating = p_admiralty_rating,
            tlp_classification = p_tlp_classification,
            valid_from = coalesce(p_valid_from, valid_from),
            valid_until = coalesce(p_valid_until, valid_until),
            last_seen_at = now(),
            updated_at = now()
        WHERE stix_id = p_stix_id
        RETURNING entity_id INTO v_entity_id;
    END IF;

    INSERT INTO intel.evidence_links (entity_id, evidence_id, linked_by_agent)
    SELECT v_entity_id, e, 'ATENEA'
    FROM unnest(p_evidence_ids) AS e
    ON CONFLICT (entity_id, evidence_id) DO NOTHING;

    IF p_confidence_override IS NULL THEN
        v_confidence := intel.recalculate_confidence(v_entity_id, NULL);
    END IF;

    SELECT m.mission_id INTO v_mission_id
    FROM staging.raw_intel_ledger l
    JOIN intel.pirs p ON p.pir_id = l.mission_pir_id
    JOIN intel.missions m ON m.mission_id = p.mission_id
    WHERE l.evidence_id = p_evidence_ids[1];

    PERFORM audit.log_event(
        'AGENT', session_user, 'ENTITY_UPSERTED', 'INTEL_ENTITY',
        v_entity_id::text, v_mission_id, NULL, 'SUCCESS',
        jsonb_build_object(
            'stix_id', v_stix_id,
            'stix_type', p_stix_type,
            'confidence', v_confidence,
            'admiralty_rating', p_admiralty_rating
        )
    );

    RETURN v_entity_id;
END;
$$;

-- ---------------------------------------------------------------------
-- 15. upsert_relationship — única puerta de escritura de relaciones
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.upsert_relationship(
    p_relationship_type   text,
    p_source_entity_id    uuid,
    p_target_entity_id    uuid,
    p_admiralty_rating    text,
    p_evidence_ids        uuid[],
    p_properties          jsonb DEFAULT '{}'::jsonb,
    p_valid_from          timestamptz DEFAULT NULL,
    p_valid_until         timestamptz DEFAULT NULL,
    p_confidence_override smallint DEFAULT NULL,
    p_stix_id             text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_rel_id        uuid;
    v_stix_id       text;
    v_confidence    smallint;
    v_missing       integer;
    v_mission_id    uuid;
    v_src_type      text;
    v_tgt_type      text;
    v_src_types     text[];
    v_tgt_types     text[];
    v_existing_id   uuid;
    v_existing_type text;
    v_existing_src  uuid;
    v_existing_tgt  uuid;
    v_existing_from timestamptz;
    v_existing_until timestamptz;
BEGIN
    IF session_user <> 'svc_atenea'
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Solo ATENEA puede escribir el grafo' USING ERRCODE = '42501';
    END IF;

    IF p_source_entity_id = p_target_entity_id THEN
        RAISE EXCEPTION 'Una relación no puede apuntar a sí misma' USING ERRCODE = '23514';
    END IF;

    SELECT allowed_source_types, allowed_target_types
      INTO v_src_types, v_tgt_types
    FROM intel.relationship_types
    WHERE relationship_type = p_relationship_type;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'relationship_type inválido: %', p_relationship_type
            USING ERRCODE = '23503';
    END IF;

    SELECT stix_type INTO v_src_type FROM intel.intel_entities WHERE entity_id = p_source_entity_id;
    SELECT stix_type INTO v_tgt_type FROM intel.intel_entities WHERE entity_id = p_target_entity_id;
    IF v_src_type IS NULL OR v_tgt_type IS NULL THEN
        RAISE EXCEPTION 'Entidad origen/destino inexistente' USING ERRCODE = '23503';
    END IF;
    IF v_src_types IS NOT NULL AND NOT (v_src_type = ANY (v_src_types)) THEN
        RAISE EXCEPTION 'Tipo origen % no permitido para %', v_src_type, p_relationship_type
            USING ERRCODE = '23514';
    END IF;
    IF v_tgt_types IS NOT NULL AND NOT (v_tgt_type = ANY (v_tgt_types)) THEN
        RAISE EXCEPTION 'Tipo destino % no permitido para %', v_tgt_type, p_relationship_type
            USING ERRCODE = '23514';
    END IF;

    IF p_evidence_ids IS NULL OR cardinality(p_evidence_ids) < 1 THEN
        RAISE EXCEPTION 'Relación sin evidencia: supporting_evidence_ids vacío'
            USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO v_missing
    FROM unnest(p_evidence_ids) AS e
    LEFT JOIN staging.raw_intel_ledger l ON l.evidence_id = e
    WHERE l.evidence_id IS NULL;
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'Evidencia inexistente en supporting_evidence_ids'
            USING ERRCODE = '23503';
    END IF;

    IF p_valid_until IS NOT NULL
       AND p_valid_from IS NOT NULL
       AND p_valid_until <= p_valid_from THEN
        RAISE EXCEPTION 'valid_until debe ser > valid_from' USING ERRCODE = '23514';
    END IF;

    v_confidence := coalesce(
        p_confidence_override,
        intel.admiralty_confidence(p_admiralty_rating)
    )::smallint;
    IF v_confidence < 0 OR v_confidence > 100 THEN
        RAISE EXCEPTION 'confidence_score fuera de rango' USING ERRCODE = '23514';
    END IF;

    IF p_stix_id IS NOT NULL THEN
        SELECT relationship_id, relationship_type, source_entity_id, target_entity_id,
               valid_from, valid_until
          INTO v_existing_id, v_existing_type, v_existing_src, v_existing_tgt,
               v_existing_from, v_existing_until
        FROM intel.intel_relationships WHERE stix_id = p_stix_id;
        IF v_existing_id IS NULL THEN
            RAISE EXCEPTION 'Relación inexistente: %', p_stix_id USING ERRCODE = '23503';
        END IF;
        IF v_existing_type <> p_relationship_type THEN
            RAISE EXCEPTION 'relationship_type inmutable para %', p_stix_id USING ERRCODE = '23514';
        END IF;
        IF v_existing_src <> p_source_entity_id OR v_existing_tgt <> p_target_entity_id THEN
            RAISE EXCEPTION 'source/target inmutables para %', p_stix_id USING ERRCODE = '23514';
        END IF;
    END IF;

    -- Solapamiento temporal (excluye la propia fila y las revocadas).
    IF EXISTS (
        SELECT 1
        FROM intel.intel_relationships r
        WHERE r.source_entity_id = p_source_entity_id
          AND r.target_entity_id = p_target_entity_id
          AND r.relationship_type = p_relationship_type
          AND NOT r.revoked
          AND (v_existing_id IS NULL OR r.relationship_id <> v_existing_id)
          AND tstzrange(r.valid_from, r.valid_until, '[)')
              && tstzrange(
                     coalesce(p_valid_from, v_existing_from, now()),
                     coalesce(p_valid_until, v_existing_until),
                     '[)')
    ) THEN
        RAISE EXCEPTION 'Solapamiento temporal de relación' USING ERRCODE = '23P01';
    END IF;

    IF p_stix_id IS NULL THEN
        v_stix_id := 'relationship--' || staging.uuid_v7()::text;

        INSERT INTO intel.intel_relationships (
            stix_id, relationship_type, source_entity_id, target_entity_id,
            properties, admiralty_rating, confidence_score,
            valid_from, valid_until, created_by_agent
        ) VALUES (
            v_stix_id, p_relationship_type, p_source_entity_id, p_target_entity_id,
            coalesce(p_properties, '{}'::jsonb), p_admiralty_rating, v_confidence,
            coalesce(p_valid_from, now()), p_valid_until, 'ATENEA'
        ) RETURNING relationship_id INTO v_rel_id;
    ELSE
        v_stix_id := p_stix_id;
        UPDATE intel.intel_relationships SET
            properties = properties || coalesce(p_properties, '{}'::jsonb),
            admiralty_rating = p_admiralty_rating,
            confidence_score = v_confidence,
            valid_from = coalesce(p_valid_from, valid_from),
            valid_until = coalesce(p_valid_until, valid_until),
            updated_at = now()
        WHERE stix_id = p_stix_id
        RETURNING relationship_id INTO v_rel_id;
    END IF;

    INSERT INTO intel.evidence_links (relationship_id, evidence_id, linked_by_agent)
    SELECT v_rel_id, e, 'ATENEA'
    FROM unnest(p_evidence_ids) AS e
    ON CONFLICT (relationship_id, evidence_id) DO NOTHING;

    IF p_confidence_override IS NULL THEN
        v_confidence := intel.recalculate_confidence(NULL, v_rel_id);
    END IF;

    SELECT m.mission_id INTO v_mission_id
    FROM staging.raw_intel_ledger l
    JOIN intel.pirs p ON p.pir_id = l.mission_pir_id
    JOIN intel.missions m ON m.mission_id = p.mission_id
    WHERE l.evidence_id = p_evidence_ids[1];

    PERFORM audit.log_event(
        'AGENT', session_user, 'RELATIONSHIP_UPSERTED', 'INTEL_RELATIONSHIP',
        v_rel_id::text, v_mission_id, NULL, 'SUCCESS',
        jsonb_build_object(
            'stix_id', v_stix_id,
            'relationship_type', p_relationship_type,
            'confidence', v_confidence,
            'admiralty_rating', p_admiralty_rating
        )
    );

    RETURN v_rel_id;
END;
$$;

-- ---------------------------------------------------------------------
-- 16. Revocación (nunca borrado)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.revoke_entity(p_entity_id uuid, p_reason text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_mission_id uuid;
BEGIN
    IF session_user NOT IN ('svc_atenea', 'svc_admin') THEN
        RAISE EXCEPTION 'Solo ATENEA/ADMIN puede revocar entidades' USING ERRCODE = '42501';
    END IF;
    IF p_reason IS NULL OR length(btrim(p_reason)) = 0 THEN
        RAISE EXCEPTION 'revoked_reason obligatorio' USING ERRCODE = '23514';
    END IF;

    UPDATE intel.intel_entities
    SET revoked = true, revoked_at = now(), revoked_reason = p_reason, updated_at = now()
    WHERE entity_id = p_entity_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Entidad inexistente: %', p_entity_id USING ERRCODE = '23503';
    END IF;

    UPDATE intel.intel_relationships
    SET revoked = true, revoked_at = now(),
        revoked_reason = 'Entidad revocada: ' || p_reason, updated_at = now()
    WHERE (source_entity_id = p_entity_id OR target_entity_id = p_entity_id)
      AND NOT revoked;

    SELECT m.mission_id INTO v_mission_id
    FROM staging.raw_intel_ledger l
    JOIN intel.pirs p ON p.pir_id = l.mission_pir_id
    JOIN intel.missions m ON m.mission_id = p.mission_id
    WHERE l.evidence_id = (
        SELECT evidence_id FROM intel.evidence_links
        WHERE entity_id = p_entity_id ORDER BY linked_at LIMIT 1
    );

    PERFORM audit.log_event(
        'AGENT', session_user, 'ENTITY_REVOKED', 'INTEL_ENTITY',
        p_entity_id::text, v_mission_id, NULL, 'SUCCESS',
        jsonb_build_object('reason', p_reason)
    );
END;
$$;

CREATE OR REPLACE FUNCTION intel.revoke_relationship(p_relationship_id uuid, p_reason text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
BEGIN
    IF session_user NOT IN ('svc_atenea', 'svc_admin') THEN
        RAISE EXCEPTION 'Solo ATENEA/ADMIN puede revocar relaciones' USING ERRCODE = '42501';
    END IF;
    IF p_reason IS NULL OR length(btrim(p_reason)) = 0 THEN
        RAISE EXCEPTION 'revoked_reason obligatorio' USING ERRCODE = '23514';
    END IF;

    UPDATE intel.intel_relationships
    SET revoked = true, revoked_at = now(), revoked_reason = p_reason, updated_at = now()
    WHERE relationship_id = p_relationship_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Relación inexistente: %', p_relationship_id USING ERRCODE = '23503';
    END IF;

    PERFORM audit.log_event(
        'AGENT', session_user, 'RELATIONSHIP_REVOKED', 'INTEL_RELATIONSHIP',
        p_relationship_id::text, NULL, NULL, 'SUCCESS',
        jsonb_build_object('reason', p_reason)
    );
END;
$$;

-- ---------------------------------------------------------------------
-- 17. Resolución de entidades (ALIAS_OF)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.resolve_candidate(
    p_candidate_id uuid,
    p_decision     text,
    p_reason       text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_cand    intel.entity_resolution_candidates%ROWTYPE;
    v_rel_id  uuid;
    v_exists  uuid;
BEGIN
    IF session_user NOT IN ('svc_atenea', 'svc_api', 'svc_admin') THEN
        RAISE EXCEPTION 'Rol no autorizado para resolver candidatos' USING ERRCODE = '42501';
    END IF;
    IF p_decision NOT IN ('ACCEPTED', 'REJECTED') THEN
        RAISE EXCEPTION 'decision inválida: %', p_decision USING ERRCODE = '22023';
    END IF;

    SELECT * INTO v_cand
    FROM intel.entity_resolution_candidates
    WHERE candidate_id = p_candidate_id
    FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Candidato inexistente: %', p_candidate_id USING ERRCODE = '23503';
    END IF;
    IF v_cand.status <> 'PENDING' THEN
        RAISE EXCEPTION 'Candidato ya resuelto: %', v_cand.status USING ERRCODE = '23514';
    END IF;

    IF p_decision = 'REJECTED' THEN
        UPDATE intel.entity_resolution_candidates
        SET status = 'REJECTED', reviewed_by = session_user,
            reviewed_at = now(), decision_reason = p_reason
        WHERE candidate_id = p_candidate_id;

        PERFORM audit.log_event('AGENT', session_user, 'CANDIDATE_REJECTED',
            'ENTITY_RESOLUTION', p_candidate_id::text, NULL, NULL, 'SUCCESS',
            jsonb_build_object('reason', p_reason));
        RETURN NULL;
    END IF;

    IF v_cand.evidence_ids IS NULL OR cardinality(v_cand.evidence_ids) < 1 THEN
        RAISE EXCEPTION 'Candidato sin evidencia: no se puede aceptar' USING ERRCODE = '23514';
    END IF;

    -- Reutiliza una relación ALIAS_OF activa si ya existe.
    SELECT relationship_id INTO v_exists
    FROM intel.intel_relationships
    WHERE relationship_type = 'ALIAS_OF'
      AND NOT revoked
      AND ((source_entity_id = v_cand.source_entity_id AND target_entity_id = v_cand.target_entity_id)
        OR (source_entity_id = v_cand.target_entity_id AND target_entity_id = v_cand.source_entity_id))
    LIMIT 1;

    IF v_exists IS NOT NULL THEN
        v_rel_id := v_exists;
    ELSE
        v_rel_id := staging.uuid_v7();
        INSERT INTO intel.intel_relationships (
            relationship_id, stix_id, relationship_type,
            source_entity_id, target_entity_id, properties,
            admiralty_rating, confidence_score, created_by_agent
        ) VALUES (
            v_rel_id, 'relationship--' || v_rel_id::text, 'ALIAS_OF',
            v_cand.source_entity_id, v_cand.target_entity_id,
            jsonb_build_object('resolution_score', v_cand.score,
                               'algorithm', v_cand.algorithm),
            v_cand.admiralty_rating,
            intel.admiralty_confidence(v_cand.admiralty_rating),
            'ATENEA'
        );
    END IF;

    INSERT INTO intel.evidence_links (relationship_id, evidence_id, linked_by_agent)
    SELECT v_rel_id, e, 'ATENEA'
    FROM unnest(v_cand.evidence_ids) AS e
    ON CONFLICT (relationship_id, evidence_id) DO NOTHING;

    UPDATE intel.entity_resolution_candidates
    SET status = 'ACCEPTED', reviewed_by = session_user,
        reviewed_at = now(), decision_reason = p_reason
    WHERE candidate_id = p_candidate_id;

    PERFORM audit.log_event('AGENT', session_user, 'CANDIDATE_ACCEPTED',
        'ENTITY_RESOLUTION', p_candidate_id::text, NULL, NULL, 'SUCCESS',
        jsonb_build_object('reason', p_reason, 'relationship_id', v_rel_id));

    RETURN v_rel_id;
END;
$$;

-- ---------------------------------------------------------------------
-- 17-bis. Creación de alertas y candidatos de fusión (solo ATENEA)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.create_coherence_alert(
    p_mission_id       uuid,
    p_entity_id        uuid,
    p_relationship_id  uuid,
    p_alert_type       text,
    p_severity         text,
    p_details          jsonb DEFAULT '{}'::jsonb,
    p_evidence_ids     uuid[] DEFAULT '{}'::uuid[]
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_alert_id uuid;
    v_missing  integer;
BEGIN
    IF session_user <> 'svc_atenea'
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Solo ATENEA puede crear alertas' USING ERRCODE = '42501';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM intel.missions WHERE mission_id = p_mission_id) THEN
        RAISE EXCEPTION 'mission_id inválido' USING ERRCODE = '23503';
    END IF;
    IF p_entity_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM intel.intel_entities WHERE entity_id = p_entity_id) THEN
        RAISE EXCEPTION 'entity_id inválido' USING ERRCODE = '23503';
    END IF;
    IF p_relationship_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM intel.intel_relationships WHERE relationship_id = p_relationship_id) THEN
        RAISE EXCEPTION 'relationship_id inválido' USING ERRCODE = '23503';
    END IF;

    IF p_evidence_ids IS NOT NULL AND cardinality(p_evidence_ids) > 0 THEN
        SELECT count(*) INTO v_missing
        FROM unnest(p_evidence_ids) AS e
        LEFT JOIN staging.raw_intel_ledger l ON l.evidence_id = e
        WHERE l.evidence_id IS NULL;
        IF v_missing > 0 THEN
            RAISE EXCEPTION 'Evidencia inexistente en la alerta' USING ERRCODE = '23503';
        END IF;
    END IF;

    INSERT INTO intel.coherence_alerts (
        mission_id, entity_id, relationship_id, alert_type, severity,
        details, evidence_ids
    ) VALUES (
        p_mission_id, p_entity_id, p_relationship_id, p_alert_type, p_severity,
        coalesce(p_details, '{}'::jsonb), coalesce(p_evidence_ids, '{}'::uuid[])
    ) RETURNING alert_id INTO v_alert_id;

    PERFORM audit.log_event(
        'AGENT', session_user, 'COHERENCE_ALERT_CREATED', 'COHERENCE_ALERT',
        v_alert_id::text, p_mission_id, NULL, 'SUCCESS',
        jsonb_build_object('alert_type', p_alert_type, 'severity', p_severity)
    );
    RETURN v_alert_id;
END;
$$;

CREATE OR REPLACE FUNCTION intel.create_resolution_candidate(
    p_source_entity_id uuid,
    p_target_entity_id uuid,
    p_score            numeric,
    p_algorithm        text,
    p_admiralty_rating text DEFAULT 'F6',
    p_evidence_ids     uuid[] DEFAULT '{}'::uuid[]
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_candidate_id uuid;
    v_missing      integer;
BEGIN
    IF session_user <> 'svc_atenea'
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Solo ATENEA puede crear candidatos de fusión' USING ERRCODE = '42501';
    END IF;
    IF p_source_entity_id = p_target_entity_id THEN
        RAISE EXCEPTION 'Una entidad no puede fusionarse consigo misma' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM intel.intel_entities WHERE entity_id = p_source_entity_id)
       OR NOT EXISTS (SELECT 1 FROM intel.intel_entities WHERE entity_id = p_target_entity_id) THEN
        RAISE EXCEPTION 'Entidad inexistente en el candidato' USING ERRCODE = '23503';
    END IF;
    IF p_score IS NULL OR p_score < 0 OR p_score > 1 THEN
        RAISE EXCEPTION 'score fuera de [0,1]' USING ERRCODE = '23514';
    END IF;
    IF p_admiralty_rating !~ '^[A-F][1-6]$' THEN
        RAISE EXCEPTION 'admiralty_rating inválido' USING ERRCODE = '23514';
    END IF;
    IF p_evidence_ids IS NULL OR cardinality(p_evidence_ids) < 1 THEN
        RAISE EXCEPTION 'Candidato sin evidencia' USING ERRCODE = '23514';
    END IF;
    SELECT count(*) INTO v_missing
    FROM unnest(p_evidence_ids) AS e
    LEFT JOIN staging.raw_intel_ledger l ON l.evidence_id = e
    WHERE l.evidence_id IS NULL;
    IF v_missing > 0 THEN
        RAISE EXCEPTION 'Evidencia inexistente en el candidato' USING ERRCODE = '23503';
    END IF;

    INSERT INTO intel.entity_resolution_candidates (
        source_entity_id, target_entity_id, score, algorithm,
        admiralty_rating, evidence_ids
    ) VALUES (
        p_source_entity_id, p_target_entity_id, p_score, p_algorithm,
        p_admiralty_rating, coalesce(p_evidence_ids, '{}'::uuid[])
    ) RETURNING candidate_id INTO v_candidate_id;

    PERFORM audit.log_event(
        'AGENT', session_user, 'RESOLUTION_CANDIDATE_CREATED', 'ENTITY_RESOLUTION',
        v_candidate_id::text, NULL, NULL, 'SUCCESS',
        jsonb_build_object('score', p_score, 'algorithm', p_algorithm)
    );
    RETURN v_candidate_id;
END;
$$;

-- ---------------------------------------------------------------------
-- 18. Cierre de misión con control de cobertura
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.close_mission(p_mission_id uuid, p_reason text)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, audit, public
AS $$
DECLARE
    v_open_pirs integer;
    v_critical  integer;
BEGIN
    IF session_user NOT IN ('svc_api', 'svc_director', 'svc_admin') THEN
        RAISE EXCEPTION 'Rol no autorizado para cerrar misión' USING ERRCODE = '42501';
    END IF;
    IF p_reason IS NULL OR length(btrim(p_reason)) = 0 THEN
        RAISE EXCEPTION 'reason obligatorio' USING ERRCODE = '23514';
    END IF;

    SELECT count(*) INTO v_open_pirs
    FROM intel.pirs
    WHERE mission_id = p_mission_id AND status IN ('OPEN', 'IN_PROGRESS');
    IF v_open_pirs > 0 THEN
        RAISE EXCEPTION 'No se puede cerrar: % PIR abiertos', v_open_pirs
            USING ERRCODE = '23514';
    END IF;

    SELECT count(*) INTO v_critical
    FROM intel.coherence_alerts
    WHERE mission_id = p_mission_id
      AND severity = 'CRITICAL'
      AND status = 'PENDING_ANALYST';
    IF v_critical > 0 THEN
        RAISE EXCEPTION 'No se puede cerrar: % alertas CRITICAL pendientes', v_critical
            USING ERRCODE = '23514';
    END IF;

    UPDATE intel.missions
    SET status = 'CLOSED', closed_at = now(), updated_at = now()
    WHERE mission_id = p_mission_id AND status = 'ACTIVE';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Misión no activa o inexistente: %', p_mission_id
            USING ERRCODE = '23514';
    END IF;

    PERFORM audit.log_event('HUMAN', session_user, 'MISSION_CLOSED', 'MISSION',
        p_mission_id::text, p_mission_id, NULL, 'SUCCESS',
        jsonb_build_object('reason', p_reason));
END;
$$;

-- ---------------------------------------------------------------------
-- 19. OPSEC HUMINT (rate-limit y aborto)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION humint.check_opsec(
    p_sockpuppet_id  uuid,
    p_interaction_id uuid,
    p_action         text
) RETURNS text
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, humint, audit, public
AS $$
DECLARE
    v_mission_id   uuid;
    v_policy       humint.opsec_policies%ROWTYPE;
    v_count_today  integer;
    v_count_target integer;
    v_target       text;
    v_decision     text := 'ALLOW';
BEGIN
    IF session_user <> 'svc_hermes' THEN
        RAISE EXCEPTION 'Solo HERMES puede consultar OPSEC' USING ERRCODE = '42501';
    END IF;

    SELECT mission_id, target_selector
      INTO v_mission_id, v_target
    FROM humint.interactions
    WHERE interaction_id = p_interaction_id;

    IF v_mission_id IS NULL THEN
        INSERT INTO humint.opsec_events (
            sockpuppet_id, interaction_id, severity, rule_code, description
        ) VALUES (
            p_sockpuppet_id, p_interaction_id, 'HIGH', 'NO_INTERACTION',
            'Interacción inexistente para ' || coalesce(p_action, 'acción')
        );
        RETURN 'ABORT';
    END IF;

    SELECT * INTO v_policy
    FROM humint.opsec_policies
    WHERE mission_id = v_mission_id AND active
    LIMIT 1;

    IF NOT FOUND THEN
        INSERT INTO humint.opsec_events (
            sockpuppet_id, interaction_id, severity, rule_code, description
        ) VALUES (
            p_sockpuppet_id, p_interaction_id, 'HIGH', 'NO_ACTIVE_POLICY',
            'No hay política OPSEC activa para la misión'
        );
        RETURN 'ABORT';
    END IF;

    SELECT count(*) INTO v_count_today
    FROM humint.interactions
    WHERE sockpuppet_id = p_sockpuppet_id
      AND started_at >= date_trunc('day', now());

    IF v_count_today >= v_policy.max_interactions_per_day THEN
        v_decision := 'ABORT';
    END IF;

    SELECT count(*) INTO v_count_target
    FROM humint.interactions
    WHERE sockpuppet_id = p_sockpuppet_id
      AND target_selector = v_target
      AND started_at >= now() - interval '24 hours';

    IF v_count_target >= v_policy.max_messages_per_target THEN
        v_decision := 'ABORT';
    END IF;

    IF v_decision <> 'ALLOW' THEN
        INSERT INTO humint.opsec_events (
            sockpuppet_id, interaction_id, policy_id, severity,
            rule_code, description
        ) VALUES (
            p_sockpuppet_id, p_interaction_id, v_policy.policy_id, 'HIGH',
            'RATE_LIMIT_EXCEEDED',
            'Límite OPSEC excedido para ' || coalesce(p_action, 'acción')
        );
    END IF;

    RETURN v_decision;
END;
$$;

-- ---------------------------------------------------------------------
-- 20. Búsqueda híbrida (BM25 + vector + RRF normalizado)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION api.search_hybrid(
    p_query      text,
    p_embedding  vector(1024) DEFAULT NULL,
    p_limit      integer DEFAULT 20,
    p_stix_types text[] DEFAULT NULL,
    p_as_of      timestamptz DEFAULT now()
) RETURNS TABLE (
    entity_id  uuid,
    stix_id    text,
    stix_type  text,
    name       text,
    score      double precision,
    confidence smallint,
    tlp        text,
    snippet    text
)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, staging, intel, public
AS $$
WITH q AS (
    SELECT websearch_to_tsquery('simple', coalesce(p_query, '')) AS tsq
),
base AS (
    SELECT e.entity_id, e.stix_id, e.stix_type, e.name, e.confidence,
           e.tlp_classification::text AS tlp, e.search_tsv, e.embedding,
           coalesce(e.properties->>'description', '') AS snippet
    FROM intel.intel_entities e
    WHERE NOT e.revoked
      AND e.valid_from <= p_as_of
      AND (e.valid_until IS NULL OR e.valid_until > p_as_of)
      AND (p_stix_types IS NULL OR e.stix_type = ANY (p_stix_types))
      AND intel.tlp_rank(e.tlp_classification::text)
          <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
),
bm25 AS (
    SELECT b.entity_id,
           row_number() OVER (ORDER BY ts_rank_cd(b.search_tsv, q.tsq) DESC, b.entity_id) AS rank
    FROM base b, q
    WHERE b.search_tsv @@ q.tsq
),
vec AS (
    SELECT b.entity_id,
           row_number() OVER (ORDER BY b.embedding <=> p_embedding, b.entity_id) AS rank
    FROM base b
    WHERE p_embedding IS NOT NULL AND b.embedding IS NOT NULL
),
rrf AS (
    SELECT entity_id, sum(1.0 / (60.0 + rank)) AS raw_score
    FROM (
        SELECT entity_id, rank FROM bm25
        UNION ALL
        SELECT entity_id, rank FROM vec
    ) u
    GROUP BY entity_id
),
norm AS (
    SELECT entity_id, raw_score / max(raw_score) OVER () AS score
    FROM rrf
)
SELECT b.entity_id, b.stix_id, b.stix_type, b.name,
       n.score::double precision, b.confidence, b.tlp, b.snippet
FROM norm n
JOIN base b USING (entity_id)
WHERE n.score >= 0.35
ORDER BY n.score DESC
LIMIT greatest(1, least(coalesce(p_limit, 20), 200));
$$;

COMMENT ON FUNCTION api.search_hybrid(text, vector, integer, text[], timestamptz) IS
    'Híbrido BM25 + coseno con RRF normalizado; abstención si score < 0.35.';

-- ---------------------------------------------------------------------
-- 21. Triggers de integridad
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION intel.fn_set_updated_at()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION staging.fn_ledger_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'raw_intel_ledger es append-only: % bloqueado', TG_OP
        USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_ledger_no_update_delete
    BEFORE UPDATE OR DELETE ON staging.raw_intel_ledger
    FOR EACH ROW EXECUTE FUNCTION staging.fn_ledger_immutable();

CREATE TRIGGER trg_ledger_no_truncate
    BEFORE TRUNCATE ON staging.raw_intel_ledger
    FOR EACH STATEMENT EXECUTE FUNCTION staging.fn_ledger_immutable();

CREATE OR REPLACE FUNCTION staging.fn_verify_ledger()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, staging, public
AS $$
DECLARE
    v_parsed jsonb;
BEGIN
    IF NEW.payload_sha256 IS NULL
       OR NEW.payload_sha256 !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'payload_sha256 inválido' USING ERRCODE = '23514';
    END IF;
    IF encode(digest(NEW.raw_payload_bytes, 'sha256'), 'hex') <> NEW.payload_sha256 THEN
        RAISE EXCEPTION 'Hash de evidencia no coincide con los bytes' USING ERRCODE = '23514';
    END IF;

    BEGIN
        v_parsed := convert_from(NEW.raw_payload_bytes, 'UTF8')::jsonb;
    EXCEPTION WHEN others THEN
        RAISE EXCEPTION 'raw_payload_bytes no es JSON UTF-8 válido' USING ERRCODE = '22023';
    END;
    IF v_parsed <> NEW.raw_payload THEN
        RAISE EXCEPTION 'raw_payload no corresponde a raw_payload_bytes' USING ERRCODE = '23514';
    END IF;

    IF jsonb_typeof(NEW.attachments) <> 'array' THEN
        RAISE EXCEPTION 'attachments debe ser un array JSON' USING ERRCODE = '22023';
    END IF;
    IF NEW.legal_basis IS NULL OR length(btrim(NEW.legal_basis)) = 0 THEN
        RAISE EXCEPTION 'legal_basis obligatorio' USING ERRCODE = '23514';
    END IF;
    IF NEW.captured_at_utc > now() + interval '5 minutes' THEN
        RAISE EXCEPTION 'captured_at_utc en el futuro' USING ERRCODE = '22007';
    END IF;

    NEW.ingested_at := now();
    NEW.hash_algorithm := 'SHA-256';
    NEW.canonicalization := 'RFC8785-JCS';
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_ledger_verify
    BEFORE INSERT ON staging.raw_intel_ledger
    FOR EACH ROW EXECUTE FUNCTION staging.fn_verify_ledger();

CREATE OR REPLACE FUNCTION staging.fn_ledger_chain()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, staging, public
AS $$
DECLARE
    v_prev text;
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('staging.raw_intel_ledger'));
    SELECT record_hash INTO v_prev
    FROM staging.raw_intel_ledger
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

CREATE TRIGGER trg_ledger_chain
    BEFORE INSERT ON staging.raw_intel_ledger
    FOR EACH ROW EXECUTE FUNCTION staging.fn_ledger_chain();

CREATE OR REPLACE FUNCTION staging.fn_attachments_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'raw_intel_attachments es append-only: % bloqueado', TG_OP
        USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_attachments_no_update_delete
    BEFORE UPDATE OR DELETE ON staging.raw_intel_attachments
    FOR EACH ROW EXECUTE FUNCTION staging.fn_attachments_immutable();

CREATE OR REPLACE FUNCTION audit.fn_events_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit.events es append-only: % bloqueado', TG_OP
        USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_audit_no_update_delete
    BEFORE UPDATE OR DELETE ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.fn_events_immutable();

CREATE TRIGGER trg_audit_no_truncate
    BEFORE TRUNCATE ON audit.events
    FOR EACH STATEMENT EXECUTE FUNCTION audit.fn_events_immutable();

CREATE OR REPLACE FUNCTION audit.fn_events_chain()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, audit, public
AS $$
DECLARE
    v_prev text;
BEGIN
    IF NEW.record_hash IS NOT NULL AND NEW.prev_hash IS NOT NULL THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('audit.events'));
    SELECT record_hash INTO v_prev
    FROM audit.events
    ORDER BY occurred_at DESC, event_id DESC
    LIMIT 1;
    NEW.prev_hash := v_prev;
    NEW.record_hash := encode(digest(
        coalesce(v_prev, '') || NEW.event_id::text || NEW.action ||
        coalesce(NEW.object_id, '') || coalesce(NEW.details::text, '{}'),
        'sha256'), 'hex');
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_audit_chain
    BEFORE INSERT ON audit.events
    FOR EACH ROW EXECUTE FUNCTION audit.fn_events_chain();

CREATE OR REPLACE FUNCTION intel.fn_no_delete()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'La inteligencia validada no se borra: use revoke (tabla %)', TG_TABLE_NAME
        USING ERRCODE = '42501';
END;
$$;

CREATE TRIGGER trg_entities_no_delete
    BEFORE DELETE ON intel.intel_entities
    FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE TRIGGER trg_relationships_no_delete
    BEFORE DELETE ON intel.intel_relationships
    FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE TRIGGER trg_evidence_links_no_delete
    BEFORE DELETE ON intel.evidence_links
    FOR EACH ROW EXECUTE FUNCTION intel.fn_no_delete();

CREATE OR REPLACE FUNCTION intel.fn_validate_stix_id()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.stix_id !~ '^[a-z0-9-]+--[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' THEN
        RAISE EXCEPTION 'stix_id inválido: %', NEW.stix_id USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_entities_stix_id
    BEFORE INSERT OR UPDATE OF stix_id ON intel.intel_entities
    FOR EACH ROW EXECUTE FUNCTION intel.fn_validate_stix_id();

CREATE TRIGGER trg_relationships_stix_id
    BEFORE INSERT OR UPDATE OF stix_id ON intel.intel_relationships
    FOR EACH ROW EXECUTE FUNCTION intel.fn_validate_stix_id();

CREATE OR REPLACE FUNCTION intel.fn_validate_relationship_types()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_src_types text[];
    v_tgt_types text[];
    v_src_type  text;
    v_tgt_type  text;
BEGIN
    SELECT allowed_source_types, allowed_target_types
      INTO v_src_types, v_tgt_types
    FROM intel.relationship_types
    WHERE relationship_type = NEW.relationship_type;

    SELECT stix_type INTO v_src_type FROM intel.intel_entities WHERE entity_id = NEW.source_entity_id;
    SELECT stix_type INTO v_tgt_type FROM intel.intel_entities WHERE entity_id = NEW.target_entity_id;

    IF v_src_types IS NOT NULL AND NOT (v_src_type = ANY (v_src_types)) THEN
        RAISE EXCEPTION 'Tipo origen % no permitido para %', v_src_type, NEW.relationship_type
            USING ERRCODE = '23514';
    END IF;
    IF v_tgt_types IS NOT NULL AND NOT (v_tgt_type = ANY (v_tgt_types)) THEN
        RAISE EXCEPTION 'Tipo destino % no permitido para %', v_tgt_type, NEW.relationship_type
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_relationships_types
    BEFORE INSERT OR UPDATE OF relationship_type, source_entity_id, target_entity_id
    ON intel.intel_relationships
    FOR EACH ROW EXECUTE FUNCTION intel.fn_validate_relationship_types();

CREATE OR REPLACE FUNCTION intel.fn_relationship_no_overlap()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.revoked THEN
        RETURN NEW;
    END IF;
    IF EXISTS (
        SELECT 1
        FROM intel.intel_relationships r
        WHERE r.relationship_id <> NEW.relationship_id
          AND r.source_entity_id = NEW.source_entity_id
          AND r.target_entity_id = NEW.target_entity_id
          AND r.relationship_type = NEW.relationship_type
          AND NOT r.revoked
          AND tstzrange(r.valid_from, r.valid_until, '[)')
              && tstzrange(NEW.valid_from, NEW.valid_until, '[)')
    ) THEN
        RAISE EXCEPTION 'Solapamiento temporal de relación' USING ERRCODE = '23P01';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_relationships_no_overlap
    BEFORE INSERT OR UPDATE OF valid_from, valid_until, revoked
    ON intel.intel_relationships
    FOR EACH ROW EXECUTE FUNCTION intel.fn_relationship_no_overlap();

CREATE OR REPLACE FUNCTION intel.fn_tlp_no_downgrade()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF intel.tlp_rank(NEW.tlp_classification::text)
       < intel.tlp_rank(OLD.tlp_classification::text)
       AND coalesce(current_setting('osint.break_glass', true), 'false') <> 'true' THEN
        RAISE EXCEPTION 'Degradación de TLP no autorizada' USING ERRCODE = '42501';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_entities_tlp
    BEFORE UPDATE OF tlp_classification ON intel.intel_entities
    FOR EACH ROW EXECUTE FUNCTION intel.fn_tlp_no_downgrade();

-- Las relaciones heredan TLP de las entidades; el trigger se aplica a reportes.
CREATE TRIGGER trg_reports_tlp
    BEFORE UPDATE OF tlp_classification ON intel.intelligence_reports
    FOR EACH ROW EXECUTE FUNCTION intel.fn_tlp_no_downgrade();

CREATE OR REPLACE FUNCTION intel.fn_revoke_reason()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.revoked AND NOT OLD.revoked
       AND (NEW.revoked_reason IS NULL OR length(btrim(NEW.revoked_reason)) = 0) THEN
        RAISE EXCEPTION 'revoked_reason obligatorio al revocar' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_entities_revoke_reason
    BEFORE UPDATE OF revoked ON intel.intel_entities
    FOR EACH ROW EXECUTE FUNCTION intel.fn_revoke_reason();

CREATE TRIGGER trg_relationships_revoke_reason
    BEFORE UPDATE OF revoked ON intel.intel_relationships
    FOR EACH ROW EXECUTE FUNCTION intel.fn_revoke_reason();

CREATE OR REPLACE FUNCTION intel.fn_update_search_tsv()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.search_tsv := to_tsvector(
        'simple',
        coalesce(NEW.name, '') || ' ' ||
        coalesce(array_to_string(NEW.aliases, ' '), '') || ' ' ||
        coalesce(NEW.properties->>'description', '')
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_entities_search_tsv
    BEFORE INSERT OR UPDATE OF name, aliases, properties ON intel.intel_entities
    FOR EACH ROW EXECUTE FUNCTION intel.fn_update_search_tsv();

CREATE OR REPLACE FUNCTION intel.fn_tasking_mission_active()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_status text;
BEGIN
    SELECT status INTO v_status FROM intel.missions WHERE mission_id = NEW.mission_id;
    IF v_status IS NULL THEN
        RAISE EXCEPTION 'Misión inexistente: %', NEW.mission_id USING ERRCODE = '23503';
    END IF;
    IF v_status <> 'ACTIVE' THEN
        RAISE EXCEPTION 'No se puede crear tasking en misión %', v_status
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_tasking_mission_active
    BEFORE INSERT ON intel.tasking_orders
    FOR EACH ROW EXECUTE FUNCTION intel.fn_tasking_mission_active();

CREATE OR REPLACE FUNCTION intel.fn_dissemination_tlp()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    v_report_tlp staging.tlp_enum;
BEGIN
    SELECT tlp_classification INTO v_report_tlp
    FROM intel.intelligence_reports
    WHERE report_id = NEW.report_id;
    IF v_report_tlp IS NULL THEN
        RAISE EXCEPTION 'Informe inexistente: %', NEW.report_id USING ERRCODE = '23503';
    END IF;
    -- La difusión no puede ser MENOS restrictiva que el informe.
    IF intel.tlp_rank(NEW.tlp_classification::text) < intel.tlp_rank(v_report_tlp::text) THEN
        RAISE EXCEPTION 'Difusión con TLP menos restrictivo que el informe'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_dissemination_tlp
    BEFORE INSERT OR UPDATE ON intel.dissemination
    FOR EACH ROW EXECUTE FUNCTION intel.fn_dissemination_tlp();

CREATE OR REPLACE FUNCTION intel.fn_report_release_guard()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF OLD.status = 'RELEASED' AND NEW.status <> 'REVOKED' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Informe difundido: solo se permite revocar' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_report_release_guard
    BEFORE UPDATE ON intel.intelligence_reports
    FOR EACH ROW EXECUTE FUNCTION intel.fn_report_release_guard();

-- updated_at
CREATE TRIGGER trg_missions_updated_at BEFORE UPDATE ON intel.missions
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();
CREATE TRIGGER trg_pirs_updated_at BEFORE UPDATE ON intel.pirs
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();
CREATE TRIGGER trg_eeis_updated_at BEFORE UPDATE ON intel.eeis
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();
CREATE TRIGGER trg_agent_registry_updated_at BEFORE UPDATE ON intel.agent_registry
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();
CREATE TRIGGER trg_entities_updated_at BEFORE UPDATE ON intel.intel_entities
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();
CREATE TRIGGER trg_relationships_updated_at BEFORE UPDATE ON intel.intel_relationships
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();
CREATE TRIGGER trg_reports_updated_at BEFORE UPDATE ON intel.intelligence_reports
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();
CREATE TRIGGER trg_opsec_policies_updated_at BEFORE UPDATE ON humint.opsec_policies
    FOR EACH ROW EXECUTE FUNCTION intel.fn_set_updated_at();

-- ---------------------------------------------------------------------
-- 22. Vistas de contrato y de API
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW api.v_graph_nodes AS
SELECT
    e.entity_id AS id,
    e.stix_type AS label,
    e.name,
    e.aliases,
    e.properties,
    e.confidence,
    e.admiralty_rating,
    e.tlp_classification::text AS tlp,
    e.valid_from,
    e.valid_until,
    e.revoked
FROM intel.intel_entities e
WHERE NOT e.revoked
  AND e.valid_from <= now()
  AND (e.valid_until IS NULL OR e.valid_until > now())
  AND intel.tlp_rank(e.tlp_classification::text)
      <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'));

CREATE OR REPLACE VIEW api.v_graph_edges AS
SELECT
    r.relationship_id AS id,
    r.source_entity_id AS source,
    r.target_entity_id AS target,
    r.relationship_type AS type,
    r.stix_relationship_type,
    r.confidence_score AS confidence,
    r.admiralty_rating,
    r.properties,
    r.valid_from,
    r.valid_until,
    r.revoked,
    coalesce(array_agg(el.evidence_id) FILTER (WHERE el.evidence_id IS NOT NULL), '{}'::uuid[]) AS supporting_evidence_ids
FROM intel.intel_relationships r
JOIN intel.intel_entities se ON se.entity_id = r.source_entity_id
JOIN intel.intel_entities te ON te.entity_id = r.target_entity_id
LEFT JOIN intel.evidence_links el ON el.relationship_id = r.relationship_id
WHERE NOT r.revoked
  AND NOT se.revoked
  AND NOT te.revoked
  AND r.valid_from <= now()
  AND (r.valid_until IS NULL OR r.valid_until > now())
  AND intel.tlp_rank(
        CASE
          WHEN intel.tlp_rank(se.tlp_classification::text) >= intel.tlp_rank(te.tlp_classification::text)
            THEN se.tlp_classification
          ELSE te.tlp_classification
        END::text)
      <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
GROUP BY r.relationship_id, se.tlp_classification, te.tlp_classification;

CREATE OR REPLACE VIEW api.v_mission_coverage AS
SELECT
    m.mission_id,
    m.code,
    m.name,
    m.status,
    (SELECT count(*) FROM intel.pirs p WHERE p.mission_id = m.mission_id) AS pir_count,
    (SELECT count(*) FROM intel.pirs p WHERE p.mission_id = m.mission_id AND p.status = 'ANSWERED') AS pir_answered,
    (SELECT count(*) FROM intel.eeis e JOIN intel.pirs p ON p.pir_id = e.pir_id WHERE p.mission_id = m.mission_id) AS eei_count,
    (SELECT count(*) FROM intel.tasking_orders t WHERE t.mission_id = m.mission_id) AS task_count,
    (SELECT count(*) FROM intel.tasking_orders t WHERE t.mission_id = m.mission_id AND t.status = 'COMPLETED') AS task_completed,
    (SELECT count(*) FROM staging.raw_intel_ledger l JOIN intel.pirs p ON p.pir_id = l.mission_pir_id WHERE p.mission_id = m.mission_id) AS evidence_count,
    (SELECT count(*) FROM intel.evidence_links el
       JOIN staging.raw_intel_ledger l ON l.evidence_id = el.evidence_id
       JOIN intel.pirs p ON p.pir_id = l.mission_pir_id
      WHERE p.mission_id = m.mission_id) AS evidence_links,
    (SELECT count(*) FROM intel.coherence_alerts a WHERE a.mission_id = m.mission_id AND a.status = 'PENDING_ANALYST') AS open_alerts
FROM intel.missions m;

CREATE OR REPLACE VIEW api.v_orphan_check AS
SELECT 'ENTITY' AS kind, e.entity_id::text AS id
FROM intel.intel_entities e
WHERE NOT EXISTS (SELECT 1 FROM intel.evidence_links el WHERE el.entity_id = e.entity_id)
UNION ALL
SELECT 'RELATIONSHIP', r.relationship_id::text
FROM intel.intel_relationships r
WHERE NOT EXISTS (SELECT 1 FROM intel.evidence_links el WHERE el.relationship_id = r.relationship_id);

CREATE OR REPLACE VIEW api.v_evidence_chain AS
SELECT
    l.evidence_id,
    l.mission_pir_id,
    l.collector_agent,
    l.payload_sha256,
    staging.verify_evidence(l.evidence_id) AS hash_ok,
    l.captured_at_utc,
    l.ingested_at,
    l.tlp_classification::text AS tlp,
    l.legal_hold,
    l.retention_until,
    l.prev_hash,
    l.record_hash,
    (SELECT count(*) FROM staging.raw_intel_attachments a WHERE a.evidence_id = l.evidence_id) AS attachment_count,
    (SELECT count(*) FROM intel.evidence_links el WHERE el.evidence_id = l.evidence_id) AS link_count
FROM staging.raw_intel_ledger l
WHERE intel.tlp_rank(l.tlp_classification::text)
      <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'));

CREATE OR REPLACE VIEW api.v_intelligence_reports AS
SELECT
    r.report_id,
    r.mission_id,
    r.title,
    r.summary,
    r.tlp_classification::text AS tlp,
    r.status,
    r.stix_bundle,
    r.created_by,
    r.created_at,
    r.approved_by,
    r.approved_at,
    r.released_at,
    r.revoked
FROM intel.intelligence_reports r
WHERE intel.tlp_rank(r.tlp_classification::text)
      <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'));

CREATE OR REPLACE VIEW intel.v_intel_entities_stix AS
SELECT
    e.entity_id,
    jsonb_build_object(
        'type', e.stix_type,
        'spec_version', '2.1',
        'id', e.stix_id,
        'created', to_char(e.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'modified', to_char(e.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'confidence', e.confidence,
        'lang', 'es',
        'object_marking_refs', jsonb_build_array(intel.tlp_marking_id(e.tlp_classification)),
        'revoked', e.revoked,
        'name', e.name,
        'aliases', to_jsonb(e.aliases),
        'x_admiralty_rating', e.admiralty_rating,
        'x_supporting_evidence_ids', coalesce(
            (SELECT jsonb_agg(el.evidence_id ORDER BY el.linked_at)
             FROM intel.evidence_links el WHERE el.entity_id = e.entity_id),
            '[]'::jsonb),
        'x_valid_from', e.valid_from,
        'x_valid_until', e.valid_until
    ) AS stix
FROM intel.intel_entities e;

CREATE OR REPLACE VIEW intel.v_intel_relationships_stix AS
SELECT
    r.relationship_id,
    jsonb_build_object(
        'type', 'relationship',
        'spec_version', '2.1',
        'id', r.stix_id,
        'created', to_char(r.created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'modified', to_char(r.updated_at AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"'),
        'relationship_type', r.stix_relationship_type,
        'source_ref', se.stix_id,
        'target_ref', te.stix_id,
        'confidence', r.confidence_score,
        'object_marking_refs', jsonb_build_array(
            intel.tlp_marking_id(
                CASE
                  WHEN intel.tlp_rank(se.tlp_classification::text) >= intel.tlp_rank(te.tlp_classification::text)
                    THEN se.tlp_classification
                  ELSE te.tlp_classification
                END
            )
        ),
        'revoked', r.revoked,
        'x_admiralty_rating', r.admiralty_rating,
        'x_supporting_evidence_ids', coalesce(
            (SELECT jsonb_agg(el.evidence_id ORDER BY el.linked_at)
             FROM intel.evidence_links el WHERE el.relationship_id = r.relationship_id),
            '[]'::jsonb),
        'x_valid_from', r.valid_from,
        'x_valid_until', r.valid_until
    ) AS stix
FROM intel.intel_relationships r
JOIN intel.intel_entities se ON se.entity_id = r.source_entity_id
JOIN intel.intel_entities te ON te.entity_id = r.target_entity_id;

-- ---------------------------------------------------------------------
-- 23. Roles, ownership y privilegios
-- ---------------------------------------------------------------------
CREATE ROLE osint_graph_owner NOLOGIN;
CREATE ROLE osint_director    NOLOGIN;
CREATE ROLE osint_hermes      NOLOGIN;
CREATE ROLE osint_aracne      NOLOGIN;
CREATE ROLE osint_argos       NOLOGIN;
CREATE ROLE osint_atenea      NOLOGIN;
CREATE ROLE osint_api         NOLOGIN;
CREATE ROLE osint_api_ro      NOLOGIN;
CREATE ROLE osint_auditor     NOLOGIN;
CREATE ROLE osint_admin       NOLOGIN;

CREATE ROLE svc_director LOGIN PASSWORD NULL IN ROLE osint_director;
CREATE ROLE svc_hermes   LOGIN PASSWORD NULL IN ROLE osint_hermes;
CREATE ROLE svc_aracne   LOGIN PASSWORD NULL IN ROLE osint_aracne;
CREATE ROLE svc_argos    LOGIN PASSWORD NULL IN ROLE osint_argos;
CREATE ROLE svc_atenea   LOGIN PASSWORD NULL IN ROLE osint_atenea;
CREATE ROLE svc_api      LOGIN PASSWORD NULL IN ROLE osint_api;
CREATE ROLE svc_api_ro   LOGIN PASSWORD NULL IN ROLE osint_api_ro;
CREATE ROLE svc_auditor  LOGIN PASSWORD NULL IN ROLE osint_auditor;
CREATE ROLE svc_admin    LOGIN PASSWORD NULL IN ROLE osint_admin;

ALTER SCHEMA staging OWNER TO osint_graph_owner;
ALTER SCHEMA intel   OWNER TO osint_graph_owner;
ALTER SCHEMA humint  OWNER TO osint_graph_owner;
ALTER SCHEMA audit   OWNER TO osint_graph_owner;
ALTER SCHEMA api     OWNER TO osint_graph_owner;

DO $$
DECLARE
    r record;
BEGIN
    FOR r IN
        SELECT schemaname, tablename
        FROM pg_tables
        WHERE schemaname IN ('staging','intel','humint','audit','api')
    LOOP
        EXECUTE format('ALTER TABLE %I.%I OWNER TO osint_graph_owner', r.schemaname, r.tablename);
    END LOOP;

    FOR r IN
        SELECT schemaname, viewname
        FROM pg_views
        WHERE schemaname IN ('staging','intel','humint','audit','api')
    LOOP
        EXECUTE format('ALTER VIEW %I.%I OWNER TO osint_graph_owner', r.schemaname, r.viewname);
    END LOOP;

    FOR r IN
        SELECT n.nspname, p.proname, pg_get_function_identity_arguments(p.oid) AS args
        FROM pg_proc p
        JOIN pg_namespace n ON n.oid = p.pronamespace
        WHERE n.nspname IN ('staging','intel','humint','audit','api')
    LOOP
        EXECUTE format('ALTER FUNCTION %I.%I(%s) OWNER TO osint_graph_owner',
                       r.nspname, r.proname, r.args);
    END LOOP;
END;
$$;

REVOKE ALL ON SCHEMA staging, intel, humint, audit, api FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA staging, intel, humint, audit, api FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA staging, intel, humint, audit, api FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA staging, intel, humint, audit, api FROM PUBLIC;

GRANT USAGE ON SCHEMA staging, intel, humint, audit, api TO
    osint_director, osint_hermes, osint_aracne, osint_argos,
    osint_atenea, osint_api, osint_api_ro, osint_auditor, osint_admin;

-- DIRECTOR: control de misión y política OPSEC
GRANT SELECT, INSERT, UPDATE ON intel.missions, intel.pirs, intel.eeis, intel.tasking_orders
    TO osint_director;
GRANT SELECT, INSERT, UPDATE ON humint.opsec_policies TO osint_director;
GRANT SELECT ON humint.opsec_events TO osint_director;
GRANT SELECT ON intel.intelligence_reports, intel.dissemination, intel.agent_registry,
    intel.coherence_alerts, intel.intel_entities, intel.intel_relationships
    TO osint_director;

-- API/ADMIN: operaciones humanas sobre OPSEC; la API aplica RBAC.
GRANT SELECT, INSERT, UPDATE ON humint.opsec_policies TO osint_api, osint_admin;
GRANT SELECT, UPDATE ON humint.opsec_events TO osint_api, osint_admin;
GRANT SELECT ON api.v_mission_coverage TO osint_director;

-- Colectores: lectura de misión/tasking asignado y ejecución de append_evidence
GRANT SELECT ON intel.missions, intel.pirs, intel.eeis, intel.tasking_orders
    TO osint_hermes, osint_aracne, osint_argos;
GRANT EXECUTE ON FUNCTION staging.append_evidence(text, uuid, text, text, jsonb, bytea, text, timestamptz, staging.tlp_enum, text, text, numeric, jsonb, jsonb)
    TO osint_hermes, osint_aracne, osint_argos;
GRANT EXECUTE ON FUNCTION staging.current_collector_agent()
    TO osint_hermes, osint_aracne, osint_argos;

-- HERMES: compartimento HUMINT (lectura total; escritura salvo políticas)
GRANT SELECT ON humint.sockpuppets, humint.legends, humint.interactions,
    humint.deception_assessments, humint.psych_profiles, humint.opsec_policies,
    humint.opsec_events TO osint_hermes;
GRANT INSERT, UPDATE ON humint.sockpuppets, humint.legends, humint.interactions,
    humint.deception_assessments, humint.psych_profiles, humint.opsec_events
    TO osint_hermes;
GRANT EXECUTE ON FUNCTION humint.check_opsec(uuid, uuid, text) TO osint_hermes;
GRANT SELECT ON staging.raw_intel_ledger TO osint_hermes;

-- ATENEA: grafo validado
GRANT SELECT ON staging.raw_intel_ledger, staging.raw_intel_attachments TO osint_atenea;
GRANT SELECT ON intel.intel_entities, intel.intel_relationships,
    intel.stix_types, intel.relationship_types, intel.entity_resolution_candidates,
    intel.entity_aliases, intel.evidence_links, intel.coherence_alerts,
    intel.intelligence_reports, intel.dissemination
    TO osint_atenea;
GRANT SELECT ON intel.missions, intel.pirs, intel.eeis, intel.tasking_orders
    TO osint_atenea;
-- Excepción controlada: el informe no es una aserción del grafo; la aprobación
-- y difusión siguen siendo humanas. Se audita vía API.
GRANT INSERT, UPDATE ON intel.intelligence_reports TO osint_atenea;
GRANT EXECUTE ON FUNCTION intel.upsert_entity(text, text, text, staging.tlp_enum, uuid[], text[], jsonb, timestamptz, timestamptz, smallint, text)
    TO osint_atenea;
GRANT EXECUTE ON FUNCTION intel.upsert_relationship(text, uuid, uuid, text, uuid[], jsonb, timestamptz, timestamptz, smallint, text)
    TO osint_atenea;
GRANT EXECUTE ON FUNCTION intel.revoke_entity(uuid, text), intel.revoke_relationship(uuid, text)
    TO osint_atenea, osint_admin;
GRANT EXECUTE ON FUNCTION intel.resolve_candidate(uuid, text, text)
    TO osint_atenea, osint_api;
GRANT EXECUTE ON FUNCTION intel.create_coherence_alert(uuid, uuid, uuid, text, text, jsonb, uuid[]),
                          intel.create_resolution_candidate(uuid, uuid, numeric, text, text, uuid[]),
                          intel.recalculate_confidence(uuid, uuid)
    TO osint_atenea;
GRANT EXECUTE ON FUNCTION intel.admiralty_confidence(text), intel.tlp_rank(text),
    intel.tlp_marking_id(staging.tlp_enum), intel.relationship_tlp(uuid)
    TO osint_director, osint_atenea, osint_api, osint_api_ro, osint_auditor;

-- API: lecturas, control humano y aprobación de difusión
GRANT SELECT ON api.v_graph_nodes, api.v_graph_edges, api.v_mission_coverage,
    api.v_orphan_check, api.v_evidence_chain, api.v_intelligence_reports
    TO osint_api, osint_api_ro;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA api TO osint_api, osint_api_ro;
GRANT SELECT ON intel.agent_registry, intel.stix_types, intel.relationship_types,
    intel.intel_entities, intel.intel_relationships,
    intel.coherence_alerts, intel.entity_resolution_candidates,
    intel.intelligence_reports, intel.dissemination,
    intel.missions, intel.pirs, intel.eeis, intel.tasking_orders TO osint_api;
GRANT SELECT ON staging.raw_intel_ledger, staging.raw_intel_attachments TO osint_api;
GRANT INSERT, UPDATE ON intel.missions, intel.pirs, intel.eeis, intel.tasking_orders,
    intel.coherence_alerts, intel.entity_resolution_candidates,
    intel.intelligence_reports TO osint_api;
GRANT INSERT ON intel.dissemination TO osint_api;
GRANT SELECT ON audit.events, audit.agent_runs TO osint_api;
GRANT EXECUTE ON FUNCTION intel.close_mission(uuid, text) TO osint_api, osint_director;
GRANT EXECUTE ON FUNCTION intel.resolve_candidate(uuid, text, text) TO osint_api;

-- AUDITOR: metadatos, sin payload crudo
GRANT SELECT (evidence_id, mission_pir_id, collector_agent, payload_sha256,
              captured_at_utc, ingested_at, tlp_classification, legal_hold,
              retention_until, prev_hash, record_hash)
    ON staging.raw_intel_ledger TO osint_auditor;
GRANT SELECT ON audit.events, audit.agent_runs TO osint_auditor;
GRANT SELECT ON api.v_evidence_chain, api.v_orphan_check, api.v_mission_coverage TO osint_auditor;

-- ADMIN: break-glass explícito, sin acceso rutinario
GRANT EXECUTE ON FUNCTION staging.verify_evidence(uuid) TO osint_admin, osint_api;
GRANT SELECT ON audit.events, audit.agent_runs TO osint_admin;

-- ---------------------------------------------------------------------
-- 24. Row-Level Security
-- ---------------------------------------------------------------------
ALTER TABLE intel.missions ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.pirs ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.eeis ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.tasking_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE staging.raw_intel_ledger ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.intel_entities ENABLE ROW LEVEL SECURITY;
ALTER TABLE intel.intel_relationships ENABLE ROW LEVEL SECURITY;
ALTER TABLE humint.sockpuppets ENABLE ROW LEVEL SECURITY;
ALTER TABLE humint.interactions ENABLE ROW LEVEL SECURITY;

-- DIRECTOR: todo
CREATE POLICY missions_director_all ON intel.missions
    FOR ALL TO osint_director USING (true) WITH CHECK (true);
CREATE POLICY pirs_director_all ON intel.pirs
    FOR ALL TO osint_director USING (true) WITH CHECK (true);
CREATE POLICY eeis_director_all ON intel.eeis
    FOR ALL TO osint_director USING (true) WITH CHECK (true);
CREATE POLICY tasking_director_all ON intel.tasking_orders
    FOR ALL TO osint_director USING (true) WITH CHECK (true);

-- API: actúa en nombre de analistas/directores; el RBAC de la API ya validó el rol.
CREATE POLICY missions_api_all ON intel.missions
    FOR ALL TO osint_api USING (true) WITH CHECK (true);
CREATE POLICY pirs_api_all ON intel.pirs
    FOR ALL TO osint_api USING (true) WITH CHECK (true);
CREATE POLICY eeis_api_all ON intel.eeis
    FOR ALL TO osint_api USING (true) WITH CHECK (true);
CREATE POLICY tasking_api_all ON intel.tasking_orders
    FOR ALL TO osint_api USING (true) WITH CHECK (true);

-- ATENEA: lectura de misión/tasking para contextualizar la fusión.
CREATE POLICY missions_atenea_read ON intel.missions
    FOR SELECT TO osint_atenea USING (true);
CREATE POLICY pirs_atenea_read ON intel.pirs
    FOR SELECT TO osint_atenea USING (true);
CREATE POLICY eeis_atenea_read ON intel.eeis
    FOR SELECT TO osint_atenea USING (true);
CREATE POLICY tasking_atenea_read ON intel.tasking_orders
    FOR SELECT TO osint_atenea USING (true);

-- Colectores: solo misiones/tareas asignadas (el API setea osint.agent)
CREATE POLICY missions_collector_read ON intel.missions
    FOR SELECT TO osint_hermes, osint_aracne, osint_argos
    USING (mission_id IN (
        SELECT t.mission_id FROM intel.tasking_orders t
        WHERE t.assignee_agent::text = current_setting('osint.agent', true)
    ));
CREATE POLICY pirs_collector_read ON intel.pirs
    FOR SELECT TO osint_hermes, osint_aracne, osint_argos
    USING (mission_id IN (
        SELECT t.mission_id FROM intel.tasking_orders t
        WHERE t.assignee_agent::text = current_setting('osint.agent', true)
    ));
CREATE POLICY eeis_collector_read ON intel.eeis
    FOR SELECT TO osint_hermes, osint_aracne, osint_argos
    USING (pir_id IN (
        SELECT p.pir_id FROM intel.pirs p
        JOIN intel.tasking_orders t ON t.pir_id = p.pir_id
        WHERE t.assignee_agent::text = current_setting('osint.agent', true)
    ));
CREATE POLICY tasking_collector_read ON intel.tasking_orders
    FOR SELECT TO osint_hermes, osint_aracne, osint_argos
    USING (assignee_agent::text = current_setting('osint.agent', true));

-- Ledger: cada colector solo ve lo suyo; ATENEA/auditor ven todo
CREATE POLICY ledger_collector_own ON staging.raw_intel_ledger
    FOR SELECT TO osint_hermes, osint_aracne, osint_argos
    USING (collector_agent = staging.current_collector_agent());
CREATE POLICY ledger_atenea_all ON staging.raw_intel_ledger
    FOR SELECT TO osint_atenea, osint_auditor USING (true);
CREATE POLICY ledger_api_read ON staging.raw_intel_ledger
    FOR SELECT TO osint_api
    USING (intel.tlp_rank(tlp_classification::text)
           <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR')));

ALTER TABLE staging.raw_intel_attachments ENABLE ROW LEVEL SECURITY;
CREATE POLICY attachments_atenea_read ON staging.raw_intel_attachments
    FOR SELECT TO osint_atenea USING (true);
CREATE POLICY attachments_api_read ON staging.raw_intel_attachments
    FOR SELECT TO osint_api
    USING (EXISTS (
        SELECT 1 FROM staging.raw_intel_ledger l
        WHERE l.evidence_id = evidence_id
          AND intel.tlp_rank(l.tlp_classification::text)
              <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
    ));

-- Grafo: TLP para API; ATENEA todo
CREATE POLICY entities_tlp_read ON intel.intel_entities
    FOR SELECT TO osint_api, osint_api_ro, osint_auditor
    USING (intel.tlp_rank(tlp_classification::text)
           <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR')));
CREATE POLICY entities_atenea_all ON intel.intel_entities
    FOR ALL TO osint_atenea USING (true) WITH CHECK (true);
CREATE POLICY entities_director_read ON intel.intel_entities
    FOR SELECT TO osint_director USING (true);
CREATE POLICY relationships_tlp_read ON intel.intel_relationships
    FOR SELECT TO osint_api, osint_api_ro, osint_auditor
    USING (intel.tlp_rank(intel.relationship_tlp(relationship_id)::text)
           <= intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR')));
CREATE POLICY relationships_atenea_all ON intel.intel_relationships
    FOR ALL TO osint_atenea USING (true) WITH CHECK (true);
CREATE POLICY relationships_director_read ON intel.intel_relationships
    FOR SELECT TO osint_director USING (true);

-- HUMINT: solo Hermes (y auditor de metadatos vía grants limitados)
CREATE POLICY sockpuppets_hermes_all ON humint.sockpuppets
    FOR ALL TO osint_hermes USING (true) WITH CHECK (true);
CREATE POLICY interactions_hermes_all ON humint.interactions
    FOR ALL TO osint_hermes USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------
-- 25. Semillas de agentes (PAUSED hasta configurar keys reales)
-- ---------------------------------------------------------------------
INSERT INTO intel.agent_registry
    (agent_type, name, version, status, capabilities, api_key_hash, db_role)
VALUES
    ('DIRECTOR_CELL', 'DIRECTOR_CELL', '3.0.0', 'PAUSED', '["missions","pirs","eeis","tasking","opsec"]', 'REPLACE_ME_DIRECTOR', 'svc_director'),
    ('HERMES',        'HERMES',        '3.0.0', 'PAUSED', '["humint","socmint","sockpuppets","elicitation"]', 'REPLACE_ME_HERMES', 'svc_hermes'),
    ('ARACNE',        'ARACNE',        '3.0.0', 'PAUSED', '["socmint","sna","bots","pattern_of_life"]', 'REPLACE_ME_ARACNE', 'svc_aracne'),
    ('ARGOS',         'ARGOS',         '3.0.0', 'PAUSED', '["osint","selectors","breaches","exif"]', 'REPLACE_ME_ARGOS', 'svc_argos'),
    ('ATENEA',        'ATENEA',        '3.0.0', 'PAUSED', '["fusion","admiralty","stix","gatekeeper"]', 'REPLACE_ME_ATENEA', 'svc_atenea')
ON CONFLICT (agent_type) DO NOTHING;

-- ---------------------------------------------------------------------
-- 26. Comentarios de contrato
-- ---------------------------------------------------------------------
COMMENT ON TABLE staging.raw_intel_ledger IS
    'Capa 1 append-only: evidencia cruda con SHA-256, TLP y cadena de custodia.';
COMMENT ON COLUMN staging.raw_intel_ledger.raw_payload_bytes IS
    'Bytes exactos capturados (UTF-8 JSON). El hash se calcula sobre estos bytes.';
COMMENT ON COLUMN staging.raw_intel_ledger.payload_sha256 IS
    'SHA-256 hex de raw_payload_bytes; verificado por trigger.';
COMMENT ON COLUMN staging.raw_intel_ledger.prev_hash IS
    'Hash de la fila anterior del ledger (cadena de custodia).';
COMMENT ON COLUMN staging.raw_intel_ledger.record_hash IS
    'Hash encadenado de esta fila.';
COMMENT ON TABLE intel.intel_entities IS
    'Grafo validado: entidades STIX 2.1 con Almirantazgo, TLP y validez temporal.';
COMMENT ON TABLE intel.intel_relationships IS
    'Grafo validado: relaciones STIX 2.1 con evidencia FK obligatoria.';
COMMENT ON TABLE intel.evidence_links IS
    'FK normalizada evidencia ↔ aserción; garantiza que no haya inteligencia huérfana.';
COMMENT ON TABLE humint.sockpuppets IS
    'Identidades operativas de Hermes; schema aislado y cifrado.';
COMMENT ON TABLE audit.events IS
    'Auditoría append-only con hash chain; no se actualiza ni borra.';
COMMENT ON FUNCTION staging.append_evidence(text, uuid, text, text, jsonb, bytea, text, timestamptz, staging.tlp_enum, text, text, numeric, jsonb, jsonb) IS
    'Única puerta de escritura del ledger; deriva collector_agent de session_user.';

-- =====================================================================
-- 27. Verificación pendiente antes de sellar
-- =====================================================================
-- [ ] Ejecutar en Testcontainers: postgres:16 + pgvector.
-- [ ] Probar T-R01..T-R28 (ver SPEC.md §10).
-- [ ] Verificar que api.v_orphan_check devuelve 0 filas.
-- [ ] Verificar que los roles no pueden escribir fuera de su zona.
-- [ ] Verificar migración y reconciliación.
-- [ ] Calcular SHA-256 de este archivo y registrar en contracts/SEAL.md.
-- =====================================================================
-- FIN DE DB_CONTRACT.sql
-- =====================================================================
