"""Tests unitarios de los servicios v3.1 (sin Docker).

Cubren las reglas que SPEC-01/02/03 exigen antes de tocar PostgreSQL: hashes
canónicos, idempotencia, resolución por selector, TLP y señales de alias.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from app.services.correlation import (
    AliasObservation,
    ColocationEvent,
    build_alias_signals,
    build_pairs,
    can_read,
    cluster_pairs,
    colocation_score,
    detect_alias_collisions,
    detect_alias_rotation,
    haversine_m,
    mask_selector,
    tlp_rank,
)
from app.services.entity_resolver_v3 import (
    Candidate,
    EntityResolverV3,
    ResolutionStatus,
    decide_from_candidates,
    weighted_score,
)
from app.services.evidence_service import (
    EvidenceConflict,
    EvidenceHashMismatch,
    IdempotencyAction,
    assert_no_idempotency_conflict,
    canonical_json_bytes,
    decide_idempotency,
    sha256_hex,
    validate_payload_hash,
)
from app.services.selector_normalizer import (
    InvalidSelector,
    normalize_selector,
    selector_value_hash,
)


# ---------------------------------------------------------------------------
# Selectores
# ---------------------------------------------------------------------------
def test_username_is_canonicalized_and_hashed():
    assert normalize_selector("USERNAME", "  @JuanPérez ") == "juanpérez"
    h1 = selector_value_hash("USERNAME", "@JuanPérez")
    h2 = selector_value_hash("USERNAME", "juanpérez")
    assert h1 == h2
    assert len(h1) == 64


def test_phone_must_be_e164_and_url_is_canonical():
    assert normalize_selector("PHONE_E164", "+34 600-123-456") == "+34600123456"
    assert normalize_selector("URL", "HTTPS://Example.COM:443/a?b=1#frag") == (
        "https://example.com/a?b=1"
    )
    with pytest.raises(InvalidSelector):
        normalize_selector("PHONE_E164", "600123456")


def test_ip_and_mac_are_canonical():
    assert normalize_selector("IPV6", "2001:0db8::1") == "2001:db8::1"
    assert normalize_selector("MAC_ADDRESS", "aa-bb-cc-dd-ee-ff") == (
        "AA:BB:CC:DD:EE:FF"
    )


def test_unknown_selector_type_is_rejected():
    with pytest.raises(InvalidSelector):
        normalize_selector("PASSPORT", "X123")


# ---------------------------------------------------------------------------
# Evidencia
# ---------------------------------------------------------------------------
def test_canonical_json_is_stable():
    a = canonical_json_bytes({"b": 2, "a": "ñ"})
    b = canonical_json_bytes({"a": "ñ", "b": 2})
    assert a == b
    assert a == b'{"a":"\xc3\xb1","b":2}'


def test_evidence_hash_and_idempotency():
    payload = b"original-bytes"
    digest = sha256_hex(payload)
    assert validate_payload_hash(payload, digest) == digest
    with pytest.raises(EvidenceHashMismatch):
        validate_payload_hash(payload, "0" * 64)

    first = decide_idempotency(None, candidate_sha256=digest)
    assert first.action is IdempotencyAction.INSERT

    duplicate = decide_idempotency(
        {"evidence_id": "e1", "payload_sha256": digest},
        candidate_sha256=digest,
    )
    assert duplicate.action is IdempotencyAction.DUPLICATE
    assert duplicate.evidence_id == "e1"

    with pytest.raises(EvidenceConflict):
        assert_no_idempotency_conflict(
            {"evidence_id": "e1", "payload_sha256": "a" * 64},
            candidate_sha256=digest,
        )


# ---------------------------------------------------------------------------
# Entity Resolution v3
# ---------------------------------------------------------------------------
class _FakeResolutionRepo:
    def __init__(self, selector: int = 0, alias: int = 0, candidates=None):
        self.selector = selector
        self.alias = alias
        self.candidates = candidates or []

    def find_selector_matches(self, observable_type, value_hash, as_of):
        return [{"entity_id": "e-exact"}] * self.selector

    def find_alias_matches(self, normalized_value, as_of):
        return [{"entity_id": "e-alias"}] * self.alias

    def search_candidates(self, query, *, limit=20):
        return self.candidates


def test_exact_selector_wins_without_merge():
    result = EntityResolverV3(_FakeResolutionRepo(selector=1)).resolve(
        observable_type="USERNAME", value="@juan", as_of="2026-10-01T00:00:00Z"
    )
    assert result.status is ResolutionStatus.MATCH_EXACT
    assert result.entity_id == "e-exact"
    assert result.score == 1.0


def test_ambiguous_selector_requires_review():
    result = EntityResolverV3(_FakeResolutionRepo(selector=2)).resolve(
        observable_type="USERNAME", value="@juan", as_of="2026-10-01T00:00:00Z"
    )
    assert result.status is ResolutionStatus.AMBIGUOUS
    assert result.requires_human_review


def test_name_only_never_auto_merges():
    result = decide_from_candidates(
        [Candidate("e1", "Juan Pérez", trigram=1.0, embedding=1.0, context=1.0)]
    )
    # El score máximo sin alias es 0.55; no alcanza ni siquiera el umbral de
    # revisión (0.70), así que se propone entidad nueva, nunca una fusión.
    assert result.status is ResolutionStatus.NEW_STAGED
    assert result.creates_proposal
    assert weighted_score(trigram=1.0, embedding=1.0, context=1.0) < 0.70


def test_locked_candidate_is_always_review():
    result = decide_from_candidates(
        [Candidate("e1", "Juan", alias_exact=1.0, trigram=1.0, locked_by_analyst=True)]
    )
    assert result.status is ResolutionStatus.NEEDS_REVIEW
    assert result.entity_id == "e1"


def test_alias_collision_is_not_a_merge():
    result = EntityResolverV3(_FakeResolutionRepo(alias=2)).resolve(
        observable_type="USERNAME", value="@juan", as_of="2026-10-01T00:00:00Z"
    )
    assert result.status is ResolutionStatus.AMBIGUOUS
    assert "ALIAS_COLLISION" in result.warnings


# ---------------------------------------------------------------------------
# Correlación
# ---------------------------------------------------------------------------
def test_haversine_and_tlp():
    d = haversine_m(40.4168, -3.7038, 40.4178, -3.7038)
    assert 100 < d < 115
    assert can_read("TLP:AMBER", "TLP:GREEN")
    assert not can_read("TLP:GREEN", "TLP:AMBER")
    assert tlp_rank("desconocido") == tlp_rank("TLP:RED")


def test_mask_selector_never_returns_full_sensitive_value():
    masked = mask_selector("IMSI", "123456789012345")
    assert masked == "IMSI:***345"
    assert "123456789012" not in masked
    assert mask_selector("EMAIL", "juan@example.com").startswith("EMAIL:j***@")


def test_colocation_score_is_bounded_and_ordered():
    best = colocation_score(
        distance_m=0, radius_m=500, delta_s=0, max_delta_s=900, avg_confidence=100
    )
    worst = colocation_score(
        distance_m=500, radius_m=500, delta_s=900, max_delta_s=900, avg_confidence=0
    )
    assert 0.99 <= best <= 1.0
    assert worst == 0.0


def _event(eid, discipline, lat, lon, minute, confidence=80):
    return ColocationEvent(
        event_id=eid,
        discipline=discipline,
        event_type="RF_EMISSION" if discipline == "SIGINT" else "SOCIAL_POST",
        event_time=datetime(2026, 10, 1, 21, 0, tzinfo=timezone.utc)
        + timedelta(minutes=minute),
        lat=lat,
        lon=lon,
        confidence=confidence,
        tlp="TLP:AMBER",
    )


def test_pairs_and_clusters_are_deterministic():
    pairs = build_pairs(
        [
            _event("s1", "SIGINT", 40.4168, -3.7038, 0),
            _event("o1", "SOCMINT", 40.4169, -3.7037, 3),
            _event("s2", "SIGINT", 40.5000, -3.6000, 1),
        ],
        radius_m=500,
        max_delta_s=900,
    )
    assert len(pairs) == 1
    assert pairs[0].sigint.event_id == "s1"
    clusters = cluster_pairs(pairs, radius_m=500, max_delta_s=900)
    assert len(clusters) == 1
    assert clusters[0].score > 0.8
    assert clusters[0].events


def test_alias_rotation_and_collision_are_signals():
    start = datetime(2026, 9, 1, tzinfo=timezone.utc)
    observations = [
        AliasObservation(
            "e1", "o1", "HANDLE", "@a", "telegram", start, source_evidence_id="ev1"
        ),
        AliasObservation(
            "e1",
            "o1",
            "HANDLE",
            "@b",
            "telegram",
            start + timedelta(hours=2),
            source_evidence_id="ev2",
        ),
        AliasObservation(
            "e1",
            "o1",
            "HANDLE",
            "@c",
            "telegram",
            start + timedelta(hours=5),
            source_evidence_id="ev3",
        ),
        AliasObservation(
            "e2",
            "o2",
            "HANDLE",
            "@c",
            "x",
            start + timedelta(hours=6),
            source_evidence_id="ev4",
        ),
    ]
    rotation = detect_alias_rotation(
        observations, min_aliases=3, max_gap_hours=48
    )
    assert rotation and rotation[0].signal_type == "RAPID_ALIAS_ROTATION"
    assert rotation[0].score > 0.5
    collision = detect_alias_collisions(observations)
    assert collision and collision[0].signal_type == "ALIAS_COLLISION"
    assert collision[0].occurrences >= 2
    combined = build_alias_signals(observations, min_aliases=3)
    assert {s.signal_type for s in combined} >= {
        "RAPID_ALIAS_ROTATION",
        "ALIAS_COLLISION",
    }
