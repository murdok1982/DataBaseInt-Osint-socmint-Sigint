# =====================================================================
# informant-agent.Dockerfile — Imagen del Informante IA (principal M2M
# ``AI_INFORMANT``). Autorizado por SPEC-04 §2: el Informant Agent vive en la
# red interna ``internal_intel_net`` y se comunica con el backend SOLO por la
# API (nunca toca Neo4j/Redis — Regla 4).
#
# Contexto de build: ``apps/informant-agent`` (zona Agente 5, solo lectura).
# El Dockerfile se mantiene en ``/deploy`` (zona Agente 3) para no escribir en
# la zona del Agente 5; se referencia desde ``docker-compose.prod.yml``.
#
# El contrato sellado (``contracts/extraction-schema.json``) NO se copia en la
# imagen: se monta en ``/contracts`` en tiempo de ejecución (solo lectura).
# =====================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencias primero (mejor caché de capas).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Código del agente.
COPY . .

# Usuario sin privilegios (menor superficie de ataque).
RUN useradd --create-home --uid 10001 informant \
    && chown -R informant:informant /app
USER informant

# CLI del Informante IA (``python -m informant_agent``).
ENTRYPOINT ["python", "-m", "informant_agent"]
CMD ["--help"]
