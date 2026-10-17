"""Dobles de prueba **sin red** del cliente M2M (para tests y demos offline).

``FakeInformantApi`` implementa :class:`~.api_client.InformantApi` y **modela** el
servidor: rechaza ``ingest_informant`` sin un pre-vuelo previo o con un token
distinto al emitido (equivalente al ``409`` de SPEC-03 §5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .api_client import PreflightRequired
from .models import IngestJob, PreflightResponse


@dataclass
class ApiCall:
    """Registro de una llamada al doble."""

    name: str
    kwargs: dict[str, Any]


@dataclass
class FakeInformantApi:
    """Cliente M2M en memoria que registra y valida el orden pre-vuelo → ingesta."""

    preflight_response: PreflightResponse | Any
    ingest_response: IngestJob = field(default_factory=lambda: IngestJob(job_id="job-1", status="QUEUED", progress=0.0))
    require_preflight: bool = True
    calls: list[ApiCall] = field(default_factory=list)
    _token: str | None = None

    # ------------------------------------------------------------------ #
    def preflight(
        self, *, keywords: list[str], entity_hints: list[str] | None = None
    ) -> PreflightResponse:
        self.calls.append(ApiCall("preflight", {"keywords": list(keywords), "entity_hints": entity_hints}))
        token = getattr(self.preflight_response, "preflight_token", None)
        if token is None and isinstance(self.preflight_response, Mapping):
            token = self.preflight_response.get("preflight_token")
        self._token = token
        return self.preflight_response  # type: ignore[return-value]

    def ingest_informant(
        self,
        *,
        filename: str,
        content: bytes,
        payload: Mapping[str, Any],
        preflight_token: str,
    ) -> IngestJob:
        self.calls.append(
            ApiCall(
                "ingest_informant",
                {
                    "filename": filename,
                    "content": content,
                    "payload": payload,
                    "preflight_token": preflight_token,
                },
            )
        )
        if self.require_preflight:
            if not self._token:
                raise PreflightRequired("Fake API: sin pre-vuelo previo (409).")
            if preflight_token != self._token:
                raise PreflightRequired("Fake API: preflight_token no vigente (409).")
        return self.ingest_response

    # ------------------------------------------------------------------ #
    @property
    def call_names(self) -> list[str]:
        return [call.name for call in self.calls]

    def ingest_calls(self) -> list[ApiCall]:
        return [call for call in self.calls if call.name == "ingest_informant"]


__all__ = ["ApiCall", "FakeInformantApi"]
