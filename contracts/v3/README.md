# Contratos v3.1.0 — OSINT Nexus

Estado: **APROBADO EN SPEC v3.1, PENDIENTE DE SELLO TESTCONTAINERS**.

La especificación `SPEC.md` v3.0.0 fue aprobada el 2026-10-23; las specs
v3.1 (`SPEC-01`…`SPEC-04`) fueron aprobadas por el usuario el 2026-10-26. Este
directorio contiene los contratos físicos de la migración multi-INT. Los
contratos v2.2 sellados de `contracts/` siguen vigentes para la API histórica
durante la transición.

| Archivo | Propósito | Estado |
|---|---|---|
| `db-contract.sql` | DDL v3.0.0 histórico (Hito 1) | Congelado |
| `db-contract-v3.1.sql` | DDL v3.1: capas, custodia, propuestas, PostGIS/pgvector, RLS, compatibilidad v3.0 | Pendiente de Testcontainers y sello |
| `hermes-tools.json` | Bundle JSON Schema 2020-12 de las cinco tools | Pendiente de sello |
| `agent-contracts.json` | JSON Schema 2020-12 de mensajes inter-agente | Pendiente de sello |

## Requisitos del motor

- PostgreSQL 16
- PostGIS 3.4
- pgvector >= 0.7
- Extensiones: `pgcrypto`, `vector`, `btree_gist`, `pg_trgm`, `postgis`
- Imagen de referencia: `deploy/postgres/Dockerfile` (pgvector + PostGIS)

## Aplicación en local

```bash
docker compose -f deploy/docker-compose.prod.yml up -d postgres
psql "$POSTGRES_DSN" -v ON_ERROR_STOP=1 -f contracts/v3/db-contract-v3.1.sql
```

La migración v3.1 **renombra** las tablas v3.0 a `legacy_*` y crea vistas de
compatibilidad de sólo lectura (`staging.raw_intel_ledger`,
`audit.agent_runs`, `intel.intel_entities`, `intel.intel_relationships`). El
camino de escritura nuevo usa `staging.raw_evidence_vault`,
`intel.core_entities` y `staging.agent_proposals`.

El contrato crea roles `svc_*` **sin contraseña**. Las credenciales se inyectan
desde el gestor de secretos; nunca se versionan. La API humana usa `api_reader`
con `SET LOCAL osint.clearance`; las aprobaciones pasan por
`staging.fn_decide_proposal` (`SECURITY DEFINER`).

## Verificación antes de sellar

```bash
python - <<'PY'
from pathlib import Path
from pglast import parse_sql
import json
from jsonschema import Draft202012Validator

for path in ("contracts/v3/db-contract-v3.1.sql", "migrations/v3.1/0001_schema.sql"):
    parse_sql(Path(path).read_text(encoding="utf-8"))
for path in ("contracts/v3/hermes-tools.json", "contracts/v3/agent-contracts.json"):
    schema = json.loads(Path(path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
print("contratos v3.1: sintaxis OK")
PY
```

Los tests de integración viven en `apps/backend/tests/v3/db/` y usan
Testcontainers. Si Docker no está disponible, se marcan como `skip` y **no
cuentan como verificación de aceptación**.

## Sello

El sello definitivo se añadirá a `contracts/SEAL.md` cuando:

1. `db-contract-v3.1.sql` se aplique en PostgreSQL 16 + PostGIS 3.4 + pgvector
   limpio y sobre una copia v3.0;
2. pasen los tests de aceptación v3.1 (migración, RLS, idempotencia, propuestas);
3. se revisen RLS y grants con la matriz de `SPEC.md` §4.3;
4. los hashes SHA-256 de `db-contract-v3.1.sql`, `hermes-tools.json` y
   `openapi.yaml` queden registrados.
