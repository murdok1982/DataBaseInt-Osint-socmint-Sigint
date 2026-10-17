"""Recepción y almacenamiento de informes OSINT (SPEC-02 §1).

Autorizado por: **SPEC-02 §1.1–§1.3**
1. Recepción del archivo (PDF, TXT, Markdown, JSON) en ``POST /api/v1/ingest``.
2. Almacenamiento íntegro en ``/storage/raw_reports/{report_id}.pdf``.
3. Fragmentación conservando número de página y posición de caracteres
   (el *chunking* es del Agente 2; aquí solo se extraen las **páginas**).

Zona: Agente 3 (``app/core/**``). El informe original solo se sirve por FastAPI
con JWT/RBAC (SPEC-04 §4): nunca se expone el sistema de archivos ni la base de
datos.
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.errors import ServiceUnavailable

#: Extensiones admitidas por SPEC-02 §1.1.
ALLOWED_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".txt",
    ".md",
    ".markdown",
    ".json",
)

#: Extensión por defecto cuando el nombre no aporta una válida.
DEFAULT_EXTENSION = ".pdf"

PDF_MAGIC = b"%PDF"

try:  # pragma: no cover - depende del entorno
    from pypdf import PdfReader

    PDF_AVAILABLE = True
except Exception:  # pragma: no cover - import protegido deliberado
    PdfReader = None  # type: ignore
    PDF_AVAILABLE = False

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_extension(filename: str | None) -> str:
    """Extensión validada del archivo subido (nunca se usa el nombre original)."""
    suffix = Path(filename or "").suffix.lower()
    return suffix if suffix in ALLOWED_EXTENSIONS else DEFAULT_EXTENSION


def storage_root() -> Path:
    """Directorio de informes crudos (``STORAGE_RAW_DIR``) creado si falta."""
    root = Path(get_settings().storage_raw_dir).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return root


def raw_report_path(report_id: str, filename: str | None = None) -> Path:
    """Ruta destino ``{report_id}{ext}`` dentro del *storage* (SPEC-02 §1.2).

    El nombre del cliente se descarta salvo su extensión validada: así no hay
    *path traversal* posible.
    """
    safe_id = _UNSAFE_NAME.sub("", str(report_id)) or "report"
    return storage_root() / f"{safe_id}{safe_extension(filename)}"


def save_raw_report(report_id: str, filename: str | None, content: bytes) -> Path:
    """Guarda el informe íntegro y devuelve su ruta (SPEC-02 §1.2)."""
    if not content:
        raise ServiceUnavailable("El informe recibido está vacío.")
    destination = raw_report_path(report_id, filename)
    destination.write_bytes(content)
    return destination


def is_pdf(content: bytes, filename: str | None = None) -> bool:
    """¿El contenido es un PDF? (magia de bytes, no confianza en el nombre)."""
    if content[:4] == PDF_MAGIC:
        return True
    return safe_extension(filename) == DEFAULT_EXTENSION and content[:4] == PDF_MAGIC


def extract_pages(content: bytes, filename: str | None = None) -> list[str]:
    """Extrae el texto por **páginas** (SPEC-02 §1.3).

    - PDF: ``pypdf`` página a página (salto de página natural).
    - TXT/Markdown/JSON: decodificación UTF-8 y separación por ``\\f``.

    :raises ServiceUnavailable: si es un PDF y no hay lector disponible.
    """
    if is_pdf(content, filename):
        if not PDF_AVAILABLE:
            raise ServiceUnavailable(
                "No hay lector de PDF disponible: instala 'pypdf' "
                "(apps/backend/requirements.txt) para procesar informes PDF."
            )
        try:
            reader = PdfReader(io.BytesIO(content))
            return [(page.extract_text() or "") for page in reader.pages]
        except Exception as exc:
            raise ServiceUnavailable(
                f"No se pudo leer el PDF recibido: {exc}"
            ) from exc

    text = content.decode("utf-8", errors="ignore")
    pages = [page for page in text.split("\f")]
    return pages or [text]


def report_metadata(
    report_id: str, title: str, path: Path, author_id: str
) -> dict[str, Any]:
    """Metadatos del nodo ``Report`` (SPEC-01 §1 / SPEC-02 §1.5)."""
    from datetime import datetime, timezone

    return {
        "report_id": report_id,
        "title": title,
        "file_path": str(path),
        "author_id": author_id,
        "ingested_at": datetime.now(tz=timezone.utc).isoformat(),
    }


__all__ = [
    "ALLOWED_EXTENSIONS",
    "PDF_AVAILABLE",
    "safe_extension",
    "storage_root",
    "raw_report_path",
    "save_raw_report",
    "extract_pages",
    "is_pdf",
    "report_metadata",
]
