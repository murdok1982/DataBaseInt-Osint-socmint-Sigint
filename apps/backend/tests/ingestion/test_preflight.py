"""Tests — Pre-Flight Context Check y prohibición de ingesta ciega.

Autorizado por: **SPEC-02 §1 Fase 0** (contexto: análisis activos + RFIs +
diccionario canónico), **SPEC-02 §5** (``app/services/preflight.py``) y
**SPEC-03 §5** (``/ingest/informant`` sin token → 409; ``X-Preflight-Token``).

Constitución Regla 3: el Informante IA **nunca** ingesta sin un pre-vuelo vigente.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.entity_resolver import EntityResolver
from app.services.nlp_extractor import (
    FakeLLMClient,
    NLPExtractor,
    PreflightRequiredError,
)
from app.services.preflight import (
    TOKEN_HEADER,
    PreflightService,
    clean_keywords,
    derive_keywords,
    normalize_name,
)
from tests.ingestion.fakes import FakeGraphRepository

REPORT_ID = "77777777-7777-4777-8777-777777777777"
PAGE = "Acme Corp financió a Juan Pérez para adquirir equipos de cifrado."


class Clock:
    """Reloj determinista inyectable (SPEC-02 §5)."""

    def __init__(self) -> None:
        self.value = datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.value

    def advance(self, seconds: int) -> None:
        self.value = self.value + timedelta(seconds=seconds)


def make_repo() -> FakeGraphRepository:
    repo = FakeGraphRepository()
    repo.seed_analysis(
        analysis_id="an-1",
        code="AN-ACME",
        thesis_summary="Acme Corp financia adquisiciones de criptografía.",
    )
    repo.seed_analysis(
        analysis_id="an-2",
        code="AN-OTHER",
        thesis_summary="Investigación sobre logística portuaria.",
    )
    repo.seed_requirement(requirement_id="req-1", question="¿Quién financia Acme?")
    repo.seed_node("Organization", "org-1", "Acme Corp", registration_id="REG-1")
    repo.seed_node("Person", "person-1", "Juan Pérez")
    return repo


# ---------------------------------------------------------------------
# Fase 0 — contexto
# ---------------------------------------------------------------------
def test_build_context_returns_analyses_requirements_and_entities():
    repo = make_repo()
    service = PreflightService(repo, secret=b"test-secret")
    context = service.build_context(["acme"])

    assert [a["id"] for a in context.analyses] == ["an-1"]
    assert [q["id"] for q in context.requirements] == ["req-1"]
    assert {e["id"] for e in context.canonical_entities} == {"org-1", "person-1"}
    assert context.preflight_token
    assert context.token == context.preflight_token
    assert context.keywords == ["acme"]


def test_response_projection_matches_contract():
    repo = make_repo()
    service = PreflightService(repo, secret=b"secret")
    payload = service.response(["acme"])

    assert set(payload) == {
        "preflight_token",
        "analyses",
        "requirements",
        "canonical_entities",
    }
    assert payload["preflight_token"]
    assert payload["analyses"][0]["code"] == "AN-ACME"


def test_canonical_index_is_normalized():
    repo = make_repo()
    service = PreflightService(repo, secret=b"secret")
    context = service.build_context(["acme"])
    index = context.canonical_index()
    assert index[normalize_name("acme corp")] == ("org-1", "Organization")


def test_entity_hints_filter_canonical_entities():
    repo = make_repo()
    service = PreflightService(repo, secret=b"secret")
    context = service.build_context(["acme"], entity_hints=["Juan"])
    assert {e["id"] for e in context.canonical_entities} == {"person-1"}


# ---------------------------------------------------------------------
# Token efímero (emisión / verificación)
# ---------------------------------------------------------------------
def test_token_header_matches_contract():
    assert TOKEN_HEADER == "X-Preflight-Token"


def test_verify_valid_token_returns_claims():
    service = PreflightService(make_repo(), secret=b"secret")
    token = service.issue_token(["acme"])
    claims = service.require_valid_token(token)
    assert claims.keywords == ("acme",)
    assert claims.expires_at > claims.issued_at


def test_missing_token_is_rejected():
    service = PreflightService(make_repo(), secret=b"secret")
    with pytest.raises(PreflightRequiredError) as excinfo:
        service.verify_token(None)
    assert excinfo.value.code == "PREFLIGHT_REQUIRED"
    with pytest.raises(PreflightRequiredError):
        service.verify_token("   ")


def test_tampered_token_is_rejected():
    service = PreflightService(make_repo(), secret=b"secret")
    token = service.issue_token(["acme"])
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    with pytest.raises(PreflightRequiredError):
        service.verify_token(tampered)


def test_token_signature_is_secret_bound():
    token = PreflightService(make_repo(), secret=b"secret-a").issue_token(["acme"])
    other = PreflightService(make_repo(), secret=b"secret-b")
    with pytest.raises(PreflightRequiredError):
        other.verify_token(token)


def test_expired_token_is_rejected():
    clock = Clock()
    service = PreflightService(
        make_repo(), secret=b"secret", ttl_seconds=60, now=clock
    )
    token = service.issue_token(["acme"])
    service.require_valid_token(token)  # vigente
    clock.advance(61)
    with pytest.raises(PreflightRequiredError):
        service.verify_token(token)


def test_malformed_token_is_rejected():
    service = PreflightService(make_repo(), secret=b"secret")
    with pytest.raises(PreflightRequiredError):
        service.verify_token("no-es-un-token")


# ---------------------------------------------------------------------
# Prohibición de ingesta ciega (Constitución Regla 3 · SPEC-03 §5)
# ---------------------------------------------------------------------
def test_ingest_informant_requires_valid_token():
    repo = make_repo()
    service = PreflightService(repo, secret=b"secret")
    extractor = NLPExtractor(
        FakeLLMClient(),
        EntityResolver(repo),
        repository=repo,
        preflight=service,
    )
    with pytest.raises(PreflightRequiredError):
        extractor.ingest_informant(REPORT_ID, [PAGE], preflight_token=None)
    # El grafo no fue tocado.
    assert repo.edges == []


def test_ingest_informant_without_preflight_service_raises():
    repo = make_repo()
    extractor = NLPExtractor(FakeLLMClient(), EntityResolver(repo), repository=repo)
    with pytest.raises(PreflightRequiredError):
        extractor.ingest_informant(REPORT_ID, [PAGE], preflight_token="whatever")


def test_ingest_informant_reuses_canonical_entity_from_context():
    repo = make_repo()
    service = PreflightService(repo, secret=b"secret")
    token = service.build_context(["acme"]).token

    def llm(chunk):  # noqa: ANN001
        return {
            "extracted_triplets": [
                {
                    "subject": {
                        "label": "Organization",
                        "name": "Acme Corp",
                        "attributes": {"registration_id": "REG-1"},
                    },
                    "predicate": {"type": "FINANCED", "confidence": 0.9},
                    "object": {"label": "Person", "name": "Juan Pérez", "attributes": {}},
                }
            ]
        }

    extractor = NLPExtractor(
        FakeLLMClient(llm),
        EntityResolver(repo),
        repository=repo,
        preflight=service,
    )
    outcome = extractor.ingest_informant(
        REPORT_ID, [PAGE], preflight_token=token, metadata={"title": "Informe acme"}
    )

    assert len(outcome.accepted) == 1
    assert "org-1" in repo.nodes  # reutiliza la entidad canónica del pre-vuelo
    assert len(repo.edges) == 1


# ---------------------------------------------------------------------
# Utilidades de la Fase 0
# ---------------------------------------------------------------------
def test_clean_keywords_dedupes_preserving_order():
    assert clean_keywords(["Acme", "acme", " Acme ", "", "Juan"]) == ["Acme", "Juan"]


def test_derive_keywords_filters_stopwords_and_short_tokens():
    words = derive_keywords("El informe de Acme Corp")
    assert "Acme" in words and "Corp" in words
    assert "El" not in words and "de" not in words


def test_derive_keywords_falls_back_to_osint():
    assert derive_keywords("") == ["osint"]
