#!/bin/sh
# Asigna contraseña al rol de lectura de la API v3.1.
#
# El contrato crea ``api_reader`` sin contraseña. El backend v3.1 se conecta
# con ese rol para que RLS se aplique de verdad; el owner queda sólo para
# administración y migraciones. Se ejecuta una vez, al inicializar el volumen.
set -eu

: "${API_DB_PASSWORD:?define API_DB_PASSWORD en .env}"

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set=api_password="$API_DB_PASSWORD" <<'EOSQL'
ALTER ROLE api_reader LOGIN PASSWORD :'api_password';
EOSQL
