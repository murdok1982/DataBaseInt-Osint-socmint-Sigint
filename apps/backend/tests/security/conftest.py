"""Verificación Cruzada de seguridad/RBAC (Constitución, Regla 5).

Autorizado por: **SPEC-04 §1/§2/§4** (matriz de 4 actores, API Keys M2M, Argon2id)
y **SPEC-03 §3–§6** (rol mínimo por endpoint; 409 ``CONFLICT``).

Los tests corren **sin Neo4j, sin Redis y sin servicios reales del Agente 2**: el
directorio de operadores, el almacén de API Keys M2M, el gateway de grafo, el
servicio de Pre-Flight y los servicios de NLP/GraphRAG se sustituyen por dobles
mediante ``app.dependency_overrides``.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator, Optional

import pytest

# Garantiza que `app` sea importable aunque pytest se invoque desde otro cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# La configuración de seguridad del proceso de test se fija ANTES de importar
# ``app.main`` (que construye la app) para no arrancar con clave efímera.
import os  # noqa: E402

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
from app.core.login_guard import reset_login_guards  # noqa: E402
from app.core.rbac import Role  # noqa: E402
from app.core.security import (  # noqa: E402
    ARGON2_AVAILABLE,
    create_access_token,
    hash_password,
    reset_hasher_cache,
    totp_now,
)
from app.core.users import InMemoryUserStore, UserRecord  # noqa: E402
from app.db.repository import NodeLockedError  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.preflight import PreflightService  # noqa: E402

NODE_ID = "11111111-1111-1111-1111-111111111111"
REPORT_ID = "22222222-2222-2222-2222-222222222222"
ANALYSIS_ID = "33333333-3333-3333-3333-333333333333"
REQUIREMENT_ID = "44444444-4444-4444-4444-444444444444"
ALERT_ID = "55555555-5555-5555-5555-555555555555"
JOB_ID = "job-1"

ADMIN_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
ANALYST_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
VIEWER_ID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
API_KEY_ID = "dddddddd-dddd-dddd-dddd-dddddddddddd"

#: Contraseña de laboratorio usada solo en tests (nunca en despliegue).
TEST_PASSWORD = "test-password-arg2id"

#: Secreto TOTP de laboratorio del ADMIN (SPEC-04 §2: 2FA obligatorio para ADMIN).
ADMIN_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"

#: API Key M2M de laboratorio (solo tests). El almacén guarda su hash Argon2id.
API_KEY = "test-m2m-api-key-informant"
INFORMANT_CLIENT_NAME = "informant-agent"


# =====================================================================
# Entorno de pruebas (SPEC-04 §2: JWT 15 min, storage confinado)
# =====================================================================
@pytest.fixture(scope="session")
def storage_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Directorio de informes (``STORAGE_RAW_DIR``) con un PDF de prueba."""
    root = tmp_path_factory.mktemp("storage") / "raw_reports"
    root.mkdir(parents=True, exist_ok=True)
    (root / "informe-1.pdf").write_bytes(b"%PDF-1.4 informe de prueba\n")
    return root


@pytest.fixture(autouse=True)
def _security_env(monkeypatch: pytest.MonkeyPatch, storage_dir: Path) -> Iterator[None]:
    """Aísla la configuración de seguridad de cada test."""
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-" + "a" * 48)
    monkeypatch.setenv("JWT_ALGORITHM", "HS256")
    monkeypatch.setenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15")
    monkeypatch.setenv("REFRESH_TOKEN_EXPIRE_DAYS", "7")
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("STORAGE_RAW_DIR", str(storage_dir))
    monkeypatch.delenv("OSINT_USERS_JSON", raising=False)
    monkeypatch.delenv("OSINT_ADMIN_PASSWORD_HASH", raising=False)
    # 2FA de ADMIN con su valor normativo (no se relaja en los tests).
    monkeypatch.delenv("ADMIN_TOTP_REQUIRED", raising=False)
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    # Sin API Keys por defecto (SPEC-04 §4): el almacén real queda vacío.
    monkeypatch.delenv("OSINT_API_KEYS_JSON", raising=False)
    # Registro de jobs en memoria (SPEC-02 §5): sin Redis en los tests.
    monkeypatch.delenv("REDIS_URL", raising=False)
    reset_settings_cache()
    reset_hasher_cache()
    reset_job_registry()
    reset_login_guards()
    yield
    reset_settings_cache()
    reset_hasher_cache()
    reset_job_registry()
    reset_login_guards()


# =====================================================================
# Dobles de prueba (sin Neo4j / sin servicios reales del Agente 2)
# =====================================================================
def _graph_node(
    node_id: str = NODE_ID,
    name: str = "Juan Pérez",
    *,
    locked: bool = False,
) -> dict[str, Any]:
    return {
        "id": node_id,
        "label": "Person",
        "canonical_name": name,
        "aliases": ["J. Pérez"],
        "risk_score": 0.7,
        "locked_by_analyst": locked,
        "attributes": {"source_report_id": REPORT_ID},
    }


def _analysis(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": ANALYSIS_ID,
        "code": "AN-0001",
        "title": "Expediente de prueba",
        "thesis_summary": "Juan Pérez controla Acme Ltd.",
        "conclusion_body": "",
        "status": "ACTIVE",
        "author_analyst_id": ANALYST_ID,
        "coherence_score": 0.0,
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }
    record.update(overrides)
    return record


def _requirement(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": REQUIREMENT_ID,
        "question": "¿Quién controla Acme Ltd.?",
        "priority": "HIGH",
        "status": "OPEN",
        "parent_analysis_id": ANALYSIS_ID,
        "target_entity_id": NODE_ID,
    }
    record.update(overrides)
    return record


def _alert(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "id": ALERT_ID,
        "alert_type": "CONTRADICTION",
        "status": "PENDING_ANALYST",
        "description": "Contradicción detectada con el análisis activo.",
        "analysis_id": ANALYSIS_ID,
        "entity_id": NODE_ID,
        "created_at": "2026-01-01T00:00:00Z",
    }
    record.update(overrides)
    return record


class FakeGraphRepository:
    """Implementa el puerto que consume :class:`GraphGateway` (Agente 1)."""

    def __init__(self, storage_root: Path) -> None:
        self.storage_root = storage_root
        self.nodes: dict[str, dict[str, Any]] = {NODE_ID: _graph_node()}
        self.deleted: list[str] = []
        self.updates: list[tuple[str, dict[str, Any]]] = []
        self.analyses: dict[str, dict[str, Any]] = {ANALYSIS_ID: _analysis()}
        self.requirements: list[dict[str, Any]] = [_requirement()]
        self.alerts: list[dict[str, Any]] = [_alert()]
        self.canonical: list[dict[str, Any]] = [
            {"id": NODE_ID, "label": "Person", "canonical_name": "Juan Pérez"}
        ]

    # --- grafo (SPEC-03 §3) ------------------------------------------
    def list_nodes(
        self,
        *,
        label: Optional[str] = None,
        q: Optional[str] = None,
        from_: Optional[str] = None,
        to: Optional[str] = None,
        min_confidence: Optional[float] = None,
        limit: int = 50,
        cursor: Optional[str] = None,
    ) -> dict[str, Any]:
        items = [
            node
            for node in self.nodes.values()
            if label is None or node["label"] == label
        ]
        if q:
            needle = q.lower()
            items = [
                node
                for node in items
                if needle in json.dumps(node, ensure_ascii=False).lower()
            ]
        return {"items": items[:limit], "next_cursor": None}

    def get_node(self, node_id: str) -> Optional[dict[str, Any]]:
        return self.nodes.get(node_id)

    def update_node(
        self, node_id: str, payload: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        node = self.nodes.get(node_id)
        if node is None:
            return None
        # Inmutabilidad analítica (Constitución Regla 2): un nodo bloqueado no se
        # edita; la API lo traduce a 409 (SPEC-03 §4).
        if node.get("locked_by_analyst"):
            raise NodeLockedError(node_id)
        self.updates.append((node_id, dict(payload)))
        for key, value in payload.items():
            if key == "aliases":
                node["aliases"] = list(value)
            elif key == "attributes":
                node["attributes"] = dict(value)
            elif key == "canonical_name":
                node["canonical_name"] = value
        return node

    def delete_node(self, node_id: str) -> None:
        self.deleted.append(node_id)
        self.nodes.pop(node_id, None)

    def set_node_locked(
        self, node_id: str, locked: bool = True
    ) -> Optional[dict[str, Any]]:
        node = self.nodes.get(node_id)
        if node is None:
            return None
        node["locked_by_analyst"] = bool(locked)
        return node

    def list_edges(
        self, *, min_confidence: Optional[float] = None, limit: int = 50
    ) -> dict[str, Any]:
        return {
            "items": [
                {
                    "id": "edge-1",
                    "from": NODE_ID,
                    "to": REPORT_ID,
                    "type": "MENTIONED_IN",
                    "confidence": 0.9,
                    "source_report_id": REPORT_ID,
                    "exact_quote": "controla la sociedad",
                    "text_evidence_span": "controla la sociedad",
                    "page_number": 1,
                    "first_seen": "2026-01-01T00:00:00Z",
                    "last_seen": "2026-02-01T00:00:00Z",
                }
            ][:limit]
        }

    def neighborhood(self, node_id: str, depth: int) -> dict[str, Any]:
        node = self.nodes.get(node_id)
        return {"nodes": [node] if node else [], "edges": []}

    # --- informes (SPEC-03 §4) ----------------------------------------
    def get_report(self, report_id: str) -> Optional[dict[str, Any]]:
        if report_id != REPORT_ID:
            return None
        return {
            "id": REPORT_ID,
            "title": "Informe de prueba",
            "file_path": str(self.storage_root / "informe-1.pdf"),
            "ingested_at": "2026-01-01T00:00:00Z",
            "author_id": ANALYST_ID,
        }

    def get_report_file_path(self, report_id: str) -> str:
        return str(self.storage_root / "informe-1.pdf")

    def get_report_chunks(self, report_id: str) -> dict[str, Any]:
        return {
            "items": [
                {
                    "chunk_id": "chunk-1",
                    "page_number": 1,
                    "char_start": 0,
                    "char_end": 21,
                    "text": "controla la sociedad",
                }
            ]
        }

    # --- Pre-Flight (SPEC-02 §1 Fase 0) -------------------------------
    def active_analyses(self, keywords: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "id": analysis["id"],
                "code": analysis.get("code"),
                "thesis_summary": analysis.get("thesis_summary"),
            }
            for analysis in self.analyses.values()
            if analysis.get("status") == "ACTIVE"
        ]

    def list_open_requirements(self) -> list[dict[str, Any]]:
        return [
            {
                "id": req["id"],
                "question": req.get("question"),
                "priority": req.get("priority"),
            }
            for req in self.requirements
            if req.get("status") == "OPEN"
        ]

    def canonical_entities(self) -> list[dict[str, Any]]:
        return [dict(entity) for entity in self.canonical]

    # --- expedientes analíticos (SPEC-03 §6) --------------------------
    def create_analysis(
        self,
        title: str,
        thesis_summary: str,
        *,
        code: Optional[str] = None,
        author_analyst_id: Optional[str] = None,
        conclusion_body: Optional[str] = None,
        status: str = "ACTIVE",
        analysis_id: Optional[str] = None,
        extra: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        new_id = analysis_id or str(uuid.uuid4())
        record = _analysis(
            id=new_id,
            code=code or f"AN-{new_id[:8].upper()}",
            title=title,
            thesis_summary=thesis_summary,
            conclusion_body=conclusion_body,
            status=status,
            author_analyst_id=author_analyst_id,
        )
        if extra:
            record.update(extra)
        self.analyses[new_id] = record
        return dict(record)

    def get_analysis(self, analysis_id: str) -> Optional[dict[str, Any]]:
        record = self.analyses.get(analysis_id)
        return dict(record) if record else None

    def list_analyses(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        return [
            dict(record)
            for record in self.analyses.values()
            if status is None or record.get("status") == status
        ]

    def list_requirements(self, analysis_id: str) -> list[dict[str, Any]]:
        return [
            dict(req)
            for req in self.requirements
            if req.get("parent_analysis_id") == analysis_id
        ]

    def update_analysis(
        self, analysis_id: str, patch: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        record = self.analyses.get(analysis_id)
        if record is None:
            return None
        for key in ("title", "thesis_summary", "conclusion_body", "status"):
            if patch.get(key) is not None:
                record[key] = patch[key]
        record["updated_at"] = "2026-01-02T00:00:00Z"
        return dict(record)

    def create_requirement(
        self,
        question: str,
        *,
        priority: str = "MEDIUM",
        status: str = "OPEN",
        parent_analysis_id: Optional[str] = None,
        target_entity_id: Optional[str] = None,
        requirement_id: Optional[str] = None,
    ) -> dict[str, Any]:
        new_id = requirement_id or str(uuid.uuid4())
        record = _requirement(
            id=new_id,
            question=question,
            priority=priority,
            status=status,
            parent_analysis_id=parent_analysis_id,
            target_entity_id=target_entity_id,
        )
        self.requirements.append(record)
        return dict(record)

    # --- coherencia (SPEC-03 §6) --------------------------------------
    def list_alerts(self, status: Optional[str] = None) -> list[dict[str, Any]]:
        return [
            dict(alert)
            for alert in self.alerts
            if status is None or alert.get("status") == status
        ]

    def resolve_alert(
        self, alert_id: str, status: str, note: Optional[str] = None
    ) -> Optional[dict[str, Any]]:
        for alert in self.alerts:
            if alert["id"] == alert_id:
                alert["status"] = status
                if note:
                    alert["resolution_note"] = note
                return dict(alert)
        return None


class FakeNlpExtractor:
    """Doble de ``app.services.nlp_extractor.NLPExtractor`` (SPEC-02 §5).

    Réplica de la interfaz real del Agente 2: ``ingest(report_id, pages) ->
    ReportOutcome`` con atributo ``pending`` (tripletas en revisión humana) y
    ``ingest_informant(..., preflight_token=...)`` para la vía M2M (SPEC-02 §1).
    """

    def __init__(self, pending_review: int = 1, fail: bool = False) -> None:
        self.pending_review = pending_review
        self.fail = fail
        self.received: list[dict[str, Any]] = []

    def ingest(
        self, report_id: str, pages: Any, *, reprocess: bool = False
    ) -> Any:
        if self.fail:
            raise RuntimeError("pipeline NLP caído (doble de prueba)")
        page_list = list(pages) if not isinstance(pages, str) else [pages]
        self.received.append(
            {
                "report_id": str(report_id),
                "pages": len(page_list),
                "chars": sum(len(page) for page in page_list),
                "reprocess": reprocess,
                "channel": "human",
            }
        )
        return SimpleNamespace(
            report_id=str(report_id),
            pending=[{} for _ in range(self.pending_review)],
        )

    #: Alias declarativo del pipeline real (``NLPExtractor.process = ingest``).
    process = ingest

    def ingest_informant(
        self,
        report_id: str,
        pages: Any,
        *,
        preflight_token: str,
        metadata: Optional[dict[str, Any]] = None,
        reprocess: bool = False,
    ) -> Any:
        """Vía M2M: exige un ``preflight_token`` vigente (Constitución Regla 3)."""
        if self.fail:
            raise RuntimeError("pipeline NLP caído (doble de prueba)")
        page_list = list(pages) if not isinstance(pages, str) else [pages]
        self.received.append(
            {
                "report_id": str(report_id),
                "pages": len(page_list),
                "chars": sum(len(page) for page in page_list),
                "reprocess": reprocess,
                "channel": "informant",
                "preflight_token": preflight_token,
                "metadata": metadata,
            }
        )
        return SimpleNamespace(
            report_id=str(report_id),
            pending=[{} for _ in range(self.pending_review)],
        )


class FakeGraphRagEngine:
    """Doble de ``app.services.graphrag_engine.GraphRagEngine`` (SPEC-03 §5).

    Devuelve un objeto con ``as_dict()`` como el ``RagAnswer`` real.
    """

    def __init__(self, with_citations: bool = True) -> None:
        self.with_citations = with_citations
        self.queries: list[str] = []

    def answer(self, query: str) -> Any:
        self.queries.append(query)
        citations = (
            [
                {
                    "report_id": REPORT_ID,
                    "page_number": 1,
                    "exact_quote": "controla la sociedad",
                }
            ]
            if self.with_citations
            else []
        )
        return SimpleNamespace(
            as_dict=lambda: {
                "answer": f"Respuesta a: {query}",
                "subgraph": {"nodes": [_graph_node()], "edges": []},
                "citations": citations,
            }
        )

    def analysis_only(self, query: str) -> Any:
        """Cita solo de análisis humano (``analysis_id`` + ``code``)."""
        self.queries.append(query)
        return SimpleNamespace(
            as_dict=lambda: {
                "answer": f"Respuesta (análisis) a: {query}",
                "subgraph": {"nodes": [], "edges": []},
                "citations": [
                    {
                        "analysis_id": ANALYSIS_ID,
                        "code": "AN-0001",
                        "exact_quote": "tesis del analista",
                    }
                ],
            }
        )


# =====================================================================
# Fixtures
# =====================================================================
def _user_records() -> list[UserRecord]:
    """Tres operadores humanos, uno por rol (SPEC-04 §1)."""
    return [
        UserRecord(
            id=ADMIN_ID,
            username="admin",
            password_hash="not-used-in-matrix-tests",
            role=Role.ADMIN,
        ),
        UserRecord(
            id=ANALYST_ID,
            username="analyst",
            password_hash="not-used-in-matrix-tests",
            role=Role.ANALYST,
        ),
        UserRecord(
            id=VIEWER_ID,
            username="viewer",
            password_hash="not-used-in-matrix-tests",
            role=Role.VIEWER,
        ),
    ]


@pytest.fixture
def users() -> InMemoryUserStore:
    """Directorio de operadores en memoria (tres roles humanos)."""
    return InMemoryUserStore(users=_user_records())


@pytest.fixture
def fake_repo(storage_dir: Path) -> FakeGraphRepository:
    return FakeGraphRepository(storage_dir)


@pytest.fixture
def fake_extractor() -> FakeNlpExtractor:
    return FakeNlpExtractor()


@pytest.fixture
def fake_engine() -> FakeGraphRagEngine:
    return FakeGraphRagEngine()


@pytest.fixture
def job_registry() -> InMemoryJobRegistry:
    return InMemoryJobRegistry()


@pytest.fixture
def api_keys() -> InMemoryApiKeyStore:
    """Almacén M2M con una API Key válida (hash Argon2id real)."""
    if not ARGON2_AVAILABLE:
        pytest.skip("argon2-cffi no instalado: SPEC-04 §2 requiere Argon2id.")
    store = InMemoryApiKeyStore()
    store.add(
        INFORMANT_CLIENT_NAME,
        hash_api_key(API_KEY),
        key_id=API_KEY_ID,
    )
    return store


@pytest.fixture
def preflight(fake_repo: FakeGraphRepository) -> PreflightService:
    """``PreflightService`` real sobre el repositorio doble (SPEC-02 §1)."""
    return PreflightService(fake_repo, secret="test-preflight-secret")


@pytest.fixture
def app(
    users: InMemoryUserStore,
    fake_repo: FakeGraphRepository,
    fake_extractor: FakeNlpExtractor,
    fake_engine: FakeGraphRagEngine,
    job_registry: InMemoryJobRegistry,
    api_keys: InMemoryApiKeyStore,
    preflight: PreflightService,
) -> Iterator[Any]:
    """App FastAPI con todos los servicios externos sustituidos por dobles."""
    application = create_app()
    application.dependency_overrides[get_user_store] = lambda: users
    application.dependency_overrides[get_graph_gateway] = lambda: GraphGateway(fake_repo)
    application.dependency_overrides[get_optional_graph_gateway] = lambda: GraphGateway(
        fake_repo
    )
    application.dependency_overrides[get_job_registry] = lambda: job_registry
    application.dependency_overrides[get_nlp_extractor] = lambda: fake_extractor
    application.dependency_overrides[get_informant_extractor] = lambda: fake_extractor
    application.dependency_overrides[get_graphrag_engine] = lambda: fake_engine
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
    """Un access token JWT por rol humano (SPEC-04 §2: 15 min)."""
    return {
        Role.ADMIN: create_access_token(ADMIN_ID, Role.ADMIN.value),
        Role.ANALYST: create_access_token(ANALYST_ID, Role.ANALYST.value),
        Role.VIEWER: create_access_token(VIEWER_ID, Role.VIEWER.value),
    }


def auth_header(token: str) -> dict[str, str]:
    """Cabecera ``Authorization: Bearer`` (SPEC-03 §1)."""
    return {"Authorization": f"Bearer {token}"}


def api_key_header(key: str = API_KEY) -> dict[str, str]:
    """Cabecera M2M ``X-API-Key`` (SPEC-04 §5.2)."""
    return {"X-API-Key": key}


def login_payload(username: str, password: str = TEST_PASSWORD) -> dict[str, str]:
    """Cuerpo de ``/auth/login``; añade el TOTP vigente si el operador es ADMIN."""
    payload = {"username": username, "password": password}
    if username == "admin":
        payload["totp_code"] = totp_now(ADMIN_TOTP_SECRET)
    return payload


@pytest.fixture
def hashed_users() -> InMemoryUserStore:
    """Directorio con hashes Argon2id reales (tests de ``/auth``)."""
    from app.core.security import ARGON2_AVAILABLE

    if not ARGON2_AVAILABLE:
        pytest.skip("argon2-cffi no instalado: SPEC-04 §2 requiere Argon2id.")
    password_hash = hash_password(TEST_PASSWORD)
    return InMemoryUserStore(
        users=[
            UserRecord(
                id=ADMIN_ID,
                username="admin",
                password_hash=password_hash,
                role=Role.ADMIN,
                totp_secret=ADMIN_TOTP_SECRET,
            ),
            UserRecord(
                id=ANALYST_ID,
                username="analyst",
                password_hash=password_hash,
                role=Role.ANALYST,
            ),
            UserRecord(
                id=VIEWER_ID,
                username="viewer",
                password_hash=password_hash,
                role=Role.VIEWER,
            ),
        ]
    )


__all__ = [
    "NODE_ID",
    "REPORT_ID",
    "ANALYSIS_ID",
    "REQUIREMENT_ID",
    "ALERT_ID",
    "JOB_ID",
    "ADMIN_ID",
    "ANALYST_ID",
    "VIEWER_ID",
    "API_KEY_ID",
    "TEST_PASSWORD",
    "API_KEY",
    "INFORMANT_CLIENT_NAME",
    "FakeGraphRepository",
    "FakeNlpExtractor",
    "FakeGraphRagEngine",
    "auth_header",
    "api_key_header",
]
