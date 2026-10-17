"""El servicio **nunca** ingiere sin pre-vuelo previo (Regla 3 / SPEC-03 §5).

Cubre las dos barreras:
1. El pipeline ejecuta pre-vuelo antes de ingesta y pasa su token.
2. El cliente HTTP rechaza ``/ingest/informant`` sin ``X-Preflight-Token`` válido,
   **sin emitir ninguna petición**.
"""

from __future__ import annotations

import json

import httpx
import pytest

from informant_agent.api_client import (
    HttpInformantApiClient,
    PreflightRequired,
)
from informant_agent.extractor import ContextGuidedExtractor
from informant_agent.models import PreflightResponse
from informant_agent.pipeline import InformantPipeline
from informant_agent.testing import FakeInformantApi


# --------------------------------------------------------------------------- #
# Barrera 1 — Pipeline (con mock sin red)
# --------------------------------------------------------------------------- #
def test_pipeline_preflights_before_ingesting(context, make_document):
    api = FakeInformantApi(preflight_response=context)
    pipeline = InformantPipeline(api)
    doc = make_document("State Bank financed Acme Corp in 2021.", keywords=["State Bank"])

    result = pipeline.run(doc)

    assert api.call_names == ["preflight", "ingest_informant"]
    ingest = api.ingest_calls()[0]
    assert ingest.kwargs["preflight_token"] == "tok-123"
    assert result.job.job_id == "job-1"
    # El cuerpo ingerido valida contra el contrato sellado.
    from informant_agent.contract import validate_extraction

    validate_extraction(result.payload)


def test_pipeline_aborts_when_preflight_has_no_token(make_document):
    empty = PreflightResponse(preflight_token="", analyses=[], requirements=[], canonical_entities=[])
    api = FakeInformantApi(preflight_response=empty)
    pipeline = InformantPipeline(api)
    doc = make_document("State Bank financed Acme Corp.")

    with pytest.raises(PreflightRequired):
        pipeline.run(doc)

    assert api.call_names == ["preflight"]  # nunca llegó a ingesta
    assert api.ingest_calls() == []


def test_pipeline_validates_before_sending(context, make_document):
    api = FakeInformantApi(preflight_response=context)

    def reject(_payload):
        raise ValueError("payload inválido")

    pipeline = InformantPipeline(api, validator=reject)
    doc = make_document("State Bank financed Acme Corp.")

    with pytest.raises(ValueError):
        pipeline.run(doc)

    assert api.ingest_calls() == []  # no se envía lo que no valida


# --------------------------------------------------------------------------- #
# Barrera 2 — Cliente HTTP (httpx.MockTransport, sin red)
# --------------------------------------------------------------------------- #
@pytest.fixture
def recorder():
    return []


def _client(recorder, *, ingest_status=202, ingest_body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        recorder.append(request)
        path = request.url.path
        if path == "/api/v1/context/preflight":
            return httpx.Response(
                200,
                json={
                    "preflight_token": "tok-123",
                    "analyses": [],
                    "requirements": [],
                    "canonical_entities": [],
                },
            )
        if path == "/api/v1/ingest/informant":
            return httpx.Response(
                ingest_status,
                json=ingest_body if ingest_body is not None else {"job_id": "job-1", "status": "QUEUED"},
            )
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND", "message": path}})

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport)
    return HttpInformantApiClient(
        base_url="http://api.test", api_key="secret-key", client=http_client
    )


def test_client_refuses_ingest_without_token(recorder):
    client = _client(recorder)
    with pytest.raises(PreflightRequired):
        client.ingest_informant(
            filename="doc.txt", content=b"x", payload={}, preflight_token=""
        )
    assert recorder == []  # cero peticiones HTTP


def test_client_refuses_ingest_without_prior_preflight(recorder):
    client = _client(recorder)
    with pytest.raises(PreflightRequired):
        client.ingest_informant(
            filename="doc.txt", content=b"x", payload={}, preflight_token="fabricated"
        )
    assert recorder == []


def test_client_sends_required_headers_and_multipart(context, recorder):
    client = _client(recorder)
    preflight = client.preflight(keywords=["State Bank", "Acme Corp"])
    assert preflight.preflight_token == "tok-123"

    job = client.ingest_informant(
        filename="corroborates.json",
        content=json.dumps({"ok": True}).encode("utf-8"),
        payload={"report_metadata": {"title": "t"}},
        preflight_token=preflight.preflight_token,
    )
    assert job.job_id == "job-1"

    preflight_req, ingest_req = recorder
    assert preflight_req.headers["X-API-Key"] == "secret-key"
    assert ingest_req.headers["X-API-Key"] == "secret-key"
    assert ingest_req.headers["X-Preflight-Token"] == "tok-123"
    body = ingest_req.content
    assert b'name="payload"' in body
    assert b'name="file"' in body
    assert b'filename="corroborates.json"' in body


def test_server_409_maps_to_preflight_required(context, recorder):
    client = _client(
        recorder,
        ingest_status=409,
        ingest_body={"error": {"code": "CONFLICT", "message": "preflight_token no vigente"}},
    )
    token = client.preflight(keywords=["x"]).preflight_token
    with pytest.raises(PreflightRequired) as excinfo:
        client.ingest_informant(
            filename="d.txt", content=b"x", payload={}, preflight_token=token
        )
    assert excinfo.value.status_code == 409


def test_full_pipeline_over_mock_transport(context, recorder, make_document):
    client = _client(recorder)
    pipeline = InformantPipeline(client, extractor=ContextGuidedExtractor())
    doc = make_document("State Bank financed Acme Corp in 2021.", keywords=["State Bank"])

    result = pipeline.run(doc)

    assert [r.url.path for r in recorder] == [
        "/api/v1/context/preflight",
        "/api/v1/ingest/informant",
    ]
    assert result.job.job_id == "job-1"
