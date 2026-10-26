# PostgreSQL 16 + pgvector + PostGIS para OSINT Nexus v3.1.
#
# La imagen oficial de pgvector no trae PostGIS; se instala el paquete de PGDG
# para la misma versión mayor. No se publica ningún puerto (Constitución R4).
FROM pgvector/pgvector:pg16

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        postgresql-16-postgis-3 \
        postgresql-16-postgis-3-scripts \
    && rm -rf /var/lib/apt/lists/*

# El contrato v3.1 crea las extensiones; el entrypoint sólo ejecuta el DDL.
# Para una base v3.0 existente, aplicar a mano:
#   psql "$POSTGRES_DSN" -v ON_ERROR_STOP=1 -f /srv/osint/contracts/v3/db-contract-v3.1.sql
