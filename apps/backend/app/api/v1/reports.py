"""Rutas de ``/reports`` (tag ``reports`` del contrato).

Autorizado por: **SPEC-03 §4** (informes y evidencia textual),
**SPEC-04 §4** (acceso seguro a informes completos) y ``contracts/openapi.yaml``.

| operationId        | Método | Ruta                     | Rol mínimo |
| :----------------- | :----- | :----------------------- | :--------- |
| ``getReport``      | GET    | ``/reports/{id}``        | VIEWER     |
| ``getReportFile``  | GET    | ``/reports/{id}/file``   | VIEWER     |
| ``getReportChunks``| GET    | ``/reports/{id}/chunks`` | VIEWER     |

Seguridad del PDF (SPEC-04 §4)
------------------------------
La base de datos **nunca** sirve el archivo. El backend:

1. valida el JWT y el rol (VIEWER+);
2. pide al gateway la ruta del informe;
3. **confina** esa ruta dentro de ``STORAGE_RAW_DIR`` (``Path.resolve()`` +
   ``is_relative_to``), bloqueando *path traversal* (``../``) y enlaces fuera del
   *storage*;
4. devuelve el PDF con ``Content-Disposition: inline`` (visor del frontend).

Cualquier ruta que no resuelva dentro del *storage* se trata como **404**; nunca
se filtra la ruta real del sistema de archivos.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.api.deps import get_graph_gateway
from app.api.graph_gateway import GraphGateway
from app.core.config import get_settings
from app.core.errors import CODE_NOT_FOUND, detail_from_code
from app.core.rbac import Role, require_role
from app.core.users import Principal

router = APIRouter(prefix="/reports", tags=["reports"])


class Report(BaseModel):
    """``Report`` del contrato."""

    id: str
    title: Optional[str] = None
    file_path: Optional[str] = None
    ingested_at: Optional[str] = None
    author_id: Optional[str] = None


class Chunk(BaseModel):
    """``Chunk`` del contrato (coordenadas para subrayado)."""

    chunk_id: str
    page_number: int = Field(ge=1)
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)
    text: str


class ChunkListResponse(BaseModel):
    """Respuesta de ``getReportChunks``."""

    items: list[Chunk] = Field(default_factory=list)


def _not_found(resource: str, identifier: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=detail_from_code(
            CODE_NOT_FOUND, f"{resource} no encontrado: {identifier}"
        ),
    )


def resolve_storage_path(candidate: str) -> Optional[Path]:
    """Confina ``candidate`` dentro de ``STORAGE_RAW_DIR`` (SPEC-04 §4).

    :returns: ruta absoluta y validada, o ``None`` si escapa del *storage* o no
        es un archivo regular.
    """
    if not candidate:
        return None
    storage_root = Path(get_settings().storage_raw_dir).expanduser().resolve()
    try:
        resolved = Path(candidate).expanduser().resolve()
    except (OSError, RuntimeError):  # pragma: no cover - ruta inválida
        return None
    if not resolved.is_relative_to(storage_root):
        return None
    if not resolved.is_file():
        return None
    return resolved


# =====================================================================
# GET /reports/{id} — getReport (VIEWER+)
# =====================================================================
@router.get(
    "/{id}",
    operation_id="getReport",
    response_model=Report,
    summary="Metadatos del informe (VIEWER+)",
)
async def get_report(
    id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> Report:
    """Metadatos del informe (título, autor, fecha de ingesta)."""
    result = gateway.get_report(id)
    if result is None:
        raise _not_found("Informe", id)
    return Report(**result)


# =====================================================================
# GET /reports/{id}/file — getReportFile (VIEWER+)
# =====================================================================
@router.get(
    "/{id}/file",
    operation_id="getReportFile",
    response_class=FileResponse,
    summary="Sirve el PDF para el visor (VIEWER+)",
)
async def get_report_file(
    id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> FileResponse:
    """Sirve el PDF original solo a operadores autenticados (SPEC-04 §4).

    La ruta se valida contra ``STORAGE_RAW_DIR``; si el informe no existe o la
    ruta escapa del *storage*, se devuelve 404 sin filtrar rutas internas.
    """
    raw_path = gateway.get_report_file_path(id)
    storage_path = resolve_storage_path(raw_path)
    if storage_path is None:
        raise _not_found("Archivo de informe", id)
    return FileResponse(
        path=str(storage_path),
        media_type="application/pdf",
        filename=storage_path.name,
        content_disposition_type="inline",
    )


# =====================================================================
# GET /reports/{id}/chunks — getReportChunks (VIEWER+)
# =====================================================================
@router.get(
    "/{id}/chunks",
    operation_id="getReportChunks",
    response_model=ChunkListResponse,
    summary="Chunks con coordenadas para subrayado (VIEWER+)",
)
async def get_report_chunks(
    id: str,
    user: Principal = Depends(require_role(Role.VIEWER)),
    gateway: GraphGateway = Depends(get_graph_gateway),
) -> ChunkListResponse:
    """Chunks con coordenadas de carácter para el subrayado de evidencia."""
    payload = gateway.get_report_chunks(id)
    return ChunkListResponse(**payload)


__all__ = ["router", "Report", "Chunk", "ChunkListResponse", "resolve_storage_path"]
