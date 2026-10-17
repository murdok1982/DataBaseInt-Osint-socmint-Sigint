"""Acceso seguro a los informes completos (SPEC-04 §4).

Autorizado por: **SPEC-04 §4** (*Acceso Seguro a Informes Completos*) y
**SPEC-03 §4** (``getReport`` / ``getReportFile`` / ``getReportChunks``).

Reglas verificadas:
- el PDF solo se sirve a un operador **autenticado** (VIEWER+);
- la ruta se **confina** a ``STORAGE_RAW_DIR`` (bloquea *path traversal*);
- la base de datos nunca sirve el archivo: siempre FastAPI con JWT/RBAC;
- cualquier ruta fuera del *storage* o inexistente ⇒ **404** sin filtrar rutas.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import pytest

from app.api.deps import get_graph_gateway
from app.api.graph_gateway import GraphGateway
from app.core.rbac import Role
from tests.security.conftest import REPORT_ID, auth_header


class _EscapingRepository:
    """Repositorio hostil: devuelve rutas fuera del *storage* (ataque)."""

    def __init__(self, escaped_path: Path) -> None:
        self._escaped_path = escaped_path

    def get_report_file_path(self, report_id: str) -> str:
        return str(self._escaped_path)

    def get_report(self, report_id: str) -> Optional[dict[str, Any]]:
        return {"id": report_id, "title": "Informe", "file_path": str(self._escaped_path)}


class _MissingFileRepository:
    """Informe existente cuyo archivo ya no está en disco."""

    def __init__(self, missing_path: Path) -> None:
        self._missing_path = missing_path

    def get_report_file_path(self, report_id: str) -> str:
        return str(self._missing_path)


def test_viewer_downloads_pdf_inline(
    client: Any, tokens: dict[Role, str], storage_dir: Path
) -> None:
    """VIEWER autenticado recibe el PDF con ``Content-Disposition: inline``."""
    response = client.get(
        f"/api/v1/reports/{REPORT_ID}/file", headers=auth_header(tokens[Role.VIEWER])
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "inline" in response.headers.get("content-disposition", "")
    assert response.content.startswith(b"%PDF")


def test_anonymous_cannot_download_report_file(client: Any) -> None:
    """Sin token no hay acceso al informe original (401)."""
    response = client.get(f"/api/v1/reports/{REPORT_ID}/file")
    assert response.status_code == 401


@pytest.mark.parametrize("role", [Role.VIEWER, Role.ANALYST, Role.ADMIN])
def test_all_roles_can_read_report_metadata(
    client: Any, tokens: dict[Role, str], role: Role
) -> None:
    """``getReport`` es VIEWER+ (SPEC-03 §4 + SPEC-04 §1)."""
    response = client.get(
        f"/api/v1/reports/{REPORT_ID}", headers=auth_header(tokens[role])
    )
    assert response.status_code == 200
    assert response.json()["id"] == REPORT_ID


def test_unknown_report_returns_404(client: Any, tokens: dict[Role, str]) -> None:
    """Informe inexistente ⇒ 404 con el sobre de error del contrato."""
    response = client.get(
        "/api/v1/reports/99999999-9999-9999-9999-999999999999",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_report_chunks_are_viewer_accessible(client: Any, tokens: dict[Role, str]) -> None:
    """``getReportChunks`` devuelve coordenadas para el subrayado de evidencia."""
    response = client.get(
        f"/api/v1/reports/{REPORT_ID}/chunks",
        headers=auth_header(tokens[Role.VIEWER]),
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["page_number"] >= 1
    assert item["char_end"] > item["char_start"]
    assert item["text"]


def test_path_traversal_outside_storage_is_blocked(
    app: Any, client: Any, tokens: dict[Role, str], storage_dir: Path
) -> None:
    """Una ruta fuera de ``STORAGE_RAW_DIR`` se rechaza con 404 (SPEC-04 §4)."""
    escaped = storage_dir.parent / "secreto.txt"
    escaped.write_text("no debe servirse", encoding="utf-8")
    app.dependency_overrides[get_graph_gateway] = lambda: GraphGateway(
        _EscapingRepository(escaped)
    )

    response = client.get(
        f"/api/v1/reports/{REPORT_ID}/file", headers=auth_header(tokens[Role.VIEWER])
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
    assert "secreto" not in response.text


def test_missing_file_on_disk_returns_404(
    app: Any, client: Any, tokens: dict[Role, str], storage_dir: Path
) -> None:
    """Archivo inexistente ⇒ 404 (no se revela la ruta real)."""
    app.dependency_overrides[get_graph_gateway] = lambda: GraphGateway(
        _MissingFileRepository(storage_dir / "no-existe.pdf")
    )
    response = client.get(
        f"/api/v1/reports/{REPORT_ID}/file", headers=auth_header(tokens[Role.VIEWER])
    )
    assert response.status_code == 404


def test_admin_can_also_download_the_pdf(
    client: Any, tokens: dict[Role, str]
) -> None:
    """ADMIN también puede abrir el informe original (VIEWER+ ⊂ ADMIN)."""
    response = client.get(
        f"/api/v1/reports/{REPORT_ID}/file", headers=auth_header(tokens[Role.ADMIN])
    )
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")
