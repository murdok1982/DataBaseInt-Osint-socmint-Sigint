"""Límites y validación de la subida de informes (SPEC-02 §1.1).

Cubre tres capas:

1. ``read_upload_limited``: corta en cuanto se supera ``MAX_UPLOAD_MB``.
2. ``validate_upload``: extensión y contenido (magia PDF, UTF-8, JSON).
3. ``POST /api/v1/ingest``: 413 ``PAYLOAD_TOO_LARGE`` y 415
   ``UNSUPPORTED_MEDIA_TYPE`` con el sobre de error del contrato.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.core.config import Settings, get_settings, reset_settings_cache
from app.core.documents import UploadRejected, read_upload_limited, validate_upload
from app.core.errors import CODE_PAYLOAD_TOO_LARGE, CODE_UNSUPPORTED_MEDIA_TYPE
from app.core.rbac import Role
from tests.security.conftest import auth_header


class _FakeUpload:
    """Doble mínimo de ``UploadFile.read`` que sirve bloques de tamaño fijo."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    async def read(self, size: int = -1) -> bytes:
        chunk, self._content = self._content[:size], self._content[size:]
        return chunk


def _limit_mb(monkeypatch: pytest.MonkeyPatch, mb: int) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", str(mb))
    reset_settings_cache()


# =====================================================================
# 1) Lectura acotada
# =====================================================================
def test_read_upload_limited_returns_full_content_under_the_limit() -> None:
    content = b"x" * 1000
    assert asyncio.run(read_upload_limited(_FakeUpload(content), 4096)) == content


def test_read_upload_limited_rejects_content_over_the_limit() -> None:
    with pytest.raises(UploadRejected) as excinfo:
        asyncio.run(read_upload_limited(_FakeUpload(b"x" * (2 * 1024 * 1024)), 1024 * 1024))
    assert excinfo.value.status_code == 413
    assert excinfo.value.code == CODE_PAYLOAD_TOO_LARGE


def test_read_upload_limited_accepts_content_exactly_at_the_limit() -> None:
    content = b"x" * 1024
    assert asyncio.run(read_upload_limited(_FakeUpload(content), 1024)) == content


# =====================================================================
# 2) Validación de extensión y contenido
# =====================================================================
@pytest.mark.parametrize(
    ("filename", "content"),
    [
        ("informe.pdf", b"%PDF-1.7 contenido"),
        ("informe.txt", "Juan Pérez controla Acme.".encode("utf-8")),
        ("informe.md", b"# Titulo\n\nTexto"),
        ("informe.markdown", b"# Titulo"),
        ("informe.json", b'{"extracted_triplets": []}'),
    ],
)
def test_validate_upload_accepts_supported_formats(filename: str, content: bytes) -> None:
    validate_upload(filename, content)


@pytest.mark.parametrize(
    ("filename", "content", "reason"),
    [
        ("malware.exe", b"MZ", "extensión no admitida"),
        ("sin-extension", b"texto", "sin extensión"),
        (None, b"texto", "sin nombre"),
        ("informe.pdf", b"<html>no es pdf</html>", "PDF sin cabecera %PDF"),
        ("informe.txt", b"\xff\xfe\x00invalido", "texto que no es UTF-8"),
        ("informe.json", b"{no es json", "JSON mal formado"),
    ],
)
def test_validate_upload_rejects_unsupported_or_mismatched_content(
    filename: str | None, content: bytes, reason: str
) -> None:
    with pytest.raises(UploadRejected) as excinfo:
        validate_upload(filename, content)
    assert excinfo.value.status_code == 415, reason
    assert excinfo.value.code == CODE_UNSUPPORTED_MEDIA_TYPE, reason


# =====================================================================
# 3) Contrato HTTP: 413 y 415 con el sobre de error
# =====================================================================
def test_ingest_rejects_oversized_report_with_413(
    client: Any, tokens: dict[Role, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _limit_mb(monkeypatch, 1)
    response = client.post(
        "/api/v1/ingest",
        headers=auth_header(tokens[Role.ANALYST]),
        files={"file": ("grande.txt", b"a" * (1024 * 1024 + 1), "text/plain")},
        data={"title": "Informe grande"},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == CODE_PAYLOAD_TOO_LARGE


def test_ingest_rejects_unsupported_extension_with_415(
    client: Any, tokens: dict[Role, str]
) -> None:
    response = client.post(
        "/api/v1/ingest",
        headers=auth_header(tokens[Role.ANALYST]),
        files={"file": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
        data={"title": "Ejecutable"},
    )
    assert response.status_code == 415
    assert response.json()["error"]["code"] == CODE_UNSUPPORTED_MEDIA_TYPE


def test_ingest_rejects_pdf_without_magic_bytes_with_415(
    client: Any, tokens: dict[Role, str]
) -> None:
    response = client.post(
        "/api/v1/ingest",
        headers=auth_header(tokens[Role.ANALYST]),
        files={"file": ("falso.pdf", b"<html></html>", "application/pdf")},
        data={"title": "Falso PDF"},
    )
    assert response.status_code == 415


def test_ingest_still_accepts_a_valid_text_report(
    client: Any, tokens: dict[Role, str]
) -> None:
    response = client.post(
        "/api/v1/ingest",
        headers=auth_header(tokens[Role.ANALYST]),
        files={"file": ("ok.txt", "Informe válido.".encode("utf-8"), "text/plain")},
        data={"title": "Informe válido"},
    )
    assert response.status_code == 202


# =====================================================================
# 4) Configuración: MAX_UPLOAD_MB y GRAPH_BACKEND
# =====================================================================
def test_max_upload_bytes_follows_max_upload_mb(monkeypatch: pytest.MonkeyPatch) -> None:
    _limit_mb(monkeypatch, 5)
    assert get_settings().max_upload_bytes == 5 * 1024 * 1024


def test_graph_backend_defaults_to_neo4j(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAPH_BACKEND", raising=False)
    reset_settings_cache()
    assert get_settings().graph_backend == "neo4j"


@pytest.mark.parametrize("value", ["mongo", "NEO4J-LEGACY"])
def test_unknown_graph_backend_is_rejected_at_startup(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("GRAPH_BACKEND", value)
    with pytest.raises(ValueError, match="GRAPH_BACKEND"):
        Settings()


def test_blank_graph_backend_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GRAPH_BACKEND", "   ")
    assert Settings().graph_backend == "neo4j"


def test_postgres_graph_backend_answers_503_not_a_silent_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.deps import get_graph_repository
    from app.core.errors import ServiceUnavailable

    monkeypatch.setenv("GRAPH_BACKEND", "postgres")
    reset_settings_cache()
    with pytest.raises(ServiceUnavailable, match="GRAPH_BACKEND=postgres"):
        get_graph_repository()


def test_non_positive_upload_limit_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_MB", "0")
    reset_settings_cache()
    with pytest.raises(ValueError, match="MAX_UPLOAD_MB"):
        Settings()
