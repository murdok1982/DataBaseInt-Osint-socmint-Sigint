#!/usr/bin/env bash
# =====================================================================
# apply-constraints.sh — Aplica contracts/graph-constraints.cypher a Neo4j
#
# Autorizado por:
#   - SPEC-04 §2 (Neo4j en internal_intel_net, sin ports publicados).
#   - contracts/graph-constraints.cypher (SELLADO v2.1.0: índice vectorial
#     por label; ya no hay índice multi-label ni SPEC-GAP-3.8).
#
# Idempotente: el contrato usa ``CREATE ... IF NOT EXISTS``; reejecutarlo no
# duplica ni rompe objetos. Se ejecuta contra el contenedor vivo:
#
#     docker compose exec -T neo4j cypher-shell --file /constraints/...
#
# Uso:
#     bash deploy/scripts/apply-constraints.sh
#
# Variables de entorno:
#     ENV_FILE            .env a cargar        (def. <deploy>/.env)
#     COMPOSE_FILE        compose de producción(def. <deploy>/docker-compose.prod.yml)
#     NEO4J_USER          usuario              (def. neo4j)
#     NEO4J_PASSWORD      contraseña           (def. del .env; obligatoria)
#
# Salida: 0 si se aplican y verifican todos los objetos del contrato;
#         != 0 ante cualquier desviación.
# =====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

COMPOSE_FILE="${COMPOSE_FILE:-${DEPLOY_DIR}/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-${DEPLOY_DIR}/.env}"

#: Ruta del contrato DENTRO del contenedor neo4j (bind-mount ro del compose).
CONSTRAINTS_IN_CONTAINER="/constraints/graph-constraints.cypher"

# --- Credenciales (del .env o del entorno) ----------------------------
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-}"

if [[ -z "${NEO4J_PASSWORD}" ]]; then
  echo "ERROR: NEO4J_PASSWORD no está definida. Revisa ${ENV_FILE} (ver .env.example)." >&2
  exit 2
fi

compose() {
  docker compose --project-directory "${DEPLOY_DIR}" -f "${COMPOSE_FILE}" "$@"
}

cypher() {
  compose exec -T neo4j cypher-shell \
    -u "${NEO4J_USER}" -p "${NEO4J_PASSWORD}" "$@"
}

# --- Objetos esperados (nombres exactos del contrato) -----------------
EXPECTED_CONSTRAINTS=(
  person_id_unique organization_id_unique cyberasset_id_unique location_id_unique
  event_id_unique report_id_unique analysis_id_unique requirement_id_unique
  alert_id_unique
  organization_regid_unique cyberasset_value_unique analysis_code_unique
  user_id_unique user_username_unique apikey_id_unique apikey_key_hash_unique
  person_required organization_required cyberasset_required location_required
  event_required report_required analysis_required requirement_required
  alert_required
)
EXPECTED_INDEXES=(
  person_name_idx organization_regid_idx cyberasset_value_idx location_name_idx
  event_date_idx report_ingested_idx analysis_status_idx requirement_status_idx
  alert_status_idx entity_locked_idx
  entity_names_ft
  person_embedding_idx organization_embedding_idx cyberasset_embedding_idx
)

# =====================================================================
# 1. Esperar a que Neo4j esté listo
# =====================================================================
echo "[1/3] Esperando a que Neo4j acepte conexiones…"
ready=0
for _ in $(seq 1 60); do
  if cypher "RETURN 1;" >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
if [[ "${ready}" != "1" ]]; then
  echo "ERROR: Neo4j no respondió. ¿Está arriba el servicio?" >&2
  echo "       Levanta el stack con: docker compose --project-directory '${DEPLOY_DIR}' -f '${COMPOSE_FILE}' up -d neo4j" >&2
  exit 1
fi

# =====================================================================
# 2. Aplicar el contrato (idempotente)
# =====================================================================
echo "[2/3] Aplicando ${CONSTRAINTS_IN_CONTAINER} (CREATE ... IF NOT EXISTS)…"
apply_rc=0
cypher --file "${CONSTRAINTS_IN_CONTAINER}" --fail-at-end || apply_rc=$?
if [[ "${apply_rc}" -ne 0 ]]; then
  echo "AVISO: cypher-shell devolvió ${apply_rc}. Se verifica el estado real a continuación." >&2
fi

# =====================================================================
# 3. Verificar que todos los objetos existen
# =====================================================================
echo "[3/3] Verificando constraints e índices…"
constraints="$(cypher --format plain "SHOW CONSTRAINTS YIELD name RETURN name;" 2>/dev/null | tr -d '\r' || true)"
indexes="$(cypher --format plain "SHOW INDEXES YIELD name RETURN name;" 2>/dev/null | tr -d '\r' || true)"

has() { grep -qxF "$2" <<<"$1"; }

missing=0
for name in "${EXPECTED_CONSTRAINTS[@]}"; do
  if has "${constraints}" "${name}"; then
    echo "  [OK]    constraint ${name}"
  else
    echo "  [FALTA] constraint ${name}" >&2
    missing=$((missing + 1))
  fi
done

for name in "${EXPECTED_INDEXES[@]}"; do
  if has "${indexes}" "${name}"; then
    echo "  [OK]    índice ${name}"
  else
    echo "  [FALTA] índice ${name}" >&2
    missing=$((missing + 1))
  fi
done

# =====================================================================
# Resultado
# =====================================================================
if [[ "${missing}" -gt 0 ]]; then
  echo "RESULTADO: FALTAN ${missing} objetos del contrato. Revisa la salida anterior." >&2
  exit 1
fi

if [[ "${apply_rc}" -ne 0 ]]; then
  echo "RESULTADO: cypher-shell reportó errores al aplicar el contrato." >&2
  exit 1
fi

echo "RESULTADO: constraints e índices del contrato aplicados y verificados."
