# Migración Neo4j v2.2 → PostgreSQL v3

Script: `neo4j_to_pg.py` (Hito 1, Agente 1).

## Flujo

```bash
# 1. Exportar el grafo actual (no modifica Neo4j)
python scripts/migrate/neo4j_to_pg.py export --out storage/migration

# 2. Cargar en PostgreSQL (requiere contrato v3 aplicado)
python scripts/migrate/neo4j_to_pg.py load \
  --nodes storage/migration/nodes.jsonl \
  --edges storage/migration/edges.jsonl \
  --owner-dsn "$POSTGRES_DSN_OWNER" \
  --atenea-dsn "$POSTGRES_DSN_ATENEA"

# 3. Revisar reconciliación
python scripts/migrate/neo4j_to_pg.py reconcile --owner-dsn "$POSTGRES_DSN_OWNER"
```

## Garantías

- **No borra** Neo4j ni el grafo original.
- Cada nodo/arista migrado genera una fila en `staging.raw_intel_ledger` con
  `collector_agent = 'MIGRATION_LEGACY'`, SHA-256 de los bytes y cadena de
  custodia. La idempotencia usa `legacy:<label>:<id>`.
- Lo que no se puede mapear sin ambigüedad va a
  `staging.migration_quarantine`; no se inventan entidades ni relaciones.
- `staging.migration_reconciliation` registra conteos y detalles de la corrida.
- El grafo validado se escribe como `svc_atenea` mediante
  `intel.upsert_entity` / `intel.upsert_relationship`; por tanto, toda entidad
  migrada tiene evidencia enlazada.

## Límites conocidos

- Los nodos `Report`, `Analysis`, `IntelRequirement`, `CoherenceAlert`, `User`
  y `ApiKey` no son entidades STIX y se envían a cuarentena.
- El mapeo de tipos de relación legacy es conservador (`REL_MAP`); las aristas
  cuyo tipo/tipos de extremo no encajan se cuarentenan.
- La carga es **secuencial** para preservar la cadena de custodia; no paralelizar
  sin un diseño de idempotencia y bloqueo revisado.
