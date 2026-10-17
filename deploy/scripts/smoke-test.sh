#!/usr/bin/env bash
# =====================================================================
# smoke-test.sh — Verificación de aceptación contra el stack VIVO
#
# Autorizado por: SPEC-04 §4 (criterios de aceptación) y
# contracts/openapi.yaml (SELLADO v2.0.0, base /api/v1).
#
# Comprueba, de extremo a extremo, los endpoints reales del contrato:
#   1. GET  /info               (público)
#   2. POST /auth/login         (JWT humano)
#   3. POST /context/preflight  (M2M, X-API-Key)
#   4. POST /ingest/informant   (M2M + X-Preflight-Token) -> 202
#   5. GET  /graph/nodes        (Bearer)
#   6. POST /search/rag         (Bearer)
#   7. GET  /coherence/alerts   (Bearer)
#
# Uso:
#     SMOKE_USERNAME=admin SMOKE_PASSWORD=... SMOKE_API_KEY=... \
#         bash deploy/scripts/smoke-test.sh
#
# Variables (o en deploy/.env; ver .env.example):
#     PUBLIC_HOSTNAME   host público (def. del .env)  -> https://<host>/api/v1
#     SMOKE_BASE_URL    base explícita (tiene prioridad)
#     SMOKE_USERNAME    operador ADMIN/ANALYST
#     SMOKE_PASSWORD    contraseña del operador
#     SMOKE_API_KEY     API Key M2M (def. INFORMANT_API_KEY)
#     SMOKE_INSECURE    1 => curl -k (certificados autofirmados en pruebas)
#
# Es idempotente/repetible: no muta estado persistente (crea un job de ingesta
# efímero). Devuelve != 0 ante cualquier desviación del contrato.
# =====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
ENV_FILE="${ENV_FILE:-${DEPLOY_DIR}/.env}"

# --- Prerrequisitos ---------------------------------------------------
if ! command -v curl >/dev/null 2>&1; then
  echo "ERROR: se requiere 'curl' en el host." >&2
  exit 2
fi

# --- Configuración (entorno > .env > defaults) ------------------------
if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

PUBLIC_HOSTNAME="${PUBLIC_HOSTNAME:-}"
BASE_URL="${SMOKE_BASE_URL:-}"
if [[ -z "${BASE_URL}" ]]; then
  if [[ -z "${PUBLIC_HOSTNAME}" ]]; then
    echo "ERROR: define SMOKE_BASE_URL o PUBLIC_HOSTNAME (ver .env.example)." >&2
    exit 2
  fi
  BASE_URL="https://${PUBLIC_HOSTNAME}/api/v1"
fi
BASE_URL="${BASE_URL%/}"

SMOKE_USERNAME="${SMOKE_USERNAME:-}"
SMOKE_PASSWORD="${SMOKE_PASSWORD:-}"
SMOKE_API_KEY="${SMOKE_API_KEY:-${INFORMANT_API_KEY:-}}"
SMOKE_TOTP_CODE="${SMOKE_TOTP_CODE:-}"
SMOKE_INSECURE="${SMOKE_INSECURE:-0}"

for required in SMOKE_USERNAME SMOKE_PASSWORD SMOKE_API_KEY; do
  if [[ -z "${!required}" ]]; then
    echo "ERROR: falta ${required}. Expórtala o defínela en ${ENV_FILE}." >&2
    exit 2
  fi
done

CURL_OPTS=(-sS --max-time 30)
if [[ "${SMOKE_INSECURE}" == "1" ]]; then
  CURL_OPTS+=(-k)
fi

# --- Utilidades -------------------------------------------------------
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT
BODY="${TMP_DIR}/body"

PASS=0
FAIL=0

# http METHOD URL [args...] — imprime el código HTTP; deja el cuerpo en $BODY.
http() {
  local method="$1" url="$2"
  shift 2
  local code
  code="$(curl "${CURL_OPTS[@]}" -o "${BODY}" -w '%{http_code}' \
    -X "${method}" "${url}" "$@" 2>"${TMP_DIR}/curlerr" || true)"
  printf '%s' "${code:-000}"
}

# check LABEL EXPECTED ACTUAL
check() {
  local label="$1" expected="$2" actual="$3"
  if [[ "${actual}" == "${expected}" ]]; then
    printf '  [ OK ]  %-46s %s\n' "${label}" "${actual}"
    PASS=$((PASS + 1))
  else
    printf '  [FAIL]  %-46s esperado=%s obtenido=%s\n' "${label}" "${expected}" "${actual}" >&2
    if [[ -s "${BODY}" ]]; then
      printf '          cuerpo: %s\n' "$(head -c 300 "${BODY}" | tr '\n' ' ')" >&2
    elif [[ -s "${TMP_DIR}/curlerr" ]]; then
      printf '          curl: %s\n' "$(head -c 300 "${TMP_DIR}/curlerr" | tr '\n' ' ')" >&2
    fi
    FAIL=$((FAIL + 1))
  fi
}

# json_field FILE FIELD — extrae el primer valor string del campo indicado.
json_field() {
  sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" "$1" | head -n 1
}

echo "== OSINT Nexus · smoke test =="
echo "   base: ${BASE_URL}"
echo ""

# =====================================================================
# 1. GET /info (público)
# =====================================================================
echo "[1/7] GET /info (público)"
code="$(http GET "${BASE_URL}/info")"
check "GET /info" "200" "${code}"
if ! grep -q '"status"' "${BODY}" 2>/dev/null; then
  echo "  [FAIL]  /info sin campo 'status' del contrato" >&2
  FAIL=$((FAIL + 1))
fi

# =====================================================================
# 2. POST /auth/login (JWT humano)
# =====================================================================
echo "[2/7] POST /auth/login"
login_json="$(printf '{"username":"%s","password":"%s"}' "${SMOKE_USERNAME}" "${SMOKE_PASSWORD}")"
if [[ -n "${SMOKE_TOTP_CODE}" ]]; then
  login_json="$(printf '{"username":"%s","password":"%s","totp_code":"%s"}' \
    "${SMOKE_USERNAME}" "${SMOKE_PASSWORD}" "${SMOKE_TOTP_CODE}")"
fi
code="$(http POST "${BASE_URL}/auth/login" \
  -H 'Content-Type: application/json' -d "${login_json}")"
check "POST /auth/login" "200" "${code}"
ACCESS_TOKEN="$(json_field "${BODY}" access_token || true)"
if [[ -z "${ACCESS_TOKEN}" ]]; then
  echo "  [FAIL]  login sin 'access_token'" >&2
  FAIL=$((FAIL + 1))
fi

# =====================================================================
# 3. POST /context/preflight (M2M, X-API-Key)
# =====================================================================
echo "[3/7] POST /context/preflight (M2M)"
code="$(http POST "${BASE_URL}/context/preflight" \
  -H "X-API-Key: ${SMOKE_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"keywords":["osint","smoke","test"]}')"
check "POST /context/preflight (X-API-Key)" "200" "${code}"
PREFLIGHT_TOKEN="$(json_field "${BODY}" preflight_token || true)"
if [[ -z "${PREFLIGHT_TOKEN}" ]]; then
  echo "  [FAIL]  preflight sin 'preflight_token'" >&2
  FAIL=$((FAIL + 1))
fi

# =====================================================================
# 4. POST /ingest/informant (M2M + X-Preflight-Token) -> 202
# =====================================================================
echo "[4/7] POST /ingest/informant (con token) -> 202"
SOURCE_FILE="${TMP_DIR}/smoke-source.txt"
printf 'OSINT Nexus smoke test %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "${SOURCE_FILE}"
PAYLOAD='{"report_metadata":{"title":"smoke-test","source_type":"NEWS","source_url_or_origin":"https://example.invalid/smoke","reliability_rating":"C"},"extracted_triplets":[],"analytical_context_links":[]}'
code="$(http POST "${BASE_URL}/ingest/informant" \
  -H "X-API-Key: ${SMOKE_API_KEY}" \
  -H "X-Preflight-Token: ${PREFLIGHT_TOKEN}" \
  -F "file=@${SOURCE_FILE};type=text/plain;filename=smoke-test.txt" \
  -F "payload=${PAYLOAD}")"
check "POST /ingest/informant (202)" "202" "${code}"
if ! grep -q '"job_id"' "${BODY}" 2>/dev/null; then
  echo "  [FAIL]  ingest/informant sin 'job_id' del contrato" >&2
  FAIL=$((FAIL + 1))
fi

# =====================================================================
# 5. GET /graph/nodes (Bearer)
# =====================================================================
echo "[5/7] GET /graph/nodes (Bearer)"
code="$(http GET "${BASE_URL}/graph/nodes?limit=1" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}")"
check "GET /graph/nodes" "200" "${code}"

# =====================================================================
# 6. POST /search/rag (Bearer)
# =====================================================================
echo "[6/7] POST /search/rag (Bearer)"
code="$(http POST "${BASE_URL}/search/rag" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"query":"smoke test"}')"
check "POST /search/rag" "200" "${code}"

# =====================================================================
# 7. GET /coherence/alerts (Bearer)
# =====================================================================
echo "[7/7] GET /coherence/alerts (Bearer)"
code="$(http GET "${BASE_URL}/coherence/alerts" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}")"
check "GET /coherence/alerts" "200" "${code}"

# =====================================================================
# Resumen
# =====================================================================
echo ""
echo "== Resultado: ${PASS} OK · ${FAIL} FAIL =="
if [[ "${FAIL}" -gt 0 ]]; then
  exit 1
fi
exit 0
