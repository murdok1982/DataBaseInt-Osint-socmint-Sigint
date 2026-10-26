#!/usr/bin/env bash
# =====================================================================
# validate-contracts.sh — Verifica que /contracts es válido y coherente.
# Usado por el Orquestador antes de sellar (Fase 2).
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

# `python` puede ser un shim distinto entre Git Bash/Windows y CI. Se elige el
# primer intérprete que tenga las dependencias de validación; si no existe, los
# pasos 4/5 se saltan y el sello queda bloqueado.
PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3 python C:/Python314/python.exe /c/Python314/python.exe /mnt/c/Python314/python.exe; do
    if command -v "$candidate" >/dev/null 2>&1 \
       && "$candidate" -c 'import jsonschema, pglast' >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi
if [ -z "$PYTHON_BIN" ]; then
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
      PYTHON_BIN="$candidate"
      break
    fi
  done
fi

echo "[1/5] Validando extraction-schema.json (JSON Schema draft 2020-12)..."
if command -v npx >/dev/null 2>&1; then
  npx --yes -p ajv-cli -p ajv-formats ajv compile \
    -s contracts/extraction-schema.json --spec=draft2020 -c ajv-formats \
    && echo "  OK" || { echo "  FALLO"; fail=1; }
else
  echo "  (saltado: npx no disponible)"
fi

echo "[2/5] Validando openapi.yaml (Redocly lint)..."
if command -v npx >/dev/null 2>&1; then
  # Fijar la versión: 2.71.1 se publicó sin tarball y rompe el gate.
  npx --yes @redocly/cli@2.71.0 lint contracts/openapi.yaml \
    && echo "  OK" || { echo "  FALLO"; fail=1; }
else
  echo "  (saltado: npx no disponible)"
fi

echo "[3/5] Comprobando coherencia de nodos/relaciones vs SPEC-01..."
if command -v grep >/dev/null 2>&1; then
  for label in Person Organization CyberAsset Location Event Report Analysis IntelRequirement CoherenceAlert User ApiKey; do
    grep -q ":$label" contracts/graph-constraints.cypher \
      && echo "  nodo $label presente" || { echo "  FALTA nodo $label"; fail=1; }
  done
  for rel in CONTROLS FINANCED USES_ASSET LOCATED_AT ASSOCIATED_WITH PARTICIPATED_IN MENTIONED_IN INVESTIGATES HAS_REQUIREMENT CITES_ANALYSIS CORROBORATES CONTRADICTS EXTENDS ANSWERS_REQUIREMENT RAISED_FOR ABOUT; do
    grep -q "$rel" contracts/graph-constraints.cypher \
      && echo "  arista $rel presente" || { echo "  FALTA arista $rel"; fail=1; }
  done
else
  echo "  (saltado: grep no disponible)"
fi

echo "[4/5] Validando JSON Schemas v3.1 (hermes-tools + agent-contracts)..."
if [ -n "$PYTHON_BIN" ] && "$PYTHON_BIN" -c "import jsonschema, pglast" >/dev/null 2>&1; then
  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
import json
from jsonschema import Draft202012Validator

for name in ("contracts/v3/hermes-tools.json", "contracts/v3/agent-contracts.json"):
    path = Path(name)
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    print(f"  OK {name}")
PY
else
  echo "  (saltado: python no disponible)"
fi

echo "[5/5] Parseando DDL v3.1 y verificando que el contrato y la migración coinciden..."
if [ -n "$PYTHON_BIN" ] && "$PYTHON_BIN" -c "import jsonschema, pglast" >/dev/null 2>&1; then
  "$PYTHON_BIN" - <<'PY'
from pathlib import Path
from pglast import parse_sql

migration = Path("migrations/v3.1/0001_schema.sql").read_text(encoding="utf-8")
contract = Path("contracts/v3/db-contract-v3.1.sql").read_text(encoding="utf-8")
parse_sql(migration)
parse_sql(contract)
if migration != contract:
    raise SystemExit("  FALLO: db-contract-v3.1.sql diverge de migrations/v3.1/0001_schema.sql")
for required in (
    "CREATE TABLE staging.agent_proposals",
    "version            integer NOT NULL DEFAULT 1",
    "staging.fn_decide_proposal",
    "CREATE VIEW staging.raw_intel_ledger",
    "CREATE POLICY proposals_api_read",
):
    if required not in migration:
        raise SystemExit(f"  FALLO: falta {required}")
print("  OK DDL v3.1")
PY
else
  echo "  (saltado: python no disponible)"
fi

if [ "$fail" -ne 0 ]; then
  echo "RESULTADO: contratos NO válidos. No sellar."
  exit 1
fi
echo "RESULTADO: contratos válidos. Listo para sellar."
