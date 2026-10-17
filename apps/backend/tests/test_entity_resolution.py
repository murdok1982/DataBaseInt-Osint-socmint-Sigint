"""Tests de aceptación — Entity Resolution (SPEC-01 §4 · §7, v2.0).

Verificación Cruzada (Constitución, Reglas 5/6). Corren sin base de datos real.
"""

from __future__ import annotations

from tests.conftest import FakeRepository

from app.services.entity_resolver import (
    ALERT_NEW_ALIAS_MERGE,
    MERGE,
    NEW,
    PENDING_HUMAN_REVIEW,
    RELATION_WEIGHTS,
    EntityResolver,
    normalize_name,
    similarity_score,
    soundex,
)


# ---------------------------------------------------------------------
# Normalización y fonética (SPEC-01 §4, paso 1)
# ---------------------------------------------------------------------
def test_normalize_strips_accents_and_case():
    assert normalize_name("Juan PÉREZ") == "juan perez"
    assert normalize_name("  J.  Pérez ") == "j perez"


def test_soundex_pure_python():
    assert soundex("Perez") == "P620"
    assert soundex("Pérez") == "P620"
    assert soundex("") == ""


def test_initial_variant_score_at_least_merge_threshold():
    # Requisito explícito: "Juan Pérez" vs "J. Pérez" >= 0.88.
    assert similarity_score("Juan Pérez", "J. Pérez") >= 0.88


# ---------------------------------------------------------------------
# Pesos de riesgo v2.0 (SPEC-01 §4.1)
# ---------------------------------------------------------------------
def test_relation_weights_drop_removed_relations():
    assert "OWNS_DEVICE" not in RELATION_WEIGHTS
    assert "HOLDS_ACCOUNT" not in RELATION_WEIGHTS
    assert RELATION_WEIGHTS["CONTROLS"] == 1.0
    assert RELATION_WEIGHTS["MENTIONED_IN"] == 0.05


# ---------------------------------------------------------------------
# Decisión de resolución (SPEC-01 §4, paso 4)
# ---------------------------------------------------------------------
def test_exact_duplicate_is_fused(repo: FakeRepository, resolver: EntityResolver):
    repo.seed_node("Person", "p1", "Juan Pérez")
    result = resolver.resolve("Person", "Juan Pérez", {})
    assert result.action == MERGE
    assert result.canonical_id == "p1"
    assert result.score >= 0.88


def test_initial_variant_is_fused(repo: FakeRepository, resolver: EntityResolver):
    repo.seed_node("Person", "p1", "Juan Pérez")
    result = resolver.resolve("Person", "J. Pérez", {})
    assert result.score >= 0.88
    assert result.action == MERGE
    assert result.canonical_id == "p1"


def test_grey_zone_goes_to_human_review(repo: FakeRepository, resolver: EntityResolver):
    repo.seed_node("Person", "p1", "Juan Pérez")
    result = resolver.resolve("Person", "Juan Peralta", {})
    assert 0.70 <= result.score < 0.88
    assert result.action == PENDING_HUMAN_REVIEW
    assert result.matched_id == "p1"


def test_low_score_creates_new(repo: FakeRepository, resolver: EntityResolver):
    repo.seed_node("Person", "p1", "Juan Pérez")
    result = resolver.resolve("Person", "Karl Müller", {})
    assert result.score < 0.70
    assert result.action == NEW
    assert result.canonical_id not in (None, "p1")


def test_exact_unique_key_is_fused(repo: FakeRepository, resolver: EntityResolver):
    # SPEC-01 §4, paso 2: match exacto por `value` para CyberAsset.
    repo.seed_node("CyberAsset", "ca1", "1.1.1.1", asset_type="IP", value="1.1.1.1")
    result = resolver.resolve("CyberAsset", "cualquier nombre", {"value": "1.1.1.1"})
    assert result.action == MERGE
    assert result.canonical_id == "ca1"
    assert result.score == 1.0


def test_merge_reassigns_duplicate_edges(repo: FakeRepository, resolver: EntityResolver):
    # Match exacto por clave única (o1) mientras el llamante aporta un `id`
    # duplicado ya materializado (o2): se reasignan sus aristas y se borra.
    repo.seed_node("Organization", "o1", "Acme Corp", registration_id="REG-1")
    repo.seed_node("Organization", "o2", "Duplicado", registration_id="REG-9")
    repo.edges.append(
        {
            "from_id": "o2",
            "to_id": "x1",
            "rel_type": "FINANCED",
            "props": {"confidence": 0.8},
        }
    )
    result = resolver.resolve(
        "Organization", "Acme Corp", {"registration_id": "REG-1", "id": "o2"}
    )
    assert result.action == MERGE
    assert result.canonical_id == "o1"
    assert repo.merge_calls == [("o1", "o2")]
    assert repo.edges[0]["from_id"] == "o1"
    assert "o2" not in repo.nodes


# ---------------------------------------------------------------------
# Alias sin duplicar (SPEC-01 §4, paso 5)
# ---------------------------------------------------------------------
def test_alias_added_without_duplication(repo: FakeRepository, resolver: EntityResolver):
    repo.seed_node("Person", "p1", "Juan Pérez")
    resolver.resolve("Person", "J. Pérez", {})
    resolver.resolve("Person", "J. Pérez", {})
    aliases = repo.nodes["p1"]["aliases"]
    assert aliases.count("J. Pérez") == 1


# ---------------------------------------------------------------------
# Inmutabilidad analítica — locked_by_analyst (SPEC-01 §4 · Regla 2 · §7)
# ---------------------------------------------------------------------
def test_locked_node_is_not_overwritten_and_raises_alert(
    repo: FakeRepository, resolver: EntityResolver
):
    repo.seed_node(
        "Organization",
        "o1",
        "Acme Corp",
        registration_id="REG-1",
        locked_by_analyst=True,
    )
    result = resolver.resolve("Organization", "Acme Corp", {"registration_id": "REG-2"})

    # No se sobrescribe ningún atributo clave ni se añaden alias.
    assert repo.nodes["o1"]["props"]["registration_id"] == "REG-1"
    assert repo.nodes["o1"]["props"]["canonical_name"] == "Acme Corp"
    assert "Acme Corp" not in repo.nodes["o1"]["aliases"]
    # Nunca se recalcula/reescribe risk_score de un nodo bloqueado.
    assert repo.risk_writes == []
    # Se crea un CoherenceAlert (NEW_ALIAS_MERGE) ligado a la entidad.
    assert repo.alerts, "Debe crearse un CoherenceAlert al no poder fusionar."
    assert repo.alerts[-1]["alert_type"] == ALERT_NEW_ALIAS_MERGE
    assert repo.alerts[-1]["entity_id"] == "o1"
    assert result.action == MERGE
    assert result.canonical_id == "o1"


def test_locked_node_initial_variant_does_not_alert(
    repo: FakeRepository, resolver: EntityResolver
):
    # La variante de iniciales NO es una contradicción (SPEC-01 §4 paso 1).
    repo.seed_node("Person", "p1", "Juan Pérez", locked_by_analyst=True)
    result = resolver.resolve("Person", "J. Pérez", {})
    assert result.action == MERGE
    assert repo.alerts == []
    assert repo.nodes["p1"]["props"]["canonical_name"] == "Juan Pérez"


def test_locked_node_keeps_attributes_when_nothing_differs(
    repo: FakeRepository, resolver: EntityResolver
):
    repo.seed_node(
        "Person", "p1", "Juan Pérez", locked_by_analyst=True, nationality="ES"
    )
    resolver.resolve("Person", "Juan Pérez", {"nationality": "FR"})
    # Aunque el llamante aporte un atributo nuevo, no se escribe sobre el nodo.
    assert repo.nodes["p1"]["props"]["nationality"] == "ES"
    assert repo.risk_writes == []


# ---------------------------------------------------------------------
# risk_score idempotente (SPEC-01 §4.1 · §7)
# ---------------------------------------------------------------------
def test_risk_score_is_idempotent(repo: FakeRepository, resolver: EntityResolver):
    repo.seed_node("Person", "p1", "Juan Pérez")
    repo.edges.append(
        {
            "from_id": "p1",
            "to_id": "o1",
            "rel_type": "CONTROLS",
            "props": {"confidence": 0.9},
        }
    )
    first = resolver.recalculate_risk_score("p1", label="Person")
    second = resolver.recalculate_risk_score("p1", label="Person")
    assert first == second
    assert first == 0.9

    # Fusionar un alias no altera el riesgo (mismas aristas) → idempotente.
    resolver.resolve("Person", "J. Pérez", {})
    third = resolver.recalculate_risk_score("p1", label="Person")
    assert third == first


def test_risk_score_is_bounded(repo: FakeRepository, resolver: EntityResolver):
    repo.seed_node("Person", "p1", "Juan Pérez")
    for rel_type, conf in (("CONTROLS", 1.0), ("FINANCED", 1.0), ("USES_ASSET", 1.0)):
        repo.edges.append(
            {"from_id": "p1", "to_id": "x", "rel_type": rel_type, "props": {"confidence": conf}}
        )
    score = resolver.recalculate_risk_score("p1", label="Person")
    assert 0.0 <= score <= 1.0
    # Repetir no acumula ni desborda.
    assert resolver.recalculate_risk_score("p1", label="Person") == score
