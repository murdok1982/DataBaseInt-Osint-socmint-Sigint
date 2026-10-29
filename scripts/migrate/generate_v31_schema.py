"""Genera el DDL v3.1 desde ``specs/SPEC-01-DATA-MODEL.md``.

El spec es la fuente normativa; este script evita que la migración y la spec
diverjan por copia manual. Extrae los bloques SQL de las secciones 3–12, añade
la columna de bloqueo optimista ``version`` a ``staging.agent_proposals`` y
precede la migración con los renombrados de v3.0.

Uso: ``python scripts/migrate/generate_v31_schema.py``.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "specs" / "SPEC-01-DATA-MODEL.md"
OUT = ROOT / "migrations" / "v3.1" / "0001_schema.sql"
CONTRACT = ROOT / "contracts" / "v3" / "db-contract-v3.1.sql"

PREAMBLE = """-- OSINT Nexus v3.1 — esquema multi-INT, custodia y cuarentena.
-- Generado desde specs/SPEC-01-DATA-MODEL.md; no editar a mano.
--
-- Orden:
--   1. renombrar tablas v3.0 a legacy_* (para liberar los nombres canónicos);
--   2. crear extensiones, tipos y tablas v3.1;
--   3. crear triggers, RLS y vistas de compatibilidad.
--
-- Requiere PostgreSQL 16 con PostGIS 3.4 y pgvector >= 0.7.
-- La migración se ejecuta UNA vez por base; las FK de tablas legacy se
-- conservan apuntando a las tablas renombradas.

BEGIN;

-- La base v3.0 ya crea parte de estos roles. Se aseguran antes del bloque
-- normativo para que una instalación nueva y una migración real sean
-- idempotentes: el bloque del spec usa un único EXCEPTION que, si el primer
-- rol ya existe, impediría crear los demás.
DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY[
        'svc_hermes', 'svc_aracne', 'svc_argos', 'svc_atenea',
        'svc_director_cell', 'api_reader'
    ] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('CREATE ROLE %I LOGIN', role_name);
        END IF;
    END LOOP;
END
$$;

DO $$
BEGIN
    IF to_regclass('staging.raw_intel_ledger') IS NOT NULL
       AND to_regclass('staging.legacy_raw_intel_ledger') IS NULL THEN
        ALTER TABLE staging.raw_intel_ledger RENAME TO legacy_raw_intel_ledger;
    END IF;
    IF to_regclass('staging.raw_intel_attachments') IS NOT NULL
       AND to_regclass('staging.legacy_raw_intel_attachments') IS NULL THEN
        ALTER TABLE staging.raw_intel_attachments RENAME TO legacy_raw_intel_attachments;
    END IF;
    IF to_regclass('audit.agent_runs') IS NOT NULL
       AND to_regclass('audit.legacy_agent_runs') IS NULL THEN
        ALTER TABLE audit.agent_runs RENAME TO legacy_agent_runs;
    END IF;
    IF to_regclass('intel.intel_entities') IS NOT NULL
       AND to_regclass('intel.legacy_intel_entities') IS NULL THEN
        ALTER TABLE intel.intel_entities RENAME TO legacy_intel_entities;
    END IF;
    IF to_regclass('intel.intel_relationships') IS NOT NULL
       AND to_regclass('intel.legacy_intel_relationships') IS NULL THEN
        ALTER TABLE intel.intel_relationships RENAME TO legacy_intel_relationships;
    END IF;
    IF to_regclass('intel.entity_aliases') IS NOT NULL
       AND to_regclass('intel.legacy_entity_aliases') IS NULL THEN
        ALTER TABLE intel.entity_aliases RENAME TO legacy_entity_aliases;
    END IF;
    IF to_regclass('intel.evidence_links') IS NOT NULL
       AND to_regclass('intel.legacy_evidence_links') IS NULL THEN
        ALTER TABLE intel.evidence_links RENAME TO legacy_evidence_links;
    END IF;
END
$$;

-- Al renombrar una tabla, PostgreSQL conserva los nombres de sus índices y
-- constraints. La capa v3.1 reutiliza dos nombres canónicos de v3.0; se
-- renombran en la tabla legacy para evitar colisiones en una migración real.
DO $$
BEGIN
    IF to_regclass('intel.legacy_evidence_links') IS NOT NULL THEN
        IF to_regclass('intel.idx_evidence_links_evidence') IS NOT NULL THEN
            ALTER INDEX intel.idx_evidence_links_evidence
                RENAME TO legacy_idx_evidence_links_evidence;
        END IF;
        IF EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'evidence_links_one_target_chk'
              AND conrelid = 'intel.legacy_evidence_links'::regclass
        ) THEN
            ALTER TABLE intel.legacy_evidence_links
                RENAME CONSTRAINT evidence_links_one_target_chk
                TO legacy_evidence_links_one_target_chk;
        END IF;
    END IF;
END
$$;

"""

COMPAT = """
-- ---------------------------------------------------------------------------
-- Compatibilidad de lectura v3.0 (security_invoker: RLS se evalúa como caller)
-- ---------------------------------------------------------------------------
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

CREATE VIEW intel.intel_relationships
WITH (security_invoker = true) AS
SELECT relationship_id, source_entity_id, target_entity_id, relationship_type,
       confidence_score AS confidence, admiralty_rating AS admiralty,
       tlp_classification, valid_from, valid_until, event_id, properties,
       revoked, revoked_at, revoked_reason, created_by_agent, created_at,
       updated_at
FROM intel.intelligence_relationships;

CREATE VIEW staging.raw_intel_attachments
WITH (security_invoker = true) AS
SELECT attachment_id, evidence_id, filename, mime_type, size_bytes, sha256,
       storage_uri, created_at, metadata
FROM staging.raw_evidence_attachments;

-- La API humana no recibe UPDATE directo: decide por función SECURITY DEFINER.
ALTER TABLE staging.agent_proposals ENABLE ROW LEVEL SECURITY;

CREATE POLICY proposals_api_read ON staging.agent_proposals
FOR SELECT USING (
    intel.tlp_rank(tlp_classification::text) <=
    intel.tlp_rank(coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR'))
);

GRANT SELECT ON staging.agent_proposals, staging.raw_evidence_vault,
    staging.raw_evidence_attachments TO api_reader;

-- Las políticas RLS invocan intel.tlp_rank(text) como el rol consultante.
-- El DDL v3.0 revocó EXECUTE de PUBLIC, así que cada rol de servicio necesita
-- el grant explícito o toda lectura TLP falla con "permission denied".
GRANT EXECUTE ON FUNCTION intel.tlp_rank(text)
    TO api_reader, svc_director_cell, svc_atenea,
       svc_hermes, svc_aracne, svc_argos;

-- Identidad y clearance NO son parámetros: la función los toma de la transacción
-- (osint.actor y osint.clearance), los mismos valores que usa RLS. Un cliente
-- no puede declarar otro revisor ni otro nivel TLP en la llamada.
CREATE OR REPLACE FUNCTION staging.fn_decide_proposal(
    p_proposal_id uuid,
    p_status intel.proposal_status_enum,
    p_reason text,
    p_version integer
) RETURNS SETOF staging.agent_proposals
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = staging, intel, pg_temp
AS $$
DECLARE
    v_reviewer  text := NULLIF(btrim(current_setting('osint.actor', true)), '');
    v_clearance text := coalesce(current_setting('osint.clearance', true), 'TLP:CLEAR');
BEGIN
    IF p_status NOT IN ('ANALYST_VERIFIED', 'REJECTED') THEN
        RAISE EXCEPTION 'Transición de propuesta no permitida: %', p_status
            USING ERRCODE = '22023';
    END IF;
    IF v_reviewer IS NULL THEN
        RAISE EXCEPTION 'osint.actor no establecido: la decisión exige identidad de sesión'
            USING ERRCODE = '42501';
    END IF;
    IF p_reason IS NULL OR length(btrim(p_reason)) < 8 THEN
        RAISE EXCEPTION 'decision_reason debe tener >= 8 caracteres'
            USING ERRCODE = '23514';
    END IF;
    RETURN QUERY
    UPDATE staging.agent_proposals p
       SET status = p_status,
           reviewed_by = v_reviewer,
           reviewed_at = now(),
           decision_reason = p_reason,
           version = p.version + 1
     WHERE p.proposal_id = p_proposal_id
       AND p.version = p_version
       AND p.status = 'AGENT_STAGED'
       AND intel.tlp_rank(p.tlp_classification::text) <= intel.tlp_rank(v_clearance)
    RETURNING p.*;
END;
$$;

REVOKE ALL ON FUNCTION staging.fn_decide_proposal(
    uuid, intel.proposal_status_enum, text, integer
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION staging.fn_decide_proposal(
    uuid, intel.proposal_status_enum, text, integer
) TO api_reader;

-- La verificación de cadena lee todo el ledger, por eso es SECURITY DEFINER
-- (RLS la falsearía). Sólo puede invocarla el lector de la API, el atenea y el
-- director; el resto de roles de servicio queda fuera.
REVOKE ALL ON FUNCTION staging.verify_evidence_chain(bigint, bigint) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION staging.verify_evidence_chain(bigint, bigint)
    TO api_reader, svc_atenea, svc_director_cell;

-- Backfill de alias v3.0 -> v3.1 (sólo si existe la tabla legacy).
DO $$
BEGIN
    IF to_regclass('intel.legacy_entity_aliases') IS NOT NULL
       AND to_regclass('intel.observable_alias_history') IS NOT NULL THEN
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
        FROM intel.legacy_entity_aliases ea
        JOIN intel.core_entities e ON e.entity_id = ea.entity_id
        JOIN LATERAL (
            SELECT o.observable_id
            FROM intel.observables o
            WHERE o.entity_id = ea.entity_id
            ORDER BY o.created_at
            LIMIT 1
        ) o ON true
        WHERE ea.source_evidence_id IS NOT NULL;
    END IF;
END
$$;
"""


def extract_sql_blocks(text: str) -> list[str]:
    blocks = re.findall(r"```sql\n(.*?)```", text, flags=re.S)
    # Secciones 3–12: desde extensiones hasta RLS. El bloque 13.x no se usa
    # porque la compatibilidad/backfill se añaden abajo con nombres legacy.
    selected: list[str] = []
    in_range = False
    for block in blocks:
        if "CREATE EXTENSION IF NOT EXISTS pgcrypto" in block:
            in_range = True
        if not in_range:
            continue
        if block.lstrip().startswith("INSERT INTO intel.observable_alias_history"):
            break
        selected.append(block.strip())
        if "FORCE ROW LEVEL SECURITY" in block and "raw_evidence_attachments" in block:
            break
    return selected


def main() -> None:
    text = SPEC.read_text(encoding="utf-8")
    blocks = extract_sql_blocks(text)
    schema = "\n\n".join(blocks)
    if "CREATE TABLE staging.agent_proposals (" not in schema:
        raise SystemExit("No se encontró agent_proposals en el spec")

    # Las políticas RLS del spec pasan columnas enum directamente a
    # ``intel.tlp_rank(text)``. PostgreSQL no aplica la conversión implícita
    # enum -> text en el despacho de funciones, así que se fija el cast.
    schema = re.sub(
        r"intel\.tlp_rank\(((?:[A-Za-z_][A-Za-z0-9_]*\.)?tlp_classification)\)",
        r"intel.tlp_rank(\1::text)",
        schema,
    )

    # Bloqueo optimista exigido por SPEC-02 §11.5; el DDL original no lo tenía.
    schema = schema.replace(
        "    proposal_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),\n",
        "    proposal_id        uuid PRIMARY KEY DEFAULT staging.uuid_v7(),\n"
        "    version            integer NOT NULL DEFAULT 1 CHECK (version >= 1),\n",
        1,
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    CONTRACT.parent.mkdir(parents=True, exist_ok=True)
    ddl = PREAMBLE + schema + "\n" + COMPAT + "\nCOMMIT;\n"
    # `newline="\n"` evita que Windows convierta la migración a CRLF y rompa
    # la igualdad byte a byte con el contrato sellado.
    OUT.write_text(ddl, encoding="utf-8", newline="\n")
    CONTRACT.write_text(ddl, encoding="utf-8", newline="\n")
    print(
        f"Wrote {OUT} and {CONTRACT} "
        f"({len((PREAMBLE + schema + COMPAT).splitlines())} lines)"
    )


if __name__ == "__main__":
    main()
