"""AI_INFORMANT — Agente 5 de OSINT Nexus.

Servicio autónomo de recolección OSINT (M2M). **Nunca** escribe en el grafo ni en
la base de datos: solo llama a la API M2M.

Protocolo (`.antigravity/agent-5-ai-informant.prompt.md`):

1. ``POST /api/v1/context/preflight`` — **nunca operar a ciegas** (Regla 3).
2. Extracción guiada por contexto, validada 100% contra
   ``contracts/extraction-schema.json``.
3. ``POST /api/v1/ingest/informant`` con ``X-API-Key`` + ``X-Preflight-Token``.

Referencias: SPEC-02 (pipeline/coherencia), SPEC-03 (API M2M), SPEC-04 (seguridad).
"""

from __future__ import annotations

from .api_client import (
    API_PREFIX,
    HttpInformantApiClient,
    InformantApi,
    InformantApiError,
    PreflightRequired,
)
from .contract import (
    ExtractionContractError,
    load_sealed_schema,
    validate_extraction,
    validate_triplet,
)
from .extractor import ContextGuidedExtractor
from .coherence import LinkAnalyzer
from .models import (
    AnalyticalContextLink,
    Entity,
    ExtractionResult,
    IngestJob,
    Predicate,
    PreflightResponse,
    Provenance,
    ReportMetadata,
    Triplet,
)
from .pipeline import InformantPipeline, RunResult

__all__ = [
    "API_PREFIX",
    "AnalyticalContextLink",
    "ContextGuidedExtractor",
    "Entity",
    "ExtractionContractError",
    "ExtractionResult",
    "HttpInformantApiClient",
    "InformantApi",
    "InformantApiError",
    "InformantPipeline",
    "IngestJob",
    "LinkAnalyzer",
    "Predicate",
    "PreflightRequired",
    "PreflightResponse",
    "Provenance",
    "ReportMetadata",
    "RunResult",
    "Triplet",
    "load_sealed_schema",
    "validate_extraction",
    "validate_triplet",
]

__version__ = "2.0.0"
