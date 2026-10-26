"""Custodia de evidencia: canonicalización, SHA-256 e idempotencia.

Zona: Agente 2 — ``apps/backend/app/services/**``.

Este módulo implementa las reglas de SPEC-01 §5 y SPEC-02 §9 que **no** deben
vivir en la base de datos ni en el LLM:

* antes de persistir, los bytes se canonicalizan de forma determinista;
* el SHA-256 se calcula sobre los bytes exactos, no sobre el JSON reconstruido;
* una misma ``(collector_agent, idempotency_key)`` con los mismos bytes es un
  ``DUPLICATE`` legítimo; con bytes distintos es un ``CONFLICT`` que **nunca**
  devuelve la evidencia anterior en silencio.

El repositorio PostgreSQL sigue siendo la última línea de defensa: el contrato
v3.1 tiene el ``UNIQUE (collector_agent, idempotency_key)`` y el trigger de
hash. Este módulo da un error de dominio antes de llegar a la DB.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional


class EvidenceHashMismatch(ValueError):
    """Los bytes no coinciden con el SHA-256 declarado."""


class EvidenceConflict(Exception):
    """La idempotency_key existe con un payload distinto (409)."""


class IdempotencyAction(str, Enum):
    """Resultado de evaluar una clave de idempotencia."""

    INSERT = "INSERT"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class IdempotencyDecision:
    """Decisión de idempotencia para el servicio de evidencia."""

    action: IdempotencyAction
    evidence_id: Optional[str] = None
    existing_sha256: Optional[str] = None
    candidate_sha256: Optional[str] = None


def canonical_json_bytes(payload: Any) -> bytes:
    """Serializa ``payload`` de forma determinista (RFC 8785 simplificado).

    - claves ordenadas;
    - sin espacios;
    - UTF-8;
    - ``NaN``/``Infinity`` prohibidos (no son JSON válido).

    No se usa para *reconstruir* evidencia ya capturada: los bytes originales
    son la fuente de verdad. Se usa para calcular hashes de payloads nuevos y
    para comparar objetos canónicos en tests.
    """
    try:
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("El payload no es serializable como JSON canónico.") from exc
    # ``json.dumps`` puede emitir floats con representación no canónica; para
    # los payloads de evidencia (objetos/strings/números) es suficiente.
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    """SHA-256 hexadecimal en minúsculas."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("sha256_hex requiere bytes.")
    return hashlib.sha256(bytes(data)).hexdigest()


def validate_payload_hash(raw_bytes: bytes, expected_sha256: str) -> str:
    """Comprueba el hash y devuelve el calculado.

    :raises EvidenceHashMismatch: si el hash no coincide.
    """
    actual = sha256_hex(raw_bytes)
    expected = (expected_sha256 or "").strip().lower()
    if actual != expected:
        raise EvidenceHashMismatch(
            f"Hash de evidencia no coincide: esperado={expected!r}, real={actual}"
        )
    return actual


def evidence_idempotency_key(
    *,
    collector_agent: str,
    mission_pir_id: str,
    source_uri: str,
    captured_at_utc: str,
    payload_sha256: str,
) -> str:
    """Calcula la clave de idempotencia de evidencia (SPEC-02 §9).

    La fórmula incluye el agente, la misión/PIR, la fuente, el instante de
    captura y el hash del payload. El servidor la recalcula; nunca confía en la
    clave enviada por el cliente sin verificar el hash.
    """
    canonical = {
        "collector_agent": collector_agent,
        "mission_pir_id": str(mission_pir_id),
        "source_uri": source_uri,
        "captured_at_utc": captured_at_utc,
        "payload_sha256": payload_sha256,
    }
    return sha256_hex(canonical_json_bytes(canonical))


def decide_idempotency(
    existing: Optional[Mapping[str, Any]],
    *,
    candidate_sha256: str,
    candidate_evidence_id: Optional[str] = None,
) -> IdempotencyDecision:
    """Decide qué hacer con una evidencia candidata.

    ``existing`` es la fila previa con la misma ``(collector_agent,
    idempotency_key)``, o ``None``. La comparación es por hash de bytes, no por
    igualdad de diccionarios.
    """
    candidate = (candidate_sha256 or "").lower()
    if existing is None:
        return IdempotencyDecision(
            IdempotencyAction.INSERT,
            evidence_id=candidate_evidence_id,
            candidate_sha256=candidate,
        )
    previous = str(existing.get("payload_sha256") or "").lower()
    evidence_id = str(existing.get("evidence_id") or "") or None
    if previous == candidate:
        return IdempotencyDecision(
            IdempotencyAction.DUPLICATE,
            evidence_id=evidence_id,
            existing_sha256=previous,
            candidate_sha256=candidate,
        )
    return IdempotencyDecision(
        IdempotencyAction.CONFLICT,
        evidence_id=evidence_id,
        existing_sha256=previous,
        candidate_sha256=candidate,
    )


def assert_no_idempotency_conflict(
    existing: Optional[Mapping[str, Any]], *, candidate_sha256: str
) -> IdempotencyDecision:
    """Igual que :func:`decide_idempotency`, pero eleva ``EvidenceConflict``."""
    decision = decide_idempotency(existing, candidate_sha256=candidate_sha256)
    if decision.action is IdempotencyAction.CONFLICT:
        raise EvidenceConflict(
            "La idempotency_key ya existe con un payload distinto "
            f"(existente={decision.existing_sha256}, nuevo={candidate_sha256})."
        )
    return decision


def canonical_attachment_hash(data: bytes, expected_sha256: str) -> str:
    """Verifica el hash de un adjunto antes de subirlo a WORM."""
    if not math.isfinite(len(data)):  # pragma: no cover - len siempre finito
        raise ValueError("Adjunto inválido.")
    return validate_payload_hash(data, expected_sha256)


__all__ = [
    "EvidenceConflict",
    "EvidenceHashMismatch",
    "IdempotencyAction",
    "IdempotencyDecision",
    "assert_no_idempotency_conflict",
    "canonical_attachment_hash",
    "canonical_json_bytes",
    "decide_idempotency",
    "evidence_idempotency_key",
    "sha256_hex",
    "validate_payload_hash",
]
