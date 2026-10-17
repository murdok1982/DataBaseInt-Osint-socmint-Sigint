"""GraphRAG — respuesta en lenguaje natural con evidencia citada.

Autorizado por: **SPEC-03 §5** (GraphRAG) y **SPEC-03 §6** ("GraphRAG nunca
devuelve respuesta sin ``citations``").
Zona: Agente 2 — ``apps/backend/app/services/graphrag_engine.py``.

Regla dura (SPEC-03 §6)
-----------------------
Toda respuesta DEBE citar la evidencia usada. Se impone en dos capas:

1. :class:`RagAnswer` exige ``citations`` no vacío (``min_length=1``).
2. :class:`GraphRagEngine.answer` eleva :class:`NoCitationsError` si la
   recuperación no produce ninguna cita verificable.

El motor usa un ``GraphRepository`` **inyectado por duck-typing** (métodos
``search_nodes`` y ``neighborhood``) y, opcionalmente, un ``LLMClient`` de
síntesis. Se incluyen implementaciones en memoria (``InMemoryGraphRepository``,
``FakeRagEngine``) para testear offline sin Neo4j ni LLM.
"""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Profundidad por defecto de vecindad (SPEC-03 §6 limita ``depth`` a máx 3).
DEFAULT_DEPTH = 1
MAX_DEPTH = 3
DEFAULT_TOP_K = 10
DEFAULT_MIN_CONFIDENCE = 0.5

#: Respuesta explícita cuando no hay evidencia verificable (SPEC-03 §8).
NO_EVIDENCE_ANSWER = (
    "No se recuperó evidencia verificable en el grafo para responder a la "
    "consulta; no hay citas disponibles (SPEC-03 §6/§8)."
)


# =====================================================================
# Errores
# =====================================================================
class GraphRagError(RuntimeError):
    """Error base de GraphRAG."""


class NoCitationsError(GraphRagError):
    """La respuesta no tiene citas — prohibido por SPEC-03 §6."""


class IncompleteAnswerError(GraphRagError):
    """El LLM de síntesis devolvió una respuesta vacía."""


# =====================================================================
# Modelos de respuesta (contrato SPEC-03 §5)
# =====================================================================
class Citation(BaseModel):
    """Cita de evidencia verificable (SPEC-03 §5).

    Cita un **informe OSINT** (``report_id`` + ``page_number`` + ``exact_quote``,
    SPEC-03 §5) o un **análisis humano** (``analysis_id`` + ``code``, SPEC-03 §5).
    ``exact_quote`` es obligatorio (``RagResponse.citations[].required`` del
    contrato) y debe existir al menos un origen (informe o análisis).
    """

    model_config = ConfigDict(extra="forbid")

    report_id: Optional[str] = Field(default=None, min_length=1)
    analysis_id: Optional[str] = Field(default=None, min_length=1)
    code: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    exact_quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def _require_origin(self) -> "Citation":
        if not self.report_id and not self.analysis_id:
            raise ValueError(
                "La cita requiere 'report_id' (informe OSINT) o 'analysis_id' "
                "(análisis humano) — SPEC-03 §5."
            )
        return self

    def as_dict(self) -> dict[str, Any]:
        """Serializa la cita omitiendo los campos no aplicables."""
        return self.model_dump(mode="json", exclude_none=True)


class Subgraph(BaseModel):
    """Subgrafo de apoyo: ``{nodes, edges}`` (SPEC-03 §5)."""

    model_config = ConfigDict(extra="forbid")

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class RagAnswer(BaseModel):
    """Respuesta GraphRAG (SPEC-03 §5). ``citations`` es obligatorio y no vacío."""

    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)
    subgraph: Subgraph
    citations: list[Citation] = Field(min_length=1)

    def as_dict(self) -> dict[str, Any]:
        """Serializa la respuesta al formato JSON del contrato."""
        return self.model_dump(mode="json")


# =====================================================================
# Interfaces inyectables (duck-typing)
# =====================================================================
@runtime_checkable
class GraphRepository(Protocol):
    """Interfaz mínima de grafo que consume el motor (Agente 1 lo implementa)."""

    def search_nodes(self, query: str, limit: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
        ...

    def neighborhood(
        self, node_id: str, depth: int = DEFAULT_DEPTH
    ) -> Mapping[str, list[dict[str, Any]]]:
        ...


@runtime_checkable
class RagLLMClient(Protocol):
    """LLM de síntesis opcional."""

    def generate(self, prompt: str, *, context: str = "") -> str:
        ...


@runtime_checkable
class RagEngine(Protocol):
    """Interfaz del motor GraphRAG (SPEC-03 §5)."""

    def answer(self, query: str) -> RagAnswer:
        ...


# =====================================================================
# Motor
# =====================================================================
class GraphRagEngine:
    """Motor GraphRAG basado en evidencia del grafo (SPEC-03 §5)."""

    def __init__(
        self,
        repository: GraphRepository,
        *,
        llm: Any = None,
        depth: int = DEFAULT_DEPTH,
        top_k: int = DEFAULT_TOP_K,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
    ) -> None:
        if not 1 <= depth <= MAX_DEPTH:
            raise ValueError(f"depth debe estar en 1..{MAX_DEPTH} (SPEC-03 §6).")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence debe estar en [0.0, 1.0].")
        self.repository = repository
        self.llm = llm
        self.depth = depth
        self.top_k = top_k
        self.min_confidence = min_confidence

    def answer(self, query: str) -> RagAnswer:
        """Responde ``query`` con subgrafo y citas verificables (SPEC-03 §5).

        :raises NoCitationsError: si no se recupera evidencia citable (SPEC-03 §6).
        """
        nodes, edges, citations = self._retrieve_with_citations(query)
        if not citations:
            raise NoCitationsError(
                "GraphRAG no puede responder sin citas (SPEC-03 §6): no se "
                "recuperó evidencia con procedencia (report_id, page_number, "
                "exact_quote) ni análisis humanos citables."
            )
        text = self._synthesize(query, nodes, edges, citations)
        return RagAnswer(
            answer=text,
            subgraph=Subgraph(nodes=nodes, edges=edges),
            citations=citations,
        )

    def answer_or_empty(self, query: str) -> dict[str, Any]:
        """Responde **sin** elevar cuando no hay evidencia (SPEC-03 §8).

        Devuelve ``{answer, subgraph, citations}``; si no hay citas verificables,
        retorna :data:`NO_EVIDENCE_ANSWER` con ``citations: []`` (``200`` en la
        API, nunca una respuesta fabricada).
        """
        nodes, edges, citations = self._retrieve_with_citations(query)
        if not citations:
            return {
                "answer": NO_EVIDENCE_ANSWER,
                "subgraph": Subgraph(nodes=nodes, edges=edges).model_dump(mode="json"),
                "citations": [],
            }
        text = self._synthesize(query, nodes, edges, citations)
        return RagAnswer(
            answer=text,
            subgraph=Subgraph(nodes=nodes, edges=edges),
            citations=citations,
        ).as_dict()

    def _retrieve_with_citations(
        self, query: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[Citation]]:
        """Recupera el subgrafo y sus citas verificables."""
        nodes, edges = self._retrieve(query)
        return nodes, edges, self._collect_citations(edges, nodes)

    # -- Recuperación --------------------------------------------------
    def _retrieve(self, query: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        seeds = self.repository.search_nodes(query, limit=self.top_k) or []
        nodes: dict[str, dict[str, Any]] = {}
        edges: dict[tuple[Any, ...], dict[str, Any]] = {}

        for seed in seeds:
            node_id = seed.get("id")
            if node_id is not None:
                nodes.setdefault(node_id, seed)
            if node_id is None:
                continue
            hood = self.repository.neighborhood(node_id, depth=self.depth) or {}
            for node in hood.get("nodes", []) or []:
                if node.get("id") is not None:
                    nodes.setdefault(node["id"], node)
            for edge in hood.get("edges", []) or []:
                if self._confidence_of(edge) < self.min_confidence:
                    continue
                edges.setdefault(self._edge_key(edge), edge)

        return list(nodes.values()), list(edges.values())

    @staticmethod
    def _edge_key(edge: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            edge.get("from_id"),
            edge.get("to_id"),
            edge.get("rel_type") or edge.get("type"),
        )

    @staticmethod
    def _props(record: Mapping[str, Any]) -> Mapping[str, Any]:
        props = record.get("props")
        return props if isinstance(props, Mapping) else record

    def _confidence_of(self, record: Mapping[str, Any]) -> float:
        value = self._props(record).get("confidence")
        try:
            return float(value)
        except (TypeError, ValueError):
            return 1.0  # sin metadato de confianza → no se filtra en recuperación

    # -- Citas (SPEC-03 §5/§6) -----------------------------------------
    def _collect_citations(
        self, edges: list[dict[str, Any]], nodes: list[dict[str, Any]]
    ) -> list[Citation]:
        citations: dict[tuple[Any, ...], Citation] = {}
        for record in (*edges, *nodes):
            citation = self._citation_from(record)
            if citation is None:
                continue
            citations.setdefault(self._citation_key(citation), citation)
        return list(citations.values())

    @staticmethod
    def _citation_key(citation: Citation) -> tuple[Any, ...]:
        return (
            citation.report_id or citation.analysis_id,
            citation.page_number,
            citation.exact_quote,
        )

    def _citation_from(self, record: Mapping[str, Any]) -> Citation | None:
        """Construye una cita desde el registro (informe OSINT o análisis humano).

        Prioriza la procedencia de informe (``report_id`` + ``page_number``) y, si
        no la hay, intenta citar un análisis humano (``analysis_id`` + ``code``),
        SPEC-03 §5. Sin ``exact_quote`` no hay cita (nunca se inventa evidencia).
        """
        props = self._props(record)
        report_id = props.get("source_report_id") or props.get("report_id")
        analysis_id = (
            props.get("analysis_id")
            or props.get("referenced_analysis_id")
            or props.get("source_analysis_id")
        )
        code = props.get("code") or props.get("analysis_code")
        quote = (
            props.get("evidence_quote")
            or props.get("exact_quote")
            or props.get("text_evidence_span")
            or props.get("rationale")
        )
        page_number = props.get("page_number") or props.get("page")
        if not quote:
            return None
        try:
            if report_id and page_number is not None:
                return Citation(
                    report_id=str(report_id),
                    page_number=int(page_number),
                    exact_quote=str(quote),
                )
            if analysis_id:
                return Citation(
                    analysis_id=str(analysis_id),
                    code=str(code) if code else None,
                    page_number=int(page_number) if page_number is not None else None,
                    exact_quote=str(quote),
                )
        except Exception:  # pydantic ValidationError u otros → se descarta
            return None
        return None

    # -- Síntesis ------------------------------------------------------
    def _synthesize(
        self,
        query: str,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        citations: list[Citation],
    ) -> str:
        context = self._context(nodes, edges, citations)
        if self.llm is not None:
            text = self.llm.generate(query, context=context)
            if not text or not str(text).strip():
                raise IncompleteAnswerError(
                    "El LLM de síntesis devolvió una respuesta vacía."
                )
            return str(text).strip()
        return (
            f"Se recuperaron {len(nodes)} entidades y {len(edges)} relaciones "
            f"relevantes para «{query}», con {len(citations)} fragmento(s) de "
            f"evidencia citados."
        )

    @staticmethod
    def _context(
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        citations: list[Citation],
    ) -> str:
        return json.dumps(
            {
                "nodes": nodes,
                "edges": edges,
                "citations": [citation.model_dump(mode="json") for citation in citations],
            },
            ensure_ascii=False,
            default=str,
        )


# =====================================================================
# Implementaciones en memoria (tests / degradación offline)
# =====================================================================
class InMemoryGraphRepository:
    """Repositorio de grafo en memoria compatible con la interfaz del motor."""

    def __init__(
        self,
        nodes: list[dict[str, Any]] | None = None,
        edges: list[dict[str, Any]] | None = None,
    ) -> None:
        self.nodes: dict[Any, dict[str, Any]] = {
            node["id"]: dict(node) for node in (nodes or []) if node.get("id") is not None
        }
        self.edges: list[dict[str, Any]] = [dict(edge) for edge in (edges or [])]

    def add_node(self, node: Mapping[str, Any]) -> dict[str, Any]:
        self.nodes[node["id"]] = dict(node)
        return self.nodes[node["id"]]

    def add_edge(
        self,
        from_id: Any,
        to_id: Any,
        rel_type: str,
        props: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        edge = {
            "from_id": from_id,
            "to_id": to_id,
            "rel_type": rel_type,
            "props": dict(props or {}),
        }
        self.edges.append(edge)
        return edge

    def search_nodes(self, query: str, limit: int = DEFAULT_TOP_K) -> list[dict[str, Any]]:
        needle = (query or "").lower()
        matches = [
            node
            for node in self.nodes.values()
            if needle in json.dumps(node, ensure_ascii=False, default=str).lower()
        ]
        return matches[:limit]

    def neighborhood(
        self, node_id: Any, depth: int = DEFAULT_DEPTH
    ) -> dict[str, list[dict[str, Any]]]:
        reached = {node_id}
        frontier = {node_id}
        collected: list[dict[str, Any]] = []
        seen: set[tuple[Any, ...]] = set()
        for _ in range(max(1, depth)):
            next_frontier: set[Any] = set()
            for edge in self.edges:
                key = (
                    edge.get("from_id"),
                    edge.get("to_id"),
                    edge.get("rel_type"),
                )
                if (
                    edge.get("from_id") in frontier or edge.get("to_id") in frontier
                ) and key not in seen:
                    seen.add(key)
                    collected.append(edge)
                    for endpoint in (edge.get("from_id"), edge.get("to_id")):
                        if endpoint is not None and endpoint not in reached:
                            next_frontier.add(endpoint)
                            reached.add(endpoint)
            frontier = next_frontier
            if not frontier:
                break
        return {
            "nodes": [self.nodes[i] for i in reached if i in self.nodes],
            "edges": collected,
        }


class FakeRagEngine:
    """Motor GraphRAG determinista para tests (offline, sin grafo ni LLM)."""

    def __init__(
        self,
        citations: list[Citation | Mapping[str, Any]] | None = None,
        *,
        answer: str = "Respuesta de prueba con evidencia.",
        subgraph: Subgraph | Mapping[str, Any] | None = None,
    ) -> None:
        self._citations = [
            citation if isinstance(citation, Citation) else Citation(**dict(citation))
            for citation in (citations or [])
        ]
        self._answer = answer
        self._subgraph = subgraph

    def answer(self, query: str) -> RagAnswer:
        if not self._citations:
            raise NoCitationsError(
                "FakeRagEngine no puede responder sin citas (SPEC-03 §6)."
            )
        subgraph = self._subgraph
        if subgraph is None:
            subgraph = Subgraph(nodes=[], edges=[])
        elif isinstance(subgraph, Mapping):
            subgraph = Subgraph(**subgraph)
        return RagAnswer(
            answer=f"{self._answer} (query: {query})",
            subgraph=subgraph,
            citations=self._citations,
        )

    def answer_or_empty(self, query: str) -> dict[str, Any]:
        """Variante que no eleva sin evidencia (SPEC-03 §8)."""
        if not self._citations:
            subgraph = self._subgraph or Subgraph(nodes=[], edges=[])
            if isinstance(subgraph, Mapping):
                subgraph = Subgraph(**subgraph)
            return {
                "answer": NO_EVIDENCE_ANSWER,
                "subgraph": subgraph.model_dump(mode="json"),
                "citations": [],
            }
        return self.answer(query).as_dict()


__all__ = [
    "Citation",
    "DEFAULT_DEPTH",
    "DEFAULT_MIN_CONFIDENCE",
    "DEFAULT_TOP_K",
    "FakeRagEngine",
    "GraphRagEngine",
    "GraphRagError",
    "GraphRepository",
    "InMemoryGraphRepository",
    "IncompleteAnswerError",
    "MAX_DEPTH",
    "NO_EVIDENCE_ANSWER",
    "NoCitationsError",
    "RagAnswer",
    "RagEngine",
    "RagLLMClient",
    "Subgraph",
]
