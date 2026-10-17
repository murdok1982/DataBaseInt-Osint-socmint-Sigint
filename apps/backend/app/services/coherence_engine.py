"""Motor de Coherencia Humano–IA — Fase 2 (SPEC-02 §1 · SPEC-01 §3.2).

Autorizado por: **SPEC-02 §1 Fase 2** (evaluación del motor de coherencia),
**SPEC-02 §5** (``app/services/coherence_engine.py``, dueño Agente 2),
**SPEC-01 §3.2** (relaciones de coherencia) y la **Constitución, Reglas 7–9**.

Zona: Agente 2 — ``apps/backend/app/services/coherence_engine.py``.

Aplica los ``analytical_context_links`` (contrato v2.0) **delegando** en los
escritores de coherencia del repositorio del Agente 1:

- ``CORROBORATES`` → ``corroborate`` (sube ``coherence_score`` + enlaza evidencia).
- ``CONTRADICTS`` → ``contradict`` (arista + ``CoherenceAlert``). **Nunca** toca
  ``conclusion_body``: el motor no llama jamás a ``update_analysis`` (Regla 7/9).
- ``EXTENDS`` → ``extend_analysis`` + ``cite_analysis`` (vínculo ``CITES_ANALYSIS``).
- ``ANSWERS_REQUIREMENT`` → ``answer_requirement`` (cierra la brecha; el cierre
  definitivo lo valida el Analista, SPEC-02 §1 Fase 2).
- ``CITES_ANALYSIS`` → ``cite_analysis`` (categoría defensiva: el contrato v2 solo
  emite las 4 anteriores; ``EXTENDS`` ya produce la cita).

La inmutabilidad analítica la garantiza el repositorio (Agente 1): ningún método
de coherencia modifica un nodo/arista con ``locked_by_analyst: true``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.schemas.extraction import AnalyticalContextLink, LinkCategory

# =====================================================================
# Severidad derivada de la confianza del vínculo (SPEC-01 §2)
# =====================================================================
SEVERITY_CRITICAL_THRESHOLD = 0.85
SEVERITY_MEDIUM_THRESHOLD = 0.60


def derive_severity(confidence: float) -> str:
    """Traduce la confianza del vínculo a una severidad de alerta.

    El contrato ``analytical_context_link`` no transporta severidad, así que el
    motor la **deriva** (dominio cerrado ``LOW|MEDIUM|CRITICAL`` de SPEC-01 §2).
    """
    value = float(confidence)
    if value >= SEVERITY_CRITICAL_THRESHOLD:
        return "CRITICAL"
    if value >= SEVERITY_MEDIUM_THRESHOLD:
        return "MEDIUM"
    return "LOW"


@dataclass
class CoherenceOutcome:
    """Resultado de aplicar los vínculos analíticos de un informe (Fase 2)."""

    report_id: str
    corroborations: list[dict[str, Any]] = field(default_factory=list)
    contradictions: list[dict[str, Any]] = field(default_factory=list)
    extensions: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def applied(self) -> int:
        """Número de vínculos aplicados."""
        return (
            len(self.corroborations)
            + len(self.contradictions)
            + len(self.extensions)
            + len(self.citations)
            + len(self.answers)
        )


class CoherenceEngine:
    """Aplica ``analytical_context_links`` vía los escritores del repositorio."""

    def __init__(self, repository: Any) -> None:
        self.repository = repository

    # -----------------------------------------------------------------
    # API principal
    # -----------------------------------------------------------------
    def apply(
        self,
        report_id: str,
        links: Iterable[AnalyticalContextLink | Mapping[str, Any]],
        *,
        triplets: Optional[Iterable[Any]] = None,
    ) -> CoherenceOutcome:
        """Aplica todos los vínculos del informe (Fase 2, SPEC-02 §1).

        ``triplets`` (opcional) aporta las tripletas extraídas del informe; se
        usan para derivar el ``page_number`` de cada arista analítica a partir de
        la tripleta cuya ``provenance.exact_quote`` coincide con la cita del
        vínculo (SPEC-01 §3.2). Sin coincidencia, ``page_number`` queda ``None``.
        """
        outcome = CoherenceOutcome(report_id=str(report_id))
        page_lookup = self._page_lookup(triplets)
        for link in links or []:
            self._apply_one(str(report_id), link, outcome, page_lookup=page_lookup)
        return outcome

    #: Alias declarativo del flujo.
    process = apply

    def apply_link(
        self,
        report_id: str,
        link: AnalyticalContextLink | Mapping[str, Any],
        *,
        triplets: Optional[Iterable[Any]] = None,
    ) -> dict[str, Any]:
        """Aplica un único vínculo y devuelve su registro (uso directo/tests)."""
        outcome = CoherenceOutcome(report_id=str(report_id))
        return self._apply_one(
            str(report_id), link, outcome, page_lookup=self._page_lookup(triplets)
        )

    # -----------------------------------------------------------------
    # Internos
    # -----------------------------------------------------------------
    def _normalize(
        self, link: AnalyticalContextLink | Mapping[str, Any]
    ) -> dict[str, Any]:
        if isinstance(link, AnalyticalContextLink):
            return link.model_dump(mode="json")
        return dict(link)

    @staticmethod
    def _triplet_provenance(triplet: Any) -> tuple[Optional[str], Optional[int]]:
        """Extrae ``(exact_quote, page_number)`` de la procedencia de una tripleta."""
        provenance = (
            triplet.get("provenance")
            if isinstance(triplet, Mapping)
            else getattr(triplet, "provenance", None)
        )
        if provenance is None:
            return None, None
        if isinstance(provenance, Mapping):
            quote = provenance.get("exact_quote")
            page = provenance.get("page_number")
        else:
            quote = getattr(provenance, "exact_quote", None)
            page = getattr(provenance, "page_number", None)
        if not quote:
            return None, None
        return str(quote), (int(page) if page is not None else None)

    @classmethod
    def _page_lookup(
        cls, triplets: Optional[Iterable[Any]]
    ) -> dict[str, Optional[int]]:
        """Índice ``exact_quote -> page_number`` de las tripletas (SPEC-01 §3.2)."""
        lookup: dict[str, Optional[int]] = {}
        for triplet in triplets or []:
            quote, page = cls._triplet_provenance(triplet)
            if quote and quote not in lookup:
                lookup[quote] = page
        return lookup

    def _apply_one(
        self, report_id: str, link: AnalyticalContextLink | Mapping[str, Any],
        outcome: CoherenceOutcome,
        *,
        page_lookup: Optional[Mapping[str, Optional[int]]] = None,
    ) -> dict[str, Any]:
        data = self._normalize(link)
        raw_category = data.get("link_category")
        category = (
            raw_category.value
            if isinstance(raw_category, LinkCategory)
            else str(raw_category)
        )
        target_id = str(data.get("target_id") or "")
        confidence = float(data.get("confidence", 0.0))
        justification = str(
            data.get("coherence_justification")
            or data.get("justification")
            or ""
        )
        quote = str(data.get("exact_quote") or "")
        # SPEC-01 §3.2: ``page_number`` se deriva de la tripleta cuya cita coincide.
        page_number: Optional[int] = None
        if page_lookup and quote:
            page_number = page_lookup.get(quote)

        if category == LinkCategory.CORROBORATES.value:
            result = self.repository.corroborate(
                report_id,
                target_id,
                confidence=confidence,
                justification=justification,
                exact_quote=quote,
                page_number=page_number,
            )
            outcome.corroborations.append(result)
        elif category == LinkCategory.CONTRADICTS.value:
            # Regla 7/9: NUNCA se toca ``conclusion_body``. El repositorio crea
            # la arista [:CONTRADICTS] y el CoherenceAlert, y no modifica la tesis.
            result = self.repository.contradict(
                report_id,
                target_id,
                conflict_detail=justification,
                severity=derive_severity(confidence),
                exact_quote=quote,
                page_number=page_number,
            )
            outcome.contradictions.append(result)
            if isinstance(result, Mapping) and "id" in result:
                outcome.alerts.append(dict(result))
        elif category == LinkCategory.EXTENDS.value:
            result = self.repository.extend_analysis(
                report_id,
                target_id,
                new_angle_summary=justification,
                exact_quote=quote,
                page_number=page_number,
            )
            outcome.extensions.append(result)
            # EXTENDS conlleva el enlace de cita (SPEC-01 §3.2, contrato §6).
            outcome.citations.append(
                self.repository.cite_analysis(
                    report_id,
                    target_id,
                    relevance_score=confidence,
                    rationale=justification,
                    exact_quote=quote,
                    page_number=page_number,
                )
            )
        elif category == LinkCategory.ANSWERS_REQUIREMENT.value:
            result = self.repository.answer_requirement(
                report_id,
                target_id,
                answer_summary=justification,
                confidence=confidence,
                exact_quote=quote,
                page_number=page_number,
            )
            outcome.answers.append(result)
        elif category == "CITES_ANALYSIS":
            # Categoría defensiva (no emitida por el contrato v2: se modela vía
            # EXTENDS). Permite enlazar explícitamente una cita de análisis.
            result = self.repository.cite_analysis(
                report_id,
                target_id,
                relevance_score=confidence,
                rationale=justification,
                exact_quote=quote,
                page_number=page_number,
            )
            outcome.citations.append(result)
        else:
            raise ValueError(
                f"link_category '{category}' no reconocida. El contrato v2.0 "
                f"admite {[c.value for c in LinkCategory]} (SPEC-02 §2)."
            )

        record = {"link_category": category, "target_id": target_id, "result": result}
        return record


__all__ = [
    "CoherenceEngine",
    "CoherenceOutcome",
    "SEVERITY_CRITICAL_THRESHOLD",
    "SEVERITY_MEDIUM_THRESHOLD",
    "derive_severity",
]
