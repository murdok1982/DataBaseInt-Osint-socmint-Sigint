# Backend — OSINT Nexus (API + motor de ingesta + worker Celery)
# Deriva de SPEC-00/02/03/04. El Agente 3 mantiene este archivo.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Raíz de despliegue. El código vive en <ROOT>/apps/backend y el contrato
# sellado se MONTA en <ROOT>/contracts (solo lectura, ver docker-compose).
#
# ¿Por qué esta ruta y no ``/app``? ``app/schemas/extraction.py`` y
# ``app/db/repository.py`` resuelven el contrato con
# ``Path(__file__).resolve().parents[4] / "contracts"``. Ese cálculo asume el
# layout del repositorio (<ROOT>/apps/backend/app/...). Con ``WORKDIR /app`` la
# ruta del módulo queda en /app/app/schemas/... y ``parents[4]`` lanza
# IndexError; con <ROOT>/apps/backend el contrato resuelve a
# /srv/osint/contracts/* (montado read-only). No cambiar sin actualizar el
# código de los Agentes 1/2.
ENV DEPLOY_ROOT=/srv/osint \
    PYTHONPATH=/srv/osint/apps/backend

WORKDIR /srv/osint/apps/backend

# Dependencias del sistema mínimas (curl para el smoke test / healthchecks).
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# requirements.txt primero: mejor caché de capas.
COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

EXPOSE 8000
# Usuario no root (hardening, SPEC-04 §3).
RUN useradd -m appuser && chown -R appuser /srv/osint
USER appuser

# El contrato se monta en /srv/osint/contracts en tiempo de ejecución (ro).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
