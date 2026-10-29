"""Atestación firmada de las decisiones sobre propuestas (SPEC-02 §11.5).

Zona: Agente 1 — ``apps/backend/app/db/**``.

``staging.fn_decide_proposal`` sólo acepta una decisión si la atestación lleva
un HMAC-SHA256 válido, emitido con la clave que comparten el backend y la tabla
``staging.attestation_keys``. El lector ``api_reader`` no puede leer esa tabla,
así que no puede fabricar una atestación ni reutilizar una de otra decisión.

Formato: ``exp|actor|clearance|hmac`` con
``hmac = HMAC-SHA256(key, "proposal_id|status|version|exp|actor|clearance")``.
La clave se lee de ``DB_ATTESTATION_KEY`` y nunca se registra en logs.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

MIN_KEY_LENGTH = 32
DEFAULT_TTL_SECONDS = 60


def load_key(raw: Optional[str]) -> str:
    """Valida la clave; falla si falta o es demasiado corta."""
    key = (raw or "").strip()
    if len(key) < MIN_KEY_LENGTH:
        raise RuntimeError(
            "DB_ATTESTATION_KEY ausente o demasiado corta (mínimo 32 caracteres)."
        )
    return key


def issue_attestation(
    key: str,
    *,
    proposal_id: str,
    status: str,
    version: int,
    actor: str,
    clearance: str,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: Optional[float] = None,
) -> str:
    """Emite la atestación para una decisión concreta."""
    if not actor or "|" in actor:
        raise ValueError("El identificador del revisor no puede estar vacío ni contener '|'.")
    if "|" in clearance:
        raise ValueError("clearance inválido.")
    exp = int((time.time() if now is None else now) + ttl_seconds)
    message = "|".join([proposal_id, status, str(version), str(exp), actor, clearance])
    mac = hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{exp}|{actor}|{clearance}|{mac}"
