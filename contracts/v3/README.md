# Contratos v3.0.0 — OSINT Nexus

Estado: **APROBADO EN SPEC, PENDIENTE DE SELLO**.

La especificación `SPEC.md` v3.0.0 fue aprobada el 2026-10-23. Este directorio
contiene los contratos físicos que implementan el Hito 1. No sustituyen a los
contratos v2.2 sellados de `contracts/`; conviven con ellos durante la
migración.

| Archivo | Propósito | Estado |
|---|---|---|
| `db-contract.sql` | DDL, roles, RLS, funciones, triggers y vistas PostgreSQL 16 + pgvector | Pendiente de Testcontainers y sello |
| `agent-contracts.json` | JSON Schema 2020-12 de mensajes inter-agente | Pendiente de sello |

## Requisitos del motor

- PostgreSQL 16
- Extensiones: `pgcrypto`, `vector`, `btree_gist`, `pg_trgm`
- Imagen de referencia: `pgvector/pgvector:pg16`

## Aplicación en local

```bash
docker compose -f deploy/docker-compose.prod.yml up -d postgres
psql "$POSTGRES_DSN" -v ON_ERROR_STOP=1 -f contracts/v3/db-contract.sql
```

El contrato crea roles `svc_*` **sin contraseña**. Las credenciales se inyectan
desde el gestor de secretos; nunca se versionan.

## Verificación antes de sellar

```bash
python - <<'PY'
from pathlib import Path
from pglast import parse_sql
import json
from jsonschema import Draft202012Validator
parse_sql(Path('contracts/v3/db-contract.sql').read_text(encoding='utf-8'))
schema = json.loads(Path('contracts/v3/agent-contracts.json').read_text(encoding='utf-8'))
Draft202012Validator.check_schema(schema)
print('contratos v3: sintaxis OK')
PY
```

Los tests de integración viven en `apps/backend/tests/v3/db/` y usan
Testcontainers. Si Docker no está disponible, se marcan como `skip` y **no
cuentan como verificación de aceptación**.

## Sello

El sello definitivo se añadirá a `contracts/SEAL.md` cuando:

1. `db-contract.sql` se aplique en PostgreSQL 16 + pgvector limpio;
2. pasen `T-H1-01`…`T-H1-10`;
3. se revisen RLS y grants con la matriz de `SPEC.md` §4.3;
4. el hash SHA-256 quede registrado.
