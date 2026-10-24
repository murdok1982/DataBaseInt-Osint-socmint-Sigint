#!/usr/bin/env bash
# =====================================================================
# backup.sh — Copia de seguridad cifrada de OSINT Nexus
#
# Autorizado por: SPEC-06 §3 (RPO 24 h, cifrado AES-256, SHA256SUMS,
#                 retención BACKUP_RETENTION_DAYS) y Constitución Regla 5
#                 (Neo4j nunca se expone: todo va por `docker compose exec`).
#
# Genera en ${BACKUP_DIR}/<UTC-timestamp>/:
#   neo4j.tar.gz.gpg     copia en caliente (neo4j-admin database backup)
#   postgres.dump.gpg    volcado pg_dump -Fc del ledger v3 (PostgreSQL 16)
#   storage.tar.gz.gpg   volumen de informes (/storage del backend)
#   SHA256SUMS           sumas de los artefactos cifrados
#
# PostgreSQL custodia el ledger de cadena de custodia (PLAN.md Hito 1): su
# volcado es obligatorio. Si el servicio no responde, la copia FALLA.
#
# Los datos en claro solo atraviesan tuberías (contenedor -> gpg): nunca
# se escriben sin cifrar en el disco del host.
#
# Uso (cron diario recomendado, p. ej. 02:30 UTC):
#     bash deploy/scripts/backup.sh
#
# Variables (del .env o del entorno):
#     BACKUP_DIR              destino en el host        (obligatoria)
#     BACKUP_PASSPHRASE_FILE  frase de cifrado, 0600     (obligatoria)
#     BACKUP_RETENTION_DAYS   días que se conservan      (def. 30)
#     NEO4J_DATABASE          base de datos              (def. neo4j)
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

BACKUP_DIR="${BACKUP_DIR:-}"
BACKUP_PASSPHRASE_FILE="${BACKUP_PASSPHRASE_FILE:-}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
NEO4J_DATABASE="${NEO4J_DATABASE:-neo4j}"

die() { echo "ERROR: $*" >&2; exit 2; }

[[ -n "${BACKUP_DIR}" ]] || die "BACKUP_DIR no está definida (ver deploy/.env.example)."
[[ -n "${BACKUP_PASSPHRASE_FILE}" ]] || die "BACKUP_PASSPHRASE_FILE no está definida."
[[ -s "${BACKUP_PASSPHRASE_FILE}" ]] || die "El fichero de frase no existe o está vacío."
perms="$(stat -c '%a' "${BACKUP_PASSPHRASE_FILE}")"
[[ "${perms}" == "600" || "${perms}" == "400" ]] \
  || die "${BACKUP_PASSPHRASE_FILE} debe tener permisos 0600 (tiene ${perms})."
[[ "${BACKUP_RETENTION_DAYS}" =~ ^[0-9]+$ ]] || die "BACKUP_RETENTION_DAYS debe ser un entero."
command -v gpg >/dev/null 2>&1 || die "gpg (GnuPG) no está instalado en el host."

compose() {
  docker compose --project-directory "${DEPLOY_DIR}" -f "${COMPOSE_FILE}" "$@"
}

encrypt_to() {
  gpg --batch --yes --quiet --pinentry-mode loopback \
    --passphrase-file "${BACKUP_PASSPHRASE_FILE}" \
    --symmetric --cipher-algo AES256 --output "$1"
}

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_DIR}/${STAMP}"
STAGE="/backups/${STAMP}"
mkdir -p "${TARGET}"

cleanup() { compose exec -T neo4j rm -rf "${STAGE}" >/dev/null 2>&1 || true; }
trap cleanup EXIT

echo "[1/5] Copia en caliente de Neo4j (${NEO4J_DATABASE})…"
compose exec -T neo4j mkdir -p "${STAGE}"
compose exec -T neo4j neo4j-admin database backup \
  --to-path="${STAGE}" "${NEO4J_DATABASE}"
compose exec -T neo4j tar -czf - -C "${STAGE}" . | encrypt_to "${TARGET}/neo4j.tar.gz.gpg"

echo "[2/5] Volcado de PostgreSQL (ledger v3)…"
# Las credenciales se leen DENTRO del contenedor (variables de POSTGRES_*):
# nunca viajan por la línea de comandos del host.
compose exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  | encrypt_to "${TARGET}/postgres.dump.gpg"

echo "[3/5] Volumen de informes (/storage)…"
compose exec -T backend tar -czf - -C /storage . | encrypt_to "${TARGET}/storage.tar.gz.gpg"

echo "[4/5] Manifiesto SHA256SUMS…"
( cd "${TARGET}" && sha256sum neo4j.tar.gz.gpg postgres.dump.gpg storage.tar.gz.gpg > SHA256SUMS )

echo "[5/5] Retención: se eliminan copias de más de ${BACKUP_RETENTION_DAYS} días…"
find "${BACKUP_DIR}" -mindepth 1 -maxdepth 1 -type d -name '20*Z' \
  -mtime +"${BACKUP_RETENTION_DAYS}" -print -exec rm -rf {} +

echo "RESULTADO: copia cifrada en ${TARGET}"
