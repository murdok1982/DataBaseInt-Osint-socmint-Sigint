"""Motor de correlación v3.1 — colocalización y evasión de alias.

Zona: Agente 2 — ``apps/backend/app/services/**``.

La parte SQL pesada vive en ``contracts/v3/db-contract-v3.1.sql`` y se ejecuta
con PostGIS. Este módulo contiene las reglas **deterministas** que deben poder
probarse sin base de datos: distancia geográfica, score, enmascarado TLP,
clustering y señales de rotación/colisión de alias.

Ninguna función de este módulo crea una relación ni fusiona entidades. Una
señal es una hipótesis; la fusión exige una propuesta y aprobación humana.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Mapping, Optional, Sequence

#: Orden de TLP (menor = más compartible). ``TLP:RED`` no se envía a UI.
TLP_ORDER: dict[str, int] = {
    "TLP:CLEAR": 0,
    "TLP:GREEN": 1,
    "TLP:AMBER": 2,
    "TLP:AMBER+STRICT": 3,
    "TLP:RED": 4,
}

#: Pesos por tipo de alias para la señal de rotación (SPEC-03 §5.4).
ALIAS_TYPE_WEIGHT: dict[str, float] = {
    "HANDLE": 1.0,
    "ACCOUNT_ID": 1.0,
    "PHONE": 0.9,
    "EMAIL": 0.85,
    "WALLET": 0.85,
    "URL": 0.7,
    "DISPLAY_NAME": 0.45,
    "DEVICE_ID": 0.95,
    "OTHER": 0.35,
}


def tlp_rank(tlp: str) -> int:
    """Orden numérico de TLP; valor desconocido se trata como RED (fail-closed)."""
    return TLP_ORDER.get((tlp or "").strip().upper(), TLP_ORDER["TLP:RED"])


def can_read(clearance: str, classification: str) -> bool:
    """``True`` si el clearance alcanza la clasificación (SPEC-03 §1)."""
    return tlp_rank(clearance) >= tlp_rank(classification)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distancia geodésica en metros (esfera WGS-84 media)."""
    if not all(math.isfinite(v) for v in (lat1, lon1, lat2, lon2)):
        raise ValueError("Coordenada no finita.")
    if not (-90.0 <= lat1 <= 90.0 and -90.0 <= lat2 <= 90.0):
        raise ValueError("Latitud fuera de rango.")
    if not (-180.0 <= lon1 <= 180.0 and -180.0 <= lon2 <= 180.0):
        raise ValueError("Longitud fuera de rango.")
    radius = 6_371_008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def colocation_score(
    *,
    distance_m: float,
    radius_m: float,
    delta_s: float,
    max_delta_s: float,
    avg_confidence: float,
) -> float:
    """Score de par SIGINT/SOCMINT (SPEC-03 §3.4)."""
    if radius_m <= 0 or max_delta_s <= 0:
        raise ValueError("radius_m y max_delta_s deben ser positivos.")
    distance_component = max(0.0, 1.0 - max(0.0, distance_m) / radius_m)
    time_component = max(0.0, 1.0 - max(0.0, delta_s) / max(1.0, max_delta_s))
    confidence_component = max(0.0, min(1.0, avg_confidence / 100.0))
    return max(
        0.0,
        min(
            1.0,
            0.50 * distance_component
            + 0.30 * time_component
            + 0.20 * confidence_component,
        ),
    )


def mask_selector(observable_type: str, value: str, *, visible_tail: int = 3) -> str:
    """Enmascara un selector para UI/logs sin revelar el valor completo.

    ``IMSI:123456789012345`` → ``IMSI:***345``. El tipo se conserva para que el
    analista sepa qué está viendo; el valor completo sólo se expone a roles con
    clearance y necesidad de saber.
    """
    kind = (observable_type or "OTHER").upper()
    text = (value or "").strip()
    if not text:
        return f"{kind}:***"
    tail = text[-visible_tail:] if len(text) > visible_tail else ""
    # Para emails, mostrar sólo la primera letra del local-part.
    if kind == "EMAIL" and "@" in text:
        local, domain = text.split("@", 1)
        return f"{kind}:{local[:1]}***@{domain}"
    if kind in {"USERNAME", "DISPLAY_NAME"} and len(text) > 2:
        return f"{kind}:{text[:2]}***"
    return f"{kind}:***{tail}" if tail else f"{kind}:***"


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not isinstance(value, str):
        raise ValueError("Fecha inválida.")
    text = value.strip().replace("Z", "+00:00")
    return datetime.fromisoformat(text)


@dataclass(frozen=True)
class AliasObservation:
    """Una ventana de alias observada (fila de ``observable_alias_history``)."""

    entity_id: str
    observable_id: str
    alias_type: str
    normalized_value: str
    platform: Optional[str]
    observed_from: datetime
    observed_until: Optional[datetime] = None
    source_evidence_id: Optional[str] = None
    confidence: int = 50

    @property
    def weight(self) -> float:
        return ALIAS_TYPE_WEIGHT.get(self.alias_type.upper(), 0.35)


@dataclass(frozen=True)
class AliasSignal:
    """Señal de evasión; no implica fusión."""

    signal_type: str
    entity_id: Optional[str]
    observable_id: Optional[str]
    score: float
    occurrences: int
    aliases: tuple[str, ...] = field(default_factory=tuple)
    platforms: tuple[str, ...] = field(default_factory=tuple)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    max_gap_hours: Optional[float] = None
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)
    explanation: str = ""


def _gap_hours(newer: datetime, older: datetime) -> float:
    return abs((newer - older).total_seconds()) / 3600.0


def detect_alias_rotation(
    observations: Sequence[AliasObservation],
    *,
    min_aliases: int = 3,
    max_gap_hours: float = 48.0,
) -> list[AliasSignal]:
    """Detecta ``RAPID_ALIAS_ROTATION`` por entidad/observable y tipo de alias."""
    if min_aliases < 2:
        raise ValueError("min_aliases debe ser >= 2.")
    grouped: dict[tuple[str, str, str], list[AliasObservation]] = {}
    for obs in observations:
        grouped.setdefault(
            (obs.entity_id, obs.observable_id, obs.alias_type.upper()), []
        ).append(obs)

    signals: list[AliasSignal] = []
    for (entity_id, observable_id, alias_type), rows in grouped.items():
        ordered = sorted(rows, key=lambda r: r.observed_from)
        if not ordered:
            continue
        # ``min_aliases`` cuenta valores distintos observados, incluido el
        # primero; los ``gaps`` sólo existen entre cambios consecutivos.
        rotations: list[AliasObservation] = [ordered[0]]
        previous: AliasObservation = ordered[0]
        for row in ordered[1:]:
            if row.normalized_value != previous.normalized_value:
                gap = _gap_hours(row.observed_from, previous.observed_from)
                if gap <= max_gap_hours:
                    rotations.append(row)
            previous = row
        if len(rotations) < min_aliases:
            continue
        weight = ALIAS_TYPE_WEIGHT.get(alias_type, 0.35)
        base_score = min(1.0, len(rotations) / 5.0)
        score = round(min(1.0, base_score * (0.6 + 0.4 * weight)), 4)
        gaps = [
            _gap_hours(rotations[i].observed_from, rotations[i - 1].observed_from)
            for i in range(1, len(rotations))
        ]
        aliases = tuple(dict.fromkeys(r.normalized_value for r in rotations))
        platforms = tuple(
            dict.fromkeys(r.platform for r in rotations if r.platform)
        )
        evidence = tuple(
            dict.fromkeys(
                r.source_evidence_id for r in rotations if r.source_evidence_id
            )
        )
        signals.append(
            AliasSignal(
                signal_type="RAPID_ALIAS_ROTATION",
                entity_id=entity_id,
                observable_id=observable_id,
                score=score,
                occurrences=len(rotations),
                aliases=aliases,
                platforms=platforms,
                first_seen=rotations[0].observed_from,
                last_seen=rotations[-1].observed_from,
                max_gap_hours=round(max(gaps), 3) if gaps else None,
                evidence_ids=evidence,
                explanation=(
                    f"{len(rotations)} cambios de {alias_type.lower()} en "
                    f"{round(sum(gaps), 1)} horas; patrón compatible con evasión."
                ),
            )
        )
    return sorted(signals, key=lambda s: (-s.score, s.entity_id or ""))


def detect_cross_platform_reuse(
    observations: Sequence[AliasObservation], *, min_platforms: int = 2
) -> list[AliasSignal]:
    """Detecta reutilización del mismo alias en varias plataformas."""
    if min_platforms < 2:
        raise ValueError("min_platforms debe ser >= 2.")
    grouped: dict[tuple[str, str], list[AliasObservation]] = {}
    for obs in observations:
        grouped.setdefault((obs.entity_id, obs.observable_id), []).append(obs)
    signals: list[AliasSignal] = []
    for (entity_id, observable_id), rows in grouped.items():
        platforms = tuple(dict.fromkeys(r.platform for r in rows if r.platform))
        if len(platforms) < min_platforms:
            continue
        aliases = tuple(dict.fromkeys(r.normalized_value for r in rows))
        signals.append(
            AliasSignal(
                signal_type="CROSS_PLATFORM_REUSE",
                entity_id=entity_id,
                observable_id=observable_id,
                score=round(min(1.0, len(platforms) / 3.0), 4),
                occurrences=len(rows),
                aliases=aliases,
                platforms=platforms,
                first_seen=min(r.observed_from for r in rows),
                last_seen=max(r.observed_from for r in rows),
                evidence_ids=tuple(
                    dict.fromkeys(
                        r.source_evidence_id for r in rows if r.source_evidence_id
                    )
                ),
                explanation=f"Mismo alias en {len(platforms)} plataformas.",
            )
        )
    return signals


def detect_alias_collisions(
    observations: Sequence[AliasObservation],
) -> list[AliasSignal]:
    """Detecta el mismo alias normalizado en >1 entidad (``ALIAS_COLLISION``)."""
    grouped: dict[str, list[AliasObservation]] = {}
    for obs in observations:
        grouped.setdefault(obs.normalized_value, []).append(obs)
    signals: list[AliasSignal] = []
    for normalized, rows in grouped.items():
        entities = tuple(dict.fromkeys(r.entity_id for r in rows))
        if len(entities) <= 1:
            continue
        signals.append(
            AliasSignal(
                signal_type="ALIAS_COLLISION",
                entity_id=None,
                observable_id=None,
                score=round(min(1.0, len(entities) / 4.0), 4),
                occurrences=len(entities),
                aliases=(normalized,),
                platforms=tuple(
                    dict.fromkeys(r.platform for r in rows if r.platform)
                ),
                first_seen=min(r.observed_from for r in rows),
                last_seen=max(r.observed_from for r in rows),
                evidence_ids=tuple(
                    dict.fromkeys(
                        r.source_evidence_id for r in rows if r.source_evidence_id
                    )
                ),
                explanation=(
                    f"El alias {normalized!r} aparece en {len(entities)} entidades; "
                    "es una señal, no una prueba de identidad."
                ),
            )
        )
    return signals


def build_alias_signals(
    observations: Sequence[AliasObservation],
    *,
    min_aliases: int = 3,
    min_platforms: int = 2,
    max_gap_hours: float = 48.0,
    limit: int = 100,
) -> list[AliasSignal]:
    """Combina las tres señales y aplica el límite de la consulta."""
    signals = (
        detect_alias_rotation(
            observations, min_aliases=min_aliases, max_gap_hours=max_gap_hours
        )
        + detect_cross_platform_reuse(
            observations, min_platforms=min_platforms
        )
        + detect_alias_collisions(observations)
    )
    return sorted(
        signals, key=lambda s: (-s.score, s.entity_id or "", s.signal_type)
    )[: max(0, limit)]


# ---------------------------------------------------------------------------
# Clustering de pares de colocalización
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ColocationEvent:
    event_id: str
    discipline: str
    event_type: str
    event_time: datetime
    lat: float
    lon: float
    confidence: int
    tlp: str
    entity_id: Optional[str] = None
    observable_id: Optional[str] = None
    masked_selector: Optional[str] = None
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ColocationPair:
    sigint: ColocationEvent
    socmint: ColocationEvent
    distance_m: float
    delta_s: float
    score: float
    same_evidence: bool = False


def build_pairs(
    events: Sequence[ColocationEvent],
    *,
    radius_m: float,
    max_delta_s: float,
    min_score: float = 0.0,
) -> list[ColocationPair]:
    """Empareja SIGINT con SOCMINT/OSINT por distancia y ventana temporal."""
    sigint = [e for e in events if e.discipline.upper() == "SIGINT"]
    other = [
        e for e in events if e.discipline.upper() in {"SOCMINT", "OSINT"}
    ]
    pairs: list[ColocationPair] = []
    for s in sigint:
        for o in other:
            if s.event_id == o.event_id:
                continue
            distance = haversine_m(s.lat, s.lon, o.lat, o.lon)
            if distance > radius_m:
                continue
            delta = abs((s.event_time - o.event_time).total_seconds())
            if delta > max_delta_s:
                continue
            score = colocation_score(
                distance_m=distance,
                radius_m=radius_m,
                delta_s=delta,
                max_delta_s=max_delta_s,
                avg_confidence=(s.confidence + o.confidence) / 2.0,
            )
            if score < min_score:
                continue
            pairs.append(
                ColocationPair(
                    sigint=s,
                    socmint=o,
                    distance_m=round(distance, 2),
                    delta_s=round(delta, 2),
                    score=score,
                    same_evidence=bool(
                        set(s.evidence_ids) & set(o.evidence_ids)
                    ),
                )
            )
    return sorted(pairs, key=lambda p: (-p.score, p.delta_s))


@dataclass
class ColocationCluster:
    cluster_id: str
    center: tuple[float, float]
    radius_m: float
    score: float
    events: list[ColocationEvent]
    time_delta_s: float
    evidence_ids: tuple[str, ...]


def cluster_pairs(
    pairs: Sequence[ColocationPair], *, radius_m: float, max_delta_s: float
) -> list[ColocationCluster]:
    """Agrupa pares por proximidad/tiempo (greedy, determinista).

    La UI necesita clusters estables; el orden de entrada se conserva y el
    ``cluster_id`` es un hash corto de los eventos, no un UUID aleatorio.
    """
    clusters: list[ColocationCluster] = []
    used: set[str] = set()
    for pair in pairs:
        if pair.sigint.event_id in used and pair.socmint.event_id in used:
            continue
        members = {pair.sigint.event_id, pair.socmint.event_id}
        total_score = pair.score
        count = 1
        for other in pairs:
            if other is pair:
                continue
            ids = {other.sigint.event_id, other.socmint.event_id}
            if ids & members:
                members |= ids
                total_score += other.score
                count += 1
        used |= members
        evs = [
            e
            for e in (
                pair.sigint,
                pair.socmint,
            )
        ]
        # Añade eventos de los pares absorbidos (sin duplicar).
        for other in pairs:
            if other is pair:
                continue
            if {other.sigint.event_id, other.socmint.event_id} & members:
                evs.extend([other.sigint, other.socmint])
        unique: dict[str, ColocationEvent] = {e.event_id: e for e in evs}
        unique_events = list(unique.values())
        center = (
            sum(e.lat for e in unique_events) / len(unique_events),
            sum(e.lon for e in unique_events) / len(unique_events),
        )
        max_distance = max(
            haversine_m(center[0], center[1], e.lat, e.lon)
            for e in unique_events
        )
        times = [e.event_time for e in unique_events]
        delta = (max(times) - min(times)).total_seconds()
        evidence = tuple(
            dict.fromkeys(
                ev for e in unique_events for ev in e.evidence_ids
            )
        )
        cluster_key = "|".join(sorted(unique))
        # ``hash()`` cambia por proceso (PYTHONHASHSEED); el ID debe ser estable.
        cluster_id = "coloc-" + hashlib.sha256(
            cluster_key.encode("utf-8")
        ).hexdigest()[:12]
        clusters.append(
            ColocationCluster(
                cluster_id=cluster_id,
                center=center,
                radius_m=round(max(max_distance, 1.0), 2),
                score=round(total_score / max(1, count), 4),
                events=unique_events,
                time_delta_s=round(delta, 2),
                evidence_ids=evidence,
            )
        )
    return sorted(clusters, key=lambda c: (-c.score, c.cluster_id))


def mask_events(events: Iterable[ColocationEvent]) -> list[dict[str, object]]:
    """Serializa eventos sin exponer selectores completos."""
    result: list[dict[str, object]] = []
    for event in events:
        result.append(
            {
                "event_id": event.event_id,
                "discipline": event.discipline,
                "event_type": event.event_type,
                "event_time": event.event_time.isoformat(),
                "confidence": event.confidence,
                "tlp": event.tlp,
                "entity_id": event.entity_id,
                "observable_id": event.observable_id,
                "masked_selector": event.masked_selector,
                "evidence_ids": list(event.evidence_ids),
            }
        )
    return result


__all__ = [
    "ALIAS_TYPE_WEIGHT",
    "AliasObservation",
    "AliasSignal",
    "ColocationCluster",
    "ColocationEvent",
    "ColocationPair",
    "TLP_ORDER",
    "build_alias_signals",
    "build_pairs",
    "can_read",
    "cluster_pairs",
    "colocation_score",
    "detect_alias_collisions",
    "detect_alias_rotation",
    "detect_cross_platform_reuse",
    "haversine_m",
    "mask_events",
    "mask_selector",
    "tlp_rank",
]
