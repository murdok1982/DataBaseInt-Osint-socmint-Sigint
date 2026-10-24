#!/usr/bin/env bash
# =====================================================================
# restore.sh — Restauración de una copia cifrada de OSINT Nexus
#
# Autorizado por: SPEC-06 §3 (verificar SHA256SUMS antes de descifrar,
#                 confirmación explícita, RTO 4 h).
#
# SOBRESCRIBE Neo4j, la base PostgreSQL (ledger v3) y el volumen de informes.
# Sin --yes solo verifica la integridad de la copia y termina.
#
# Las copias anteriores a v3 no contienen postgres.dump.gpg: se rechazan para
# no restaurar un estado parcial sin querer.
#
# Uso:
#     bash deploy/scripts/restore.sh /var/backups/osint-nexus/20261017T023000Z         # verifica
#     bash deploy/scripts/restore.sh /var/backups/osint-nexus/20261017T023000Z --yes   # restaura
#
# Variables: BACKUP_PASSPHRASE_FILE, NEO4J_USER, NEO4J_PASSWORD, NEO4J_DATABASE
# =====================================================================
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-${DEPLOY_DIR}/docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-${DEPLOY_DIR}/.env}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

die() { echo "ERROR: $*" >&2; exit 2; }

SOURCE="${1:-}"
CONFIRM="${2:-}"
[[ -n "${SOURCE}" && -d "${SOURCE}" ]] || die "Uso: restore.sh <directorio-de-copia> [--yes]"

BACKUP_PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-}"
NEO4J_USER="${NEO4J_USER:-neo4j}"
NEO4J_PASSWORD="${NEO4J_PASSWORD:-}"
NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"
[[ -s "${BACKUP_PASSPHRASE_FILE}" ]] || die "BACKUP_PASSPHRASE_FILE no existe o está vacío."

echo "[1/6] Verificando SHA256SUMS…"
for artifact in neo4j.tar.gz.gpg postgres.dump.gpg storage.tar.gz.gpg; do
  [[ -f "${SOURCE}/${artifact}" ]] || die "Copia incompleta: falta ${artifact}."
done
( cd "${SOURCE}" && sha256sum --check --strict SHA256SUMS ) \
  || die "La copia está corrupta o manipulada: NO se restaura."

if [[ "${CONFIRM}" != "--yes" ]]; then
  echo "RESULTADO: copia íntegra. Para restaurar (sobrescribe datos) añade --yes."
  exit 0
fi
[[ -n "${NEO4J_PASSWORD}" ]] || die "NEO4J_PASSWORD no está definida."

compose() {
  docker compose --project-directory "${DEPLOY_DIR}" -f "${COMPOSE_FILE}" "$@"
}
decrypt() {
  gpg --batch --quiet --pinentry-mode loopback \
    --passphrase-file "${BACKUP_PASSPHRASE_FILE}" --decrypt "$1"
}
system_cypher() {
  compose exec -T neo4j cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASSWORD}" \
    -d system "$1"
}

STAGE="/backups/restore-$$"
cleanup() { compose exec -T neo4j rm -rf "${STAGE}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[2/6] Deteniendo ingesta (worker) para evitar escrituras concurrentes…"
compose stop worker

echo "[3/6] Restaurando Neo4j (${NEO4J_DATABASE})…"
decrypt "${SOURCE}/neo4j.tar.gz.gpg" \
  | compose exec -T neo4j sh -c "mkdir -p '${STAGE}' && tar -xzf - -C '${STAGE}'"
system_cypher "STOP DATABASE \`${NEO4J_DATABASE}\` WAIT;"
compose exec -T neo4j neo4j-admin database restore \
  --from-path="${STAGE}" --overwrite-destination=true "${NEO4J_DATABASE}"
system_cypher "START DATABASE \`${NEO4J_DATABASE}\` WAIT;"

echo "[4/6] Restaurando PostgreSQL (ledger v3)…"
# --clean --if-exists: sustituye los objetos existentes por los del volcado.
decrypt "${SOURCE}/postgres.dump.gpg" \
  | compose exec -T postgres sh -c \
    'pg_restore --clean --if-exists --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

echo "[5/6] Restaurando volumen de informes (/storage)…"
decrypt "${SOURCE}/storage.tar.gz.gpg" \
  | compose exec -T backend sh -c "find /storage -mindepth 1 -delete && tar -xzf - -C /storage"

echo "[6/6] Reanudando worker y aplicando el contrato de grafo…"
compose start worker
bash "${SCRIPT_DIR}/apply-constraints.sh"

echo "RESULTADO: restauración completada desde ${SOURCE}. Ejecuta smoke-test.sh."
