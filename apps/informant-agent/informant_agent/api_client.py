"""Cliente M2M del Informante IA (``X-API-Key``) — SPEC-03 §1, §5.

Solo dos operaciones, ambas definidas en ``contracts/openapi.yaml``:

* ``POST /api/v1/context/preflight`` → :class:`PreflightResponse`.
* ``POST /api/v1/ingest/informant`` (multipart ``file`` + ``payload``) con headers
  ``X-API-Key`` + ``X-Preflight-Token`` → :class:`IngestJob`.

**Prohibición de ingesta ciega (Regla 3 / SPEC-03 §5):** si falta el
``preflight_token`` (o no fue emitido por un pre-vuelo de este cliente), se eleva
:class:`PreflightRequired` y **no se emite ninguna petición HTTP**.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Protocol, runtime_checkable

import httpx

from .models import IngestJob, PreflightResponse

#: Prefijo de todas las rutas (SPEC-03 §1).
API_PREFIX = "/api/v1"

#: Rutas exactas del contrato (openapi.yaml).
PREFLIGHT_PATH = f"{API_PREFIX}/context/preflight"
INGEST_INFORMANT_PATH = f"{API_PREFIX}/ingest/informant"

_CONTENT_TYPES = {
    ".json": "application/json",
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
}


class InformantApiError(RuntimeError):
    """Error genérico al hablar con la API M2M."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class PreflightRequired(InformantApiError):
    """Se intentó ingerir sin un pre-vuelo válido (ingesta ciega prohibida)."""


@runtime_checkable
class InformantApi(Protocol):
    """Interfaz mínima del cliente M2M (permite mocks sin red)."""

    def preflight(
        self, *, keywords: list[str], entity_hints: list[str] | None = None
    ) -> PreflightResponse:
        ...

    def ingest_informant(
        self,
        *,
        filename: str,
        content: bytes,
        payload: Mapping[str, Any],
        preflight_token: str,
    ) -> IngestJob:
        ...


def guess_content_type(filename: str) -> str:
    """Content-Type por extensión (``application/octet-stream`` por defecto)."""
    return _CONTENT_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


class HttpInformantApiClient:
    """Cliente HTTP real contra la API de OSINT Nexus (httpx)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
        require_preflight_history: bool = True,
    ):
        if not base_url:
            raise ValueError("base_url es obligatoria.")
        if not api_key:
            raise ValueError(
                "X-API-Key es obligatoria (AI_INFORMANT). Configura INFORMANT_API_KEY."
            )
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)
        self._require_preflight_history = require_preflight_history
        self._issued_tokens: set[str] = set()

    # ------------------------------------------------------------------ #
    # Fase 0 — Pre-Flight Context Check
    # ------------------------------------------------------------------ #
    def preflight(
        self, *, keywords: list[str], entity_hints: list[str] | None = None
    ) -> PreflightResponse:
        """``POST /context/preflight``. Guarda el ``preflight_token`` emitido."""
        if not keywords:
            raise ValueError("El pre-vuelo exige al menos una keyword (PreflightRequest).")

        body: dict[str, Any] = {"keywords": list(keywords)}
        if entity_hints:
            body["entity_hints"] = list(entity_hints)

        response = self._client.post(
            f"{self.base_url}{PREFLIGHT_PATH}",
            json=body,
            headers=self._auth_headers(),
        )
        self._raise_for_status(response, operation="context/preflight")
        parsed = PreflightResponse.model_validate(response.json())
        if parsed.preflight_token:
            self._issued_tokens.add(parsed.preflight_token)
        return parsed

    # ------------------------------------------------------------------ #
    # Fase 1 — Ingesta M2M (nunca sin pre-vuelo)
    # ------------------------------------------------------------------ #
    def ingest_informant(
        self,
        *,
        filename: str,
        content: bytes,
        payload: Mapping[str, Any],
        preflight_token: str,
    ) -> IngestJob:
        """``POST /ingest/informant`` (multipart). Rechaza si no hay pre-vuelo."""
        if not preflight_token:
            raise PreflightRequired(
                "Ingesta ciega prohibida: falta X-Preflight-Token (Regla 3 / "
                "SPEC-03 §5). Ejecuta /context/preflight antes de /ingest/informant."
            )
        if self._require_preflight_history and preflight_token not in self._issued_tokens:
            raise PreflightRequired(
                "El X-Preflight-Token no fue emitido por un pre-vuelo de este "
                "cliente. Ejecuta /context/preflight antes de /ingest/informant."
            )

        headers = self._auth_headers()
        headers["X-Preflight-Token"] = preflight_token
        files = {"file": (filename, content, guess_content_type(filename))}
        data = {"payload": json.dumps(payload, ensure_ascii=False)}

        response = self._client.post(
            f"{self.base_url}{INGEST_INFORMANT_PATH}",
            headers=headers,
            files=files,
            data=data,
        )
        self._raise_for_status(response, operation="ingest/informant")
        return IngestJob.model_validate(response.json())

    # ------------------------------------------------------------------ #
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "HttpInformantApiClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    def _auth_headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key, "Accept": "application/json"}

    @staticmethod
    def _raise_for_status(response: httpx.Response, *, operation: str) -> None:
        if response.is_success:
            return
        code: str | None = None
        message: str | None = None
        try:
            body = response.json()
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                code = body["error"].get("code")
                message = body["error"].get("message")
        except Exception:  # noqa: BLE001 — cuerpo no-JSON: se ignora
            pass

        if response.status_code == 409:
            raise PreflightRequired(
                f"{operation}: 409 CONFLICT — {message or 'preflight_token no vigente.'}",
                status_code=409,
                code=code or "CONFLICT",
            )
        raise InformantApiError(
            f"{operation}: HTTP {response.status_code} — {message or response.text[:200]}",
            status_code=response.status_code,
            code=code,
        )


__all__ = [
    "API_PREFIX",
    "INGEST_INFORMANT_PATH",
    "PREFLIGHT_PATH",
    "HttpInformantApiClient",
    "InformantApi",
    "InformantApiError",
    "PreflightRequired",
    "guess_content_type",
]
