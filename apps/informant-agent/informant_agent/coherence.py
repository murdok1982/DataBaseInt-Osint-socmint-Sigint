"""Motor de referencia analítica (**lado Informante**).

Evalúa cada documento frente a las ``thesis_summary`` de los ``Analysis`` activos
y las preguntas de los ``IntelRequirement`` abiertos, y emite
``analytical_context_links`` con ``link_category ∈ {CORROBORATES, CONTRADICTS,
EXTENDS, ANSWERS_REQUIREMENT}`` (SPEC-02 §1 Fase 1 / §2, prompt Agente 5 punto 3).

Garantías
---------
* **Nunca** sobrescribe la tesis humana: solo produce un *vínculo* con
  ``coherence_justification``; la decisión (``CoherenceAlert``, score, etc.) es del
  backend (SPEC-02 §1 Fase 2).
* ``exact_quote`` es siempre un **fragmento literal** del documento (oración de la
  página que mejor solapa con la tesis/RFI).
* Determinista y sin red: heurísticas explícitas y testeables.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence

from .collectors.base import Page, RawDocument
from .models import PreflightResponse

#: Marcadores de contradicción/negación (ES/EN).
CONTRADICTION_CUES = re.compile(
    r"\b(?:no|not|nunca|never|niega|neg[oó]|denies|denied|deniega|deneg[oó]|"
    r"refuta|refutes|refuted|desmiente|desminti[oó]|false|falso|falsa|falsely|"
    r"unfounded|infundad[oa]|contradice|contradicts|contrary|contrario|"
    r"contraria|did\s+not|does\s+not|is\s+not|was\s+not|were\s+not|"
    r"without\s+evidence|sin\s+pruebas|rechaza|rejects|denied)\b",
    re.IGNORECASE,
)

#: Marcadores de confirmación/evidencia.
SUPPORT_CUES = re.compile(
    r"\b(?:confirma|confirm[oó]?|confirmed|corrobora|corroborates|verificado|"
    r"verified|verified|prueba|pruebas|evidence|evidencia|documenta|documents|"
    r"demuestra|shows|revela|reveals|financi[oó]|financed|funded|financia)\b",
    re.IGNORECASE,
)

#: Palabras vacías (ES/EN) que no aportan señal de solapamiento.
STOPWORDS = frozenset(
    """
    a al an and ante bajo con contra de del desde el en entre es esta este esto
    for from in is la las le les lo los más no of o on or para por que se sin
    sobre the to un una uno y a's as at be by has have it its that their there
    these this those was were will with within without
    """.split()
)

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True)
class Sentence:
    """Oración de una página con su localización (para ``exact_quote``)."""

    text: str
    page_number: int
    tokens: frozenset[str]
    contradicts: bool
    supports: bool


def strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_token(token: str) -> str:
    """Normaliza un token: minúsculas, sin acentos y con *stemming* ligero."""
    token = strip_accents(token).lower()
    for suffix in ("es", "s", "ed", "ing", "d"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def tokenize(text: str) -> frozenset[str]:
    """Conjunto de tokens significativos (normalizados, sin *stopwords*)."""
    raw = re.split(r"[^\wáéíóúüñÁÉÍÓÚÜÑ]+", strip_accents(text).lower())
    return frozenset(
        normalize_token(tok)
        for tok in raw
        if tok and tok not in STOPWORDS and len(tok) > 2
    )


def split_sentences(pages: Iterable[Page]) -> list[Sentence]:
    """Divide las páginas en oraciones con su página de origen."""
    out: list[Sentence] = []
    for page in pages:
        for match in _SENTENCE_RE.finditer(page.text):
            raw = match.group(0)
            text = raw.strip()
            if not text:
                continue
            out.append(
                Sentence(
                    text=text,
                    page_number=page.page_number,
                    tokens=tokenize(text),
                    contradicts=bool(CONTRADICTION_CUES.search(text)),
                    supports=bool(SUPPORT_CUES.search(text)),
                )
            )
    return out


class LinkAnalyzer:
    """Evalúa un documento frente a Análisis/RFIs y produce vínculos analíticos."""

    def __init__(
        self,
        *,
        min_analysis_overlap: int = 2,
        min_requirement_overlap: int = 2,
    ):
        self.min_analysis_overlap = max(1, min_analysis_overlap)
        self.min_requirement_overlap = max(1, min_requirement_overlap)

    # ------------------------------------------------------------------ #
    # API pública
    # ------------------------------------------------------------------ #
    def evaluate(
        self, document: RawDocument, context: PreflightResponse
    ) -> list[dict]:
        """Devuelve ``analytical_context_links`` (dicts validables por el contrato)."""
        sentences = split_sentences(document.pages)
        if not sentences:
            return []

        links: list[dict] = []
        for analysis in context.analyses:
            link = self._evaluate_analysis(analysis, sentences)
            if link is not None:
                links.append(link)
        for requirement in context.requirements:
            link = self._evaluate_requirement(requirement, sentences)
            if link is not None:
                links.append(link)
        return links

    # ------------------------------------------------------------------ #
    # Análisis humanos
    # ------------------------------------------------------------------ #
    def _evaluate_analysis(self, analysis, sentences: Sequence[Sentence]) -> dict | None:
        analysis_id = getattr(analysis, "id", None)
        thesis = getattr(analysis, "thesis_summary", None) or ""
        if not analysis_id or not thesis.strip():
            return None
        thesis_tokens = tokenize(thesis)
        if not thesis_tokens:
            return None
        overlap, best = _best_sentence(thesis_tokens, sentences)
        if overlap < self.min_analysis_overlap:
            return None

        if best.contradicts:
            category, confidence = "CONTRADICTS", 0.85
            justification = (
                f"El documento contiene datos verificables que chocan con la tesis "
                f"del análisis {analysis_id} (marcador de negación/refutación en la "
                f"evidencia citada). Se preserva la conclusión del analista y se "
                f"recomienda elevar un CoherenceAlert (SPEC-02 §1 Fase 2)."
            )
        elif best.supports or any(s.supports for s in sentences if thesis_tokens & s.tokens):
            category, confidence = "CORROBORATES", 0.80
            justification = (
                f"La evidencia del documento respalda la tesis del análisis "
                f"{analysis_id} (solapamiento de {overlap} términos relevantes con "
                f"marcadores de confirmación)."
            )
        else:
            category, confidence = "EXTENDS", 0.55
            justification = (
                f"El documento aborda entidades/temáticas de la tesis del análisis "
                f"{analysis_id} (solapamiento de {overlap} términos) y amplía la "
                f"información con nueva evidencia, sin confirmarla ni refutarla."
            )
        return {
            "target_id": analysis_id,
            "link_category": category,
            "confidence": confidence,
            "coherence_justification": justification,
            "exact_quote": best.text,
        }

    # ------------------------------------------------------------------ #
    # IntelRequirement abiertos
    # ------------------------------------------------------------------ #
    def _evaluate_requirement(self, requirement, sentences: Sequence[Sentence]) -> dict | None:
        requirement_id = getattr(requirement, "id", None)
        question = getattr(requirement, "question", None) or ""
        if not requirement_id or not question.strip():
            return None
        question_tokens = tokenize(question)
        if not question_tokens:
            return None
        overlap, best = _best_sentence(question_tokens, sentences)
        if overlap < self.min_requirement_overlap:
            return None
        return {
            "target_id": requirement_id,
            "link_category": "ANSWERS_REQUIREMENT",
            "confidence": 0.70,
            "coherence_justification": (
                f"El documento aporta evidencia que responde a la RFI {requirement_id} "
                f"(solapamiento de {overlap} términos con la pregunta; validación de "
                f"cierre manual por el analista, SPEC-02 §1 Fase 2)."
            ),
            "exact_quote": best.text,
        }


def _best_sentence(
    target_tokens: frozenset[str], sentences: Sequence[Sentence]
) -> tuple[int, Sentence]:
    """Oración con mayor solapamiento de tokens (empate → la primera)."""
    best = sentences[0]
    best_overlap = -1
    for sentence in sentences:
        overlap = len(target_tokens & sentence.tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best = sentence
    return max(best_overlap, 0), best


__all__ = [
    "CONTRADICTION_CUES",
    "SUPPORT_CUES",
    "LinkAnalyzer",
    "Sentence",
    "normalize_token",
    "split_sentences",
    "tokenize",
]
