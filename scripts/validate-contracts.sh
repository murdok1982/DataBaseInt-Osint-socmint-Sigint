#!/usr/bin/env bash
# =====================================================================
# validate-contracts.sh — Verifica que /contracts es válido y coherente.
# Usado por el Orquestador antes de sellar (Fase 2).
# =====================================================================
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

echo "[1/3] Validando extraction-schema.json (JSON Schema draft 2020-12)..."
if command -v npx >/dev/null 2>&1; then
  npx --yes -p ajv-cli -p ajv-formats ajv compile \
    -s contracts/extraction-schema.json --spec=draft2020 -c ajv-formats \
    && echo "  OK" || { echo "  FALLO"; fail=1; }
else
  echo "  (saltado: npx no disponible)"
fi

echo "[2/3] Validando openapi.yaml (Redocly lint)..."
if command -v npx >/dev/null 2>&1; then
  npx --yes @redocly/cli lint contracts/openapi.yaml \
    && echo "  OK" || { echo "  FALLO"; fail=1; }
else
  echo "  (saltado: npx no disponible)"
fi

echo "[3/3] Comprobando coherencia de nodos/relaciones vs SPEC-01..."
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

if [ "$fail" -ne 0 ]; then
  echo "RESULTADO: contratos NO válidos. No sellar."
  exit 1
fi
echo "RESULTADO: contratos válidos. Listo para sellar."
