"""Entity Resolution — Regla de "Rehacer Relaciones".

Autorizado por: **SPEC-01 §4** (Algoritmo de Entity Resolution).
Zona: Agente 1 — ``apps/backend/app/services/entity_resolver.py``.

Implementado **solo con la stdlib** para poder testearse offline (sin Neo4j ni
librerías de NLP). El repositorio se inyecta por *duck-typing* (interfaz
compatible con :class:`app.db.repository.GraphRepository`).

Score de similitud (0.0–1.0) — combinación documentada
-----------------------------------------------------
``score = 0.50 · token_similarity + 0.25 · soundex_similarity + 0.25 · sequence_ratio``

- ``token_similarity``: emparejamiento greedy token a token. Un token de una
  letra (inicial, p. ej. ``"j."``) coincide con un token completo que empiece por
  esa letra (``"juan"``); el resto usa ``difflib.SequenceMatcher``.
- ``soundex_similarity``: Soundex compacto por token, comparado con
  ``SequenceMatcher`` (captura variantes fonéticas).
- ``sequence_ratio``: ``SequenceMatcher`` sobre el nombre normalizado completo.

Decisión (SPEC-01 §4, paso 4):
- match exacto por clave única / nombre normalizado → ``MERGE``
- ``score >= 0.88`` → ``MERGE`` (fusión de alias + re-MERGE de aristas)
- ``0.70 <= score < 0.88`` → ``PENDING_HUMAN_REVIEW``
- ``score < 0.70`` → ``NEW``
- coincidencia con ``locked_by_analyst = true`` → **no** fusiona/sobrescribe;
  enlaza aristas y crea ``CoherenceAlert`` si un atributo clave difiere.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Iterable, Optional, Sequence

# --- Acciones de resolución (SPEC-01 §4) ------------------------------
MERGE = "MERGE"
PENDING_HUMAN_REVIEW = "PENDING_HUMAN_REVIEW"
NEW = "NEW"

# --- Umbrales (SPEC-01 §4) --------------------------------------------
MERGE_THRESHOLD = 0.88
REVIEW_THRESHOLD = 0.70

# --- Pesos del score (suman 1.0) --------------------------------------
WEIGHT_TOKEN = 0.50
WEIGHT_SOUNDEX = 0.25
WEIGHT_SEQUENCE = 0.25

#: Peso de sensibilidad por tipo de arista para el ``risk_score`` (SPEC-01 §4.1,
#: tabla normativa). v2.0 retira ``OWNS_DEVICE`` y ``HOLDS_ACCOUNT`` (consolidados
#: en ``USES_ASSET`` sobre ``CyberAsset``).
RELATION_WEIGHTS: dict[str, float] = {
    "CONTROLS": 1.0,
    "FINANCED": 0.9,
    "USES_ASSET": 0.7,
    "ASSOCIATED_WITH": 0.5,
    "PARTICIPATED_IN": 0.4,
    "LOCATED_AT": 0.2,
    "MENTIONED_IN": 0.05,
}

#: Labels que poseen ``risk_score`` en SPEC-01 §1 (``Person`` lo declara;
#: se incluye ``Organization`` por simetría de riesgo). Ver SPEC-GAP-02.
RISK_LABELS: tuple[str, ...] = ("Person", "Organization")

#: Labels con propiedad ``canonical_name`` (SPEC-01 §1).
CANONICAL_NAME_LABELS: tuple[str, ...] = ("Person", "Organization")

#: Atributos clave por label (SPEC-01 §1/§4.2). Si un nodo bloqueado recibe un
#: valor distinto para uno de ellos, se levanta un ``CoherenceAlert`` en vez de
#: sobrescribir (Regla 2).
KEY_ATTRIBUTES: dict[str, tuple[str, ...]] = {
    "Person": ("canonical_name",),
    "Organization": ("canonical_name", "registration_id"),
    "CyberAsset": ("value", "asset_type"),
    "Location": ("name",),
    "Event": ("title",),
}

#: Tipo de alerta levantada al no poder fusionar por inmutabilidad analítica.
ALERT_NEW_ALIAS_MERGE = "NEW_ALIAS_MERGE"

#: Atributos cuyo "diferir" se evalúa por similitud (iniciales incluidas,
#: SPEC-01 §4 paso 1). El resto se compara por igualdad normalizada.
_NAME_LIKE_KEYS: frozenset[str] = frozenset(
    {"canonical_name", "name", "title", "value"}
)

_SOUNDEX_MAP: dict[str, str] = {
    "b": "1", "f": "1", "p": "1", "v": "1",
    "c": "2", "g": "2", "j": "2", "k": "2", "q": "2", "s": "2", "x": "2", "z": "2",
    "d": "3", "t": "3",
    "l": "4",
    "m": "5", "n": "5",
    "r": "6",
}

_INITIAL_PREFIXES = re.compile(r"^[a-z]\.?$")


# =====================================================================
# Normalización
# =====================================================================
def strip_accents(text: str) -> str:
    """Elimina diacríticos (``"Pérez"`` → ``"Perez"``)."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(name: str) -> str:
    """Normaliza un nombre: lowercase, sin acentos, sin puntuación, sin iniciales
    sueltas redundantes.

    SPEC-01 §4, paso 1. Ejemplo: ``"J. Pérez"`` → ``"j perez"``.
    """
    text = strip_accents(name or "").lower()
    text = text.replace("-", " ").replace("_", " ")
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def tokenize(name: str) -> list[str]:
    """Tokens normalizados de un nombre."""
    normalized = normalize_name(name)
    return normalized.split() if normalized else []


def is_initial(token: str) -> bool:
    """Indica si un token es una inicial (una letra, con o sin punto)."""
    return bool(_INITIAL_PREFIXES.match(token))


# =====================================================================
# Fonética — Soundex compacto (puro Python)
# =====================================================================
def soundex(word: str) -> str:
    """Soundex de una palabra, 4 caracteres (``"PERez"`` → ``"P620"``)."""
    letters = re.sub(r"[^a-zA-Z]", "", word or "").lower()
    if not letters:
        return ""
    result = letters[0].upper()
    previous = _SOUNDEX_MAP.get(letters[0], "")
    for char in letters[1:]:
        code = _SOUNDEX_MAP.get(char, "")
        if char in "hw":
            continue  # H/W no reinician la clave anterior
        if code != previous and code:
            result += code
        previous = code
    return (result + "000")[:4]


def _soundex_name(name: str) -> str:
    return " ".join(soundex(token) for token in tokenize(name))


# =====================================================================
# Similitud
# =====================================================================
def _pair_score(left: str, right: str) -> float:
    """Similitud entre dos tokens, contemplando iniciales."""
    if left == right:
        return 1.0
    if is_initial(left) and right.startswith(left):
        return 1.0  # "j" es abreviatura de "juan"
    if is_initial(right) and left.startswith(right):
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _token_similarity(a_tokens: Sequence[str], b_tokens: Sequence[str]) -> float:
    """Similitud de tokens por emparejamiento greedy (0.0–1.0)."""
    if not a_tokens or not b_tokens:
        return 0.0
    remaining = list(b_tokens)
    total = 0.0
    for token in a_tokens:
        best_index, best_score = -1, -1.0
        for index, candidate in enumerate(remaining):
            candidate_score = _pair_score(token, candidate)
            if candidate_score > best_score:
                best_index, best_score = index, candidate_score
        if best_index >= 0:
            total += best_score
            remaining.pop(best_index)
    return total / max(len(a_tokens), len(b_tokens))


def similarity_score(name_a: str, name_b: str) -> float:
    """Score combinado 0.0–1.0 entre dos nombres (ver docstring del módulo)."""
    a_tokens, b_tokens = tokenize(name_a), tokenize(name_b)
    token = _token_similarity(a_tokens, b_tokens)
    soundex_ratio = SequenceMatcher(None, _soundex_name(name_a), _soundex_name(name_b)).ratio()
    sequence_ratio = SequenceMatcher(None, " ".join(a_tokens), " ".join(b_tokens)).ratio()
    return round(
        WEIGHT_TOKEN * token + WEIGHT_SOUNDEX * soundex_ratio + WEIGHT_SEQUENCE * sequence_ratio,
        4,
    )


# =====================================================================
# Riesgo (idempotente)
# =====================================================================
def compute_risk_score(relationships: Iterable[Any]) -> float:
    """Recalcula ``risk_score`` desde las aristas — **función pura e idempotente**.

    ``risk_score = min(1.0, Σ confidence_i · weight(rel_type_i))`` (SPEC-01 §4,
    paso 5). Al depender solo del grafo, recalcular N veces da el mismo valor.
    """
    total = 0.0
    for relationship in relationships or []:
        if isinstance(relationship, dict):
            rel_type = relationship.get("rel_type") or relationship.get("type")
            props = relationship.get("props") or relationship
        else:  # pragma: no cover - forma alternativa (tupla)
            rel_type, props = relationship[0], relationship[1]
        confidence = float((props or {}).get("confidence", 0.0) or 0.0)
        total += confidence * RELATION_WEIGHTS.get(rel_type, 0.0)
    return round(min(1.0, total), 4)


# =====================================================================
# Modelo de resultado
# =====================================================================
@dataclass(frozen=True)
class ResolutionResult:
    """Resultado de resolver una entidad (SPEC-01 §4)."""

    action: str
    canonical_id: Optional[str]
    score: float
    matched_id: Optional[str] = None
    alias: Optional[str] = None


# =====================================================================
# Resolutor
# =====================================================================
class EntityResolver:
    """Aplica el algoritmo de Entity Resolution de SPEC-01 §4.

    :param repository: objeto con la interfaz de ``GraphRepository``
        (``find_by_unique_key``, ``fulltext_search``, ``upsert_entity``,
        ``merge_duplicates``, ``list_relationships``, ``set_risk_score``).
    """

    def __init__(
        self,
        repository: Any,
        *,
        merge_threshold: float = MERGE_THRESHOLD,
        review_threshold: float = REVIEW_THRESHOLD,
    ) -> None:
        self.repository = repository
        self.merge_threshold = merge_threshold
        self.review_threshold = review_threshold

    # -- API principal -------------------------------------------------
    def resolve(
        self, label: str, name: str, attrs: Optional[dict[str, Any]] = None
    ) -> ResolutionResult:
        """Resuelve una entidad extraída contra el grafo (SPEC-01 §4)."""
        attrs = dict(attrs or {})

        # Paso 2 — match exacto por clave única (SPEC-01 §4.2).
        for key in ("value", "registration_id", "code", "id"):
            candidate_value = attrs.get(key)
            if candidate_value:
                hit = self.repository.find_by_unique_key(label, key, candidate_value)
                if hit:
                    return self._fuse(hit, label, name, attrs, score=1.0)

        # Paso 3 — match difuso (full-text index + score combinado).
        candidates = self.repository.fulltext_search(name, label=label) or []
        best_node: Optional[dict[str, Any]] = None
        best_score = 0.0
        for node in candidates:
            candidate_score = self._node_score(name, node)
            if candidate_score > best_score:
                best_node, best_score = node, candidate_score

        if best_node is None:
            return self._create(label, name, attrs)

        # Paso 4 — regla de decisión.
        if best_score >= self.merge_threshold:
            return self._fuse(best_node, label, name, attrs, score=best_score)
        if best_score >= self.review_threshold:
            matched_id = best_node.get("id")
            return ResolutionResult(
                PENDING_HUMAN_REVIEW,
                canonical_id=matched_id,
                score=best_score,
                matched_id=matched_id,
                alias=name,
            )
        return self._create(label, name, attrs)

    # -- Recalculo de riesgo -------------------------------------------
    def recalculate_risk_score(
        self, entity_id: str, label: str = "Person"
    ) -> Optional[float]:
        """Recalcula y persiste ``risk_score`` de forma idempotente (SPEC-01 §4)."""
        if label not in RISK_LABELS:
            return None
        relationships = self.repository.list_relationships(entity_id) or []
        risk_score = compute_risk_score(relationships)
        self.repository.set_risk_score(entity_id, risk_score)
        return risk_score

    # -- Internos ------------------------------------------------------
    def _node_score(self, name: str, node: dict[str, Any]) -> float:
        """Mejor score entre el nombre dado y los identificadores del nodo."""
        normalized = normalize_name(name)
        candidates: list[str] = []
        for field in ("canonical_name", "name", "value"):
            value = node.get(field)
            if value:
                candidates.append(str(value))
        for alias in node.get("aliases") or []:
            candidates.append(str(alias))
        best = 0.0
        for candidate in candidates:
            if normalize_name(candidate) == normalized:
                return 1.0
            best = max(best, similarity_score(name, candidate))
        return best

    def _fuse(
        self,
        node: dict[str, Any],
        label: str,
        name: str,
        attrs: dict[str, Any],
        score: float,
    ) -> ResolutionResult:
        """Fusiona la entidad entrante con el nodo canónico (SPEC-01 §4, paso 5).

        Si el nodo canónico está ``locked_by_analyst`` (Regla 2 / contrato §7
        CASE B), **no** se sobrescriben sus atributos ni sus alias: solo se
        enlazan las nuevas aristas al nodo existente y, si un atributo clave
        difiere, se levanta un ``CoherenceAlert`` (``NEW_ALIAS_MERGE``).
        """
        canonical_id = node["id"]
        incoming_id = attrs.get("id")
        if incoming_id and incoming_id != canonical_id:
            # Si el llamante ya materializó un duplicado, se reasignan sus aristas
            # al canónico (no se copian propiedades del duplicado).
            self.repository.merge_duplicates(canonical_id, incoming_id)

        if bool(node.get("locked_by_analyst")):
            self._raise_locked_alert(
                canonical_id, label, name, attrs, node=node
            )
            return ResolutionResult(
                MERGE,
                canonical_id=canonical_id,
                score=round(score, 4),
                matched_id=canonical_id,
                alias=name,
            )

        props = {
            key: value
            for key, value in attrs.items()
            if key not in ("aliases", "id")
        }
        # Añade la variante del nombre al array `aliases` sin duplicar.
        self.repository.upsert_entity(label, canonical_id, props, alias=name)
        self.recalculate_risk_score(canonical_id, label=label)
        return ResolutionResult(
            MERGE,
            canonical_id=canonical_id,
            score=round(score, 4),
            matched_id=canonical_id,
            alias=name,
        )

    def _key_values_equal(self, key: str, current: Any, new: Any) -> bool:
        """¿Son equivalentes dos valores de un atributo clave? (SPEC-01 §4 paso 1).

        Los atributos de tipo nombre se comparan también por similitud (para no
        marcar como conflicto la variante de iniciales ``"J. Pérez"`` ↔
        ``"Juan Pérez"``); el resto, por igualdad normalizada.
        """
        if normalize_name(str(current)) == normalize_name(str(new)):
            return True
        if key in _NAME_LIKE_KEYS:
            try:
                if similarity_score(str(current), str(new)) >= self.merge_threshold:
                    return True
            except Exception:  # pragma: no cover - comparación defensiva
                return False
        return False

    def _raise_locked_alert(
        self,
        canonical_id: str,
        label: str,
        name: str,
        attrs: dict[str, Any],
        *,
        node: dict[str, Any],
    ) -> Optional[dict[str, Any]]:
        """Crea un ``CoherenceAlert`` si un atributo clave difiere (Regla 2).

        Solo se alerta cuando el nodo **ya posee** el atributo con un valor
        distinto (una mera ausencia no es una contradicción).
        """
        keys = KEY_ATTRIBUTES.get(label, ())
        incoming = {key: attrs.get(key) for key in keys if attrs.get(key)}
        if "canonical_name" in keys and name:
            incoming.setdefault("canonical_name", name)

        conflicts: list[tuple[str, Any, Any]] = []
        for key, new_value in incoming.items():
            if key == "canonical_name" and label in CANONICAL_NAME_LABELS:
                current_value = node.get("canonical_name")
            else:
                current_value = node.get(key)
            if current_value in (None, ""):
                continue
            if self._key_values_equal(key, current_value, new_value):
                continue
            conflicts.append((key, current_value, new_value))

        if not conflicts:
            return None

        detail = "; ".join(
            f"atributo clave '{key}': {current!r} vs {new!r}"
            for key, current, new in conflicts
        )
        description = (
            f"El nodo '{canonical_id}' ({label}) está bloqueado por el analista; "
            f"no se fusiona ni sobrescribe. Diferencias detectadas: {detail}."
        )
        create_alert = getattr(self.repository, "create_alert", None)
        if create_alert is None:  # pragma: no cover - repositorio mínimo
            return None
        return create_alert(
            ALERT_NEW_ALIAS_MERGE,
            description,
            entity_id=canonical_id,
        )

    def _create(
        self, label: str, name: str, attrs: dict[str, Any]
    ) -> ResolutionResult:
        """Crea una entidad nueva cuando no hay coincidencia (SPEC-01 §4)."""
        new_id = attrs.get("id") or str(uuid.uuid4())
        props = {key: value for key, value in attrs.items() if key not in ("aliases",)}
        props["id"] = new_id
        if label in CANONICAL_NAME_LABELS and "canonical_name" not in props:
            props["canonical_name"] = name
        self.repository.upsert_entity(label, new_id, props, alias=name)
        return ResolutionResult(
            NEW, canonical_id=new_id, score=0.0, matched_id=None, alias=name
        )


__all__ = [
    "MERGE",
    "NEW",
    "PENDING_HUMAN_REVIEW",
    "MERGE_THRESHOLD",
    "REVIEW_THRESHOLD",
    "RELATION_WEIGHTS",
    "RISK_LABELS",
    "CANONICAL_NAME_LABELS",
    "KEY_ATTRIBUTES",
    "ALERT_NEW_ALIAS_MERGE",
    "ResolutionResult",
    "EntityResolver",
    "strip_accents",
    "normalize_name",
    "tokenize",
    "is_initial",
    "soundex",
    "similarity_score",
    "compute_risk_score",
]
