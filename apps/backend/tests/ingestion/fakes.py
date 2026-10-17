"""Fakes de la suite de ingesta (Agente 2) — offline, sin Neo4j.

Autorizado por: SPEC-01 §2/§3.2/§4 · SPEC-02 §1 (Fase 0/2) · SPEC-03 §5.

:class:`FakeGraphRepository` extiende el ``FakeRepository`` compartido
(``tests/conftest.py``, no editable) con los **escritores de coherencia** y las
**consultas del pre-vuelo** que el ``CoherenceEngine`` y el ``PreflightService``
consumen por *duck-typing* (Agente 1 los implementa en
``app/db/repository.py``).

Además actúa de **espía** de ``update_analysis`` para probar la inmutabilidad
analítica (Constitución Regla 7/9: el motor jamás toca ``conclusion_body``).
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from tests.conftest import FakeRepository

#: Labels OSINT extraíbles (SPEC-01 §1) relevantes para el diccionario canónico.
OSINT_LABELS: tuple[str, ...] = (
    "Person",
    "Organization",
    "CyberAsset",
    "Location",
    "Event",
)

#: Severidades de alerta admitidas (SPEC-01 §2).
SEVERITIES: tuple[str, ...] = ("LOW", "MEDIUM", "CRITICAL")


class FakeGraphRepository(FakeRepository):
    """``FakeRepository`` + coherencia (Fase 2) + pre-vuelo (Fase 0)."""

    def __init__(self) -> None:
        super().__init__()
        self.analyses: dict[str, dict[str, Any]] = {}
        self.requirements: dict[str, dict[str, Any]] = {}
        self.corroborations: list[dict[str, Any]] = []
        self.contradictions: list[dict[str, Any]] = []
        self.extensions: list[dict[str, Any]] = []
        self.citations: list[dict[str, Any]] = []
        self.answers: list[dict[str, Any]] = []
        self.investigations: list[dict[str, Any]] = []
        #: Espía: el motor de coherencia NUNCA debe invocarlo (Regla 7/9).
        self.update_analysis_calls: list[tuple[str, dict[str, Any]]] = []

    # -----------------------------------------------------------------
    # Sembrado (tests)
    # -----------------------------------------------------------------
    def seed_analysis(
        self,
        *,
        analysis_id: Optional[str] = None,
        code: Optional[str] = None,
        title: str = "Expediente",
        thesis_summary: str = "",
        conclusion_body: Optional[str] = None,
        status: str = "ACTIVE",
    ) -> dict[str, Any]:
        node_id = analysis_id or str(uuid.uuid4())
        analysis = {
            "id": node_id,
            "code": code or f"AN-{node_id[:8].upper()}",
            "title": title,
            "thesis_summary": thesis_summary,
            "conclusion_body": conclusion_body,
            "status": status,
            "coherence_score": 0.0,
        }
        self.analyses[node_id] = analysis
        return dict(analysis)

    def seed_requirement(
        self,
        *,
        requirement_id: Optional[str] = None,
        question: str = "¿Quién financia Acme Corp?",
        priority: str = "HIGH",
        status: str = "OPEN",
    ) -> dict[str, Any]:
        node_id = requirement_id or str(uuid.uuid4())
        requirement = {
            "id": node_id,
            "question": question,
            "priority": priority,
            "status": status,
        }
        self.requirements[node_id] = requirement
        return dict(requirement)

    # -----------------------------------------------------------------
    # Pre-Flight (Fase 0 · SPEC-02 §1)
    # -----------------------------------------------------------------
    def active_analyses(self, keywords: list[str]) -> list[dict[str, Any]]:
        clean = [str(k).casefold() for k in (keywords or []) if str(k).strip()]
        if not clean:
            return []
        return [
            {
                "id": analysis["id"],
                "code": analysis.get("code"),
                "thesis_summary": analysis.get("thesis_summary"),
            }
            for analysis in self.analyses.values()
            if analysis.get("status") == "ACTIVE"
            and any(
                keyword in (analysis.get("thesis_summary") or "").casefold()
                for keyword in clean
            )
        ]

    def list_open_requirements(self) -> list[dict[str, Any]]:
        return [
            {
                "id": requirement["id"],
                "question": requirement.get("question"),
                "priority": requirement.get("priority"),
            }
            for requirement in self.requirements.values()
            if requirement.get("status") == "OPEN"
        ]

    def canonical_entities(self) -> list[dict[str, Any]]:
        entities: list[dict[str, Any]] = []
        for node in self.nodes.values():
            if node["label"] not in OSINT_LABELS:
                continue
            props = node["props"]
            name = (
                props.get("canonical_name")
                or props.get("value")
                or props.get("name")
            )
            entities.append(
                {
                    "id": node["id"],
                    "label": node["label"],
                    "canonical_name": name,
                }
            )
        return sorted(entities, key=lambda e: str(e.get("canonical_name") or ""))

    # -----------------------------------------------------------------
    # Escritores de coherencia (Fase 2 · SPEC-01 §3.2)
    # -----------------------------------------------------------------
    def corroborate(
        self,
        report_id: str,
        analysis_id: str,
        *,
        confidence: float,
        justification: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        conf = float(confidence)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence debe estar en [0.0, 1.0].")
        record = {
            "type": "CORROBORATES",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "confidence": conf,
            "justification": justification,
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.corroborations.append(record)
        analysis = self.analyses.get(analysis_id)
        if analysis is not None:
            analysis["coherence_score"] = min(
                1.0, float(analysis.get("coherence_score") or 0.0) + conf
            )
        return record

    def contradict(
        self,
        report_id: str,
        analysis_id: str,
        *,
        conflict_detail: str,
        severity: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
        entity_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if severity not in SEVERITIES:
            raise ValueError(f"severity '{severity}' no válida: {list(SEVERITIES)}.")
        # Regla 7/9: NUNCA se toca ``conclusion_body`` (no se llama update_analysis).
        alert = self.create_alert(
            "CONTRADICTION",
            conflict_detail,
            analysis_id=analysis_id,
            entity_id=entity_id,
        )
        self.contradictions.append(
            {
                "type": "CONTRADICTS",
                "report_id": report_id,
                "source_report_id": report_id,
                "analysis_id": analysis_id,
                "conflict_detail": conflict_detail,
                "severity": severity,
                "exact_quote": exact_quote,
                "text_evidence_span": exact_quote,
                "page_number": page_number,
                "entity_id": entity_id,
                "alert_id": alert["id"],
            }
        )
        return alert

    def extend_analysis(
        self,
        report_id: str,
        analysis_id: str,
        *,
        new_angle_summary: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        record = {
            "type": "EXTENDS",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "new_angle_summary": new_angle_summary,
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.extensions.append(record)
        return record

    def answer_requirement(
        self,
        report_id: str,
        requirement_id: str,
        *,
        answer_summary: str,
        confidence: float,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        conf = float(confidence)
        if not 0.0 <= conf <= 1.0:
            raise ValueError("confidence debe estar en [0.0, 1.0].")
        requirement = self.requirements.get(requirement_id)
        if requirement is not None:
            requirement["status"] = "ANSWERED"
        record = {
            "type": "ANSWERS_REQUIREMENT",
            "report_id": report_id,
            "source_report_id": report_id,
            "requirement_id": requirement_id,
            "answer_summary": answer_summary,
            "confidence": conf,
            "status": "ANSWERED",
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.answers.append(record)
        return record

    def cite_analysis(
        self,
        report_id: str,
        analysis_id: str,
        *,
        relevance_score: float,
        rationale: str,
        exact_quote: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> dict[str, Any]:
        record = {
            "type": "CITES_ANALYSIS",
            "report_id": report_id,
            "source_report_id": report_id,
            "analysis_id": analysis_id,
            "relevance_score": float(relevance_score),
            "rationale": rationale,
            "exact_quote": exact_quote,
            "text_evidence_span": exact_quote,
            "page_number": page_number,
        }
        self.citations.append(record)
        return record

    def link_analysis_to_entity(
        self, analysis_id: str, entity_id: str
    ) -> dict[str, Any]:
        record = {
            "type": "INVESTIGATES",
            "analysis_id": analysis_id,
            "entity_id": entity_id,
        }
        self.investigations.append(record)
        return record

    # -----------------------------------------------------------------
    # Autoridad humana (espía: el motor no debe invocarla)
    # -----------------------------------------------------------------
    def update_analysis(
        self, analysis_id: str, patch: dict[str, Any]
    ) -> Optional[dict[str, Any]]:
        self.update_analysis_calls.append((analysis_id, dict(patch)))
        analysis = self.analyses.get(analysis_id)
        if analysis is None:
            return None
        for key in ("title", "thesis_summary", "conclusion_body", "status"):
            if key in patch:
                analysis[key] = patch[key]
        return dict(analysis)


__all__ = [
    "FakeGraphRepository",
    "OSINT_LABELS",
    "SEVERITIES",
]
