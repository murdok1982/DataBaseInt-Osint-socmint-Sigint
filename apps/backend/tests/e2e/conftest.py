"""Verificación Cruzada E2E — ciclo simbiótico Analista ↔ Informante IA.

Autorizado por: **SPEC-00 §3** (ciclo), **SPEC-02 §1/§6** (Pre-Flight, ingesta y
Coherence Engine), **SPEC-03 §3–§7** (API) y **SPEC-04 §1/§4** (RBAC y 409).

El ciclo se recorre **in-process** con ``fastapi.testclient.TestClient`` y los
**dobles existentes** (sin Neo4j, sin Redis y sin servicios reales del Agente 2):

- Se reutiliza el ``FakeGraphRepository`` de la suite de seguridad (grafo,
  informes, expedientes, alertas, pre-vuelo) — ver ``tests/security/conftest.py``.
- Se le **añaden** los *escritores de coherencia* de SPEC-01 §3.2 que consume el
  ``CoherenceEngine`` (``corroborate``/``contradict``/``extend_analysis``/
  ``cite_analysis``/``answer_requirement``) y un **espía** de ``update_analysis``
  (Constitución, Reglas 7/9: el motor de coherencia jamás edita la tesis).
- La ingesta M2M corre con el ``NLPExtractor`` **real** (Agente 2) compuesto
  sobre esos dobles y un LLM guionizado, de modo que el ``CoherenceEngine`` real
  se ejercita de extremo a extremo a través de ``POST /ingest/informant``.
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

# Garantiza que `app` sea importable aunque pytest se invoque desde otro cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# La configuración de seguridad se fija ANTES de importar ``app.main``.
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-" + "a" * 48)
os.environ.setdefault("ENVIRONMENT", "test")

from app.api.deps import (  # noqa: E402
    get_api_key_store,
    get_graph_gateway,
    get_graphrag_engine,
    get_informant_extractor,
    get_job_registry,
    get_nlp_extractor,
    get_optional_graph_gateway,
    get_preflight_service,
    get_user_store,
)
from app.api.graph_gateway import GraphGateway  # noqa: E402
from app.core.apikeys import InMemoryApiKeyStore, hash_api_key  # noqa: E402
from app.core.config import reset_settings_cache  # noqa: E402
from app.core.jobs import InMemoryJobRegistry, reset_job_registry  # noqa: E402
from app.core.rbac import Role  # noqa: E402
from app.core.security import (  # noqa: E402
    ARGON2_AVAILABLE,
    create_access_token,
    reset_hasher_cache,
)
from app.core.users import InMemoryUserStore  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.entity_resolver import EntityResolver  # noqa: E402
from app.services.nlp_extractor import NLPExtractor  # noqa: E402
from app.services.preflight import PreflightService  # noqa: E402
from tests.security.conftest import (  # noqa: E402
    ADMIN_ID,
    ANALYST_ID,
    API_KEY,
    API_KEY_ID,
    INFORMANT_CLIENT_NAME,
    NODE_ID,
    REPORT_ID,
    VIEWER_ID,
    FakeGraphRagEngine,
    FakeGraphRepository as _SecurityFakeRepository,
    _user_records,
)

#: Severidades de alerta admitidas (SPEC-01 §2).
SEVERITIES: tuple[str, ...] = ("LOW", "MEDIUM", "CRITICAL")


class E2EGraphRepository(_SecurityFakeRepository):
    """Dobles de grafo/informes/expedientes **+ escritores de coherencia**.

    Extiende el ``FakeGraphRepository`` de la suite de seguridad (que ya cubre
    las capacidades que consume ``GraphGateway``) con las cinco operaciones de
    la Fase 2 que consume :class:`~app.services.coherence_engine.CoherenceEngine`
    y con un espía de la autoridad humana (``update_analysis``).
    """

    def __init__(self, storage_root: Path) -> None:
        super().__init__(storage_root)
        #: Espía: el motor de coherencia NUNCA debe invocar la edición humana.
        self.update_analysis_calls: list[tuple[str, dict[str, Any]]] = []
        self.corroborations: list[dict[str, Any]] = []
        self.contradictions: list[dict[str, Any]] = []
        self.extensions: list[dict[str, Any]] = []
        self.citations: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []

    # -----------------------------------------------------------------
    # Autoridad humana (espía de inmutabilidad — Reglas 7/9)
    # -----------------------------------------------------------------
    def update_analysis(
        self, analysis_id: str, patch: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        self.update_analysis_calls.append((analysis_id, dict(patch)))
        return super().update_analysis(analysis_id, patch)

    # -----------------------------------------------------------------
    # Escritores de coherencia (Fase 2 · SPEC-01 §3.2)
    # -----------------------------------------------------------------
    def create_alert(
        self,
        alert_type: str,
        description: str,
        *,
        status: str = "PENDING_ANALYST",
        analysis_id: Optional[str] = None,
        entity_id: Optional[str] = None,
        alert_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Crea una ``CoherenceAlert`` (el doble de seguridad no la exponía)."""
        call = {
            "id": alert_id or f"alert-{len(self.alerts) + 1}",
            "alert_type": alert_type,
            "status": status,
            "description": description,
            "analysis_id": analysis_id,
            "entity_id": entity_id,
        }
        self.alerts.append(call)
        return call

    def corroborate(
        self,
        report_id: str,
        analysis_id: str,
        *,
        confidence: float,
        justification: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        conf = float(confidence)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence debe estar en [0.0, 1.0].")
        record = {
            "type": "CORROBORATES",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "confidence": conf,
            "justification": justification,
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.corroborations.append(record)
        analysis = self.analyses.get(analysis_id)
        if analysis is not None:
            analysis["coherence_score"] = min(
                1.0, float(analysis.get("coherence_score") or 0.0) + conf
            )
        return record

    def contradict(
        self,
        report_id: str,
        analysis_id: str,
        *,
        conflict_detail: str,
        severity: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
        entity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if severity not in SEVERITIES:
            raise ValueError(f"severity '{severity}' no válida: {list(SEVERITIES)}.")
        # Regla 7/9: se crea la alerta; NUNCA se toca ``conclusion_body``.
        alert = self.create_alert(
            "CONTRADICTION",
            conflict_detail,
            analysis_id=analysis_id,
            entity_id=entity_id,
        )
        self.contradictions.append(
            {
                "type": "CONTRADICTS",
                "report_id": report_id,
                "source_report_id": report_id,
                "analysis_id": analysis_id,
                "conflict_detail": conflict_detail,
                "severity": severity,
                "exact_quote": exact_quote,
                "text_evidence_span": exact_quote,
                "page_number": page_number,
                "entity_id": entity_id,
                "alert_id": alert["id"],
            }
        )
        return alert

    def extend_analysis(
        self,
        report_id: str,
        analysis_id: str,
        *,
        new_angle_summary: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        record = {
            "type": "EXTENDS",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "new_angle_summary": new_angle_summary,
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.extensions.append(record)
        return record

    def answer_requirement(
        self,
        report_id: str,
        requirement_id: str,
        *,
        answer_summary: str,
        confidence: float,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        conf = float(confidence)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence debe estar en [0.0, 1.0].")
        record = {
            "type": "ANSWERS_REQUIREMENT",
            "report_id": report_id,
            "source_report_id": report_id,
            "requirement_id": requirement_id,
            "answer_summary": answer_summary,
            "confidence": conf,
            "status": "ANSWERED",
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.answers.append(record)
        return record

    def cite_analysis(
        self,
        report_id: str,
        analysis_id: str,
        *,
        relevance_score: float,
        rationale: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        record = {
            "type": "CITES_ANALYSIS",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "relevance_score": float(relevance_score),
            "rationale": rationale,
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.citations.append(record)
        return record


class ScriptedLLM:
    """Doble LLM (SPEC-02 §1 Fase 1) guionizado por el propio test.

    El test fija ``links`` (y opcionalmente ``triplets``) antes de cada ingesta
    del Informante IA; el pipeline real (nuevas líneas de SPEC-02 §1) los valida
    y los entrega al ``CoherenceEngine``.
    """

    def __init__(self) -> None:
        self.triplets: list[dict[str, Any]] = []
        self.links: list[dict[str, Any]] = []
        self.calls: int = 0

    def set_payload(
        self,
        *,
        triplets: Optional[list[dict[str, Any]]] = None,
        links: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self.triplets = list(triplets or [])
        self.links = list(links or [])

    def extract(  # noqa: ANN401 - firma del Protocol LLMClient
        self, chunk: Any, *, context: Any = None
    ) -> dict[str, Any]:
        self.calls += 1
        return {
            "extracted_triplets": copy.deepcopy(self.triplets),
            "analytical_context_links": copy.deepcopy(self.links),
        }


# =====================================================================
# Entorno de pruebas
# =====================================================================
@pytest.fixture(scope="session")
def storage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Directorio de informes (``STORAGE_RAW_DIR``) con un PDF de prueba."""
    root = tmp_path_factory.mktemp("e2e_storage") / "raw_reports"
    root.mkdir(parents=True, exist_ok=True)
    (root / "informe-1.pdf").write_bytes(b"%PDF-1.4 informe de prueba e2e\n")
    return root


@pytest.fixture(autouse=True)
def _e2e_env(monkeypatch: pytest.MonkeyPatch, storage_dir: Path) -> Iterator[None]:
    """Aísla la configuración de seguridad de cada test (sin Neo4j/Redis)."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-" + "a" * 48)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("STORAGE_RAW_DIR", str(storage_dir))
    monkeypatch.delenv("OSINT_USERS_JSON", raising=False)
    monkeypatch.delenv("OSINT_ADMIN_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("OSINT_API_KEYS_JSON", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    reset_settings_cache()
    reset_hasher_cache()
    reset_job_registry()
    yield
    reset_settings_cache()
    reset_hasher_cache()
    reset_job_registry()


# =====================================================================
# Dobles
# =====================================================================
@pytest.fixture
def repo(storage_dir: Path) -> E2EGraphRepository:
    return E2EGraphRepository(storage_dir)


@pytest.fixture
def engine() -> FakeGraphRagEngine:
    return FakeGraphRagEngine(with_citations=True)


@pytest.fixture
def llm() -> ScriptedLLM:
    return ScriptedLLM()


@pytest.fixture
def users() -> InMemoryUserStore:
    return InMemoryUserStore(users=_user_records())


@pytest.fixture
def api_keys() -> InMemoryApiKeyStore:
    """Almacén M2M con una API Key válida (hash Argon2id real)."""
    if not ARGON2_AVAILABLE:
        pytest.skip("argon2-cffi no instalado: SPEC-04 §2 requiere Argon2id.")
    store = InMemoryApiKeyStore()
    store.add(INFORMANT_CLIENT_NAME, hash_api_key(API_KEY), key_id=API_KEY_ID)
    return store


@pytest.fixture
def preflight(repo: E2EGraphRepository) -> PreflightService:
    """``PreflightService`` real sobre el repositorio doble (SPEC-02 §1)."""
    return PreflightService(repo, secret="test-e2e-preflight-secret")


@pytest.fixture
def job_registry() -> InMemoryJobRegistry:
    return InMemoryJobRegistry()


@pytest.fixture
def informant_extractor(
    llm: ScriptedLLM, repo: E2EGraphRepository, preflight: PreflightService
) -> NLPExtractor:
    """``NLPExtractor`` **real** (Agente 2) sobre dobles + ``CoherenceEngine``."""
    return NLPExtractor(
        llm,
        EntityResolver(repo),
        repository=repo,
        preflight=preflight,
    )


@pytest.fixture
def app(
    users: InMemoryUserStore,
    repo: E2EGraphRepository,
    engine: FakeGraphRagEngine,
    job_registry: InMemoryJobRegistry,
    api_keys: InMemoryApiKeyStore,
    preflight: PreflightService,
    informant_extractor: NLPExtractor,
) -> Iterator[Any]:
    """App FastAPI con todos los servicios externos sustituidos por dobles."""
    application = create_app()
    application.dependency_overrides[get_user_store] = lambda: users
    application.dependency_overrides[get_graph_gateway] = lambda: GraphGateway(repo)
    application.dependency_overrides[get_optional_graph_gateway] = lambda: GraphGateway(
        repo
    )
    application.dependency_overrides[get_job_registry] = lambda: job_registry
    application.dependency_overrides[get_nlp_extractor] = lambda: informant_extractor
    application.dependency_overrides[get_informant_extractor] = (
        lambda: informant_extractor
    )
    application.dependency_overrides[get_graphrag_engine] = lambda: engine
    application.dependency_overrides[get_api_key_store] = lambda: api_keys
    application.dependency_overrides[get_preflight_service] = lambda: preflight
    try:
        yield application
    finally:
        application.dependency_overrides.clear()


@pytest.fixture
def client(app: Any) -> Any:
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture
def tokens() -> dict[Role, str]:
    """Un access token JWT por rol humano (SPEC-04 §2: 30 min)."""
    return {
        Role.ADMIN: create_access_token(ADMIN_ID, Role.ADMIN.value),
        Role.ANALYST: create_access_token(ANALYST_ID, Role.ANALYST.value),
        Role.VIEWER: create_access_token(VIEWER_ID, Role.VIEWER.value),
    }


__all__ = [
    "E2EGraphRepository",
    "ScriptedLLM",
    "API_KEY",
    "NODE_ID",
    "REPORT_ID",
]
