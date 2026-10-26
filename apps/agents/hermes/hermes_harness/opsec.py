"""Política OPSEC HUMINT (SPEC-02 §8/§12).

La regla es *fail-closed*: si falta un control, la interacción se aborta y se
emite un ``OpsecEvent``. No existe ``WARN`` para una violación; como máximo hay
una decisión ``ALLOW`` con notas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

HIGH_IMPACT_ACTIONS = frozenset({"SEND_MESSAGE", "JOIN_CHANNEL", "CALL"})


@dataclass(frozen=True)
class OpsecContext:
    sockpuppet_active: bool = False
    mission_active: bool = False
    interaction_authorized: bool = False
    legal_basis: str = ""
    conflict_of_interest: bool = False
    target_is_human: bool = True
    target_is_minor: bool = False
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class OpsecDecision:
    decision: str
    reasons: tuple[str, ...] = ()
    risk_level: str = "LOW"

    @property
    def allowed(self) -> bool:
        return self.decision == "ALLOW"


def evaluate_opsec(action: str, context: OpsecContext) -> OpsecDecision:
    """Evalúa una acción HUMINT antes de ejecutarla."""
    action = (action or "").strip().upper()
    reasons: list[str] = []
    if not context.sockpuppet_active:
        reasons.append("SOCKPUPPET_INACTIVE")
    if not context.mission_active:
        reasons.append("MISSION_INACTIVE")
    if not context.interaction_authorized:
        reasons.append("INTERACTION_NOT_AUTHORIZED")
    if context.conflict_of_interest:
        reasons.append("CONFLICT_OF_INTEREST")
    if context.target_is_minor:
        reasons.append("TARGET_MINOR")
    if action in HIGH_IMPACT_ACTIONS and not context.legal_basis.strip():
        reasons.append("LEGAL_BASIS_MISSING")
    if context.target_is_human and action in {"SEND_MESSAGE", "CALL"} and not context.legal_basis.strip():
        if "LEGAL_BASIS_MISSING" not in reasons:
            reasons.append("LEGAL_BASIS_MISSING")

    if reasons:
        return OpsecDecision(
            decision="ABORT",
            reasons=tuple(reasons),
            risk_level="CRITICAL" if "TARGET_MINOR" in reasons else "HIGH",
        )
    return OpsecDecision(decision="ALLOW", reasons=(), risk_level="LOW")


def opsec_event_payload(
    *,
    sockpuppet_id: str,
    interaction_id: str,
    mission_id: str,
    action: str,
    decision: OpsecDecision,
) -> dict[str, object]:
    """Datos mínimos para ``audit.events``/``OpsecEvent``."""
    return {
        "event_type": "OPSEC_CHECK",
        "sockpuppet_id": sockpuppet_id,
        "interaction_id": interaction_id,
        "mission_id": mission_id,
        "action": action,
        "decision": decision.decision,
        "risk_level": decision.risk_level,
        "reasons": list(decision.reasons),
    }


__all__ = [
    "HIGH_IMPACT_ACTIONS",
    "OpsecContext",
    "OpsecDecision",
    "evaluate_opsec",
    "opsec_event_payload",
]
