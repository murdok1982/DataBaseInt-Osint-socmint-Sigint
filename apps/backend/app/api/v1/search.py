"""Rutas de ``/search`` (tag ``search`` del contrato).

Autorizado por: **SPEC-03 §5** (GraphRAG) y **SPEC-04 §1** (matriz RBAC).

| operationId  | Método | Ruta              | Rol mínimo |
| :----------- | :----- | :---------------- | :--------- |
| ``searchRag``| POST   | ``/search/rag``   | VIEWER     |

El motor (``app.services.graphrag_engine``, **Agente 2**) se importa de forma
perezosa: si no existe aún, la ruta responde **503** ``SERVICE_UNAVAILABLE``.

Guardia normativa de SPEC-03 §6 (v1.1.0 / B7): *GraphRAG nunca inventa una
respuesta sin ``citations``*. Si el motor no recupera evidencia verificable
(``NoCitationsError``) o devuelve ``citations: []``, esta capa responde **200**
con un ``answer`` explícito de *sin evidencia* y ``citations: []`` (en vez de
propagar una respuesta fabricada). **503** queda reservado para una dependencia
realmente ausente (grafo/motor no compuesto).

**SPEC-GAP-3.3.** Firma requerida al Agente 2:
``answer(query, *, user_id) -> {answer, subgraph, citations}``
(alias aceptados: ``query``, ``rag_query``, ``ask``, ``search_rag``).
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import get_graphrag_engine, service_unavailable
from app.core.rbac import Role, require_role
from app.core.users import Principal

router = APIRouter(prefix="/search", tags=["search"])

_ENGINE_CAPABILITIES = ("answer", "rag_query", "query", "ask", "search_rag")

#: Respuesta explícita cuando no hay evidencia verificable (SPEC-03 §6 / B7).
NO_EVIDENCE_ANSWER = (
    "No se recuperó evidencia verificable en el grafo para responder a la "
    "consulta; no hay citas disponibles (SPEC-03 §6)."
)


class RagRequest(BaseModel):
    """Cuerpo de ``searchRag`` (contrato: ``query`` 1..2000)."""

    query: str = Field(min_length=1, max_length=2000)


class Citation(BaseModel):
    """``citations[]`` del contrato — evidencia textual obligatoria.

    Cita un **informe OSINT** (``report_id`` + ``page_number`` + ``exact_quote``)
    o un **análisis humano** (``analysis_id`` + ``code``, SPEC-03 §5). El
    contrato (v2.0.0) solo exige ``exact_quote``; ``report_id`` y ``page_number``
    son opcionales para no rechazar las citas de análisis del motor GraphRAG.

    ``extra="allow"`` (SPEC-GAP-3.3): se preservan los campos adicionales que el
    motor pueda adjuntar a una cita, sin descartarla por ello.
    """

    model_config = ConfigDict(extra="allow")

    report_id: Optional[str] = None
    analysis_id: Optional[str] = None
    code: Optional[str] = None
    page_number: Optional[int] = Field(default=None, ge=1)
    exact_quote: str


class RagNode(BaseModel):
    """Nodo del subgrafo devuelto por GraphRAG."""

    id: str
    label: str
    canonical_name: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    risk_score: Optional[float] = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class RagEdge(BaseModel):
    """Arista del subgrafo devuelto por GraphRAG (con evidencia, Regla 3)."""

    id: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    type: Optional[str] = None
    confidence: Optional[float] = None
    source_report_id: Optional[str] = None
    evidence_quote: Optional[str] = None
    text_evidence_span: Optional[str] = None
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None

    model_config = {"populate_by_name": True}


class RagSubgraph(BaseModel):
    nodes: list[RagNode] = Field(default_factory=list)
    edges: list[RagEdge] = Field(default_factory=list)


class RagResponse(BaseModel):
    """``RagResponse`` del contrato (``answer`` + ``citations`` obligatorios)."""

    answer: str
    subgraph: RagSubgraph = Field(default_factory=RagSubgraph)
    citations: list[Citation] = Field(default_factory=list)


def _resolve_engine_callable(engine: Any) -> Callable[..., Any]:
    for name in _ENGINE_CAPABILITIES:
        candidate = getattr(engine, name, None)
        if callable(candidate):
            return candidate
    raise service_unavailable(
        f"Motor GraphRAG incompleto: falta uno de {list(_ENGINE_CAPABILITIES)} "
        "(SPEC-GAP-3.3)."
    )


def _engine_error_types() -> tuple[type[BaseException], ...]:
    """Errores del motor GraphRAG del Agente 2 (``GraphRagError`` y derivados).

    Un motor que no puede responder por una causa distinta a la ausencia de
    citas (p. ej. síntesis incompleta) se traduce a **503**: nunca a un 200 con
    un dato fabricado ni a un 500.
    """
    try:
        module = __import__(
            "app.services.graphrag_engine",
            fromlist=["GraphRagError"],
        )
    except Exception:  # pragma: no cover - módulo ausente
        return ()
    found = [
        cls
        for name in ("GraphRagError", "IncompleteAnswerError")
        if (cls := getattr(module, name, None)) is not None
    ]
    return tuple(found)


def _no_citations_types() -> tuple[type[BaseException], ...]:
    """``NoCitationsError`` del motor, si existe (sin citas ⇒ 200 sin evidencia)."""
    try:
        module = __import__(
            "app.services.graphrag_engine",
            fromlist=["NoCitationsError"],
        )
    except Exception:  # pragma: no cover - módulo ausente
        return ()
    cls = getattr(module, "NoCitationsError", None)
    return (cls,) if cls is not None else ()


def _filtered_kwargs(func: Callable[..., Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):  # pragma: no cover - builtins
        return kwargs
    if any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in signature.parameters.values()
    ):
        return kwargs
    allowed = {
        name
        for name, param in signature.parameters.items()
        if param.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in kwargs.items() if key in allowed}


# =====================================================================
# POST /search/rag — searchRag (VIEWER+)
# =====================================================================
@router.post(
    "/rag",
    operation_id="searchRag",
    response_model=RagResponse,
    summary="Consulta GraphRAG en lenguaje natural (VIEWER+)",
    responses={403: {"description": "Rol insuficiente (RBAC SPEC-04)"}},
)
async def search_rag(
    payload: RagRequest,
    user: Principal = Depends(require_role(Role.VIEWER)),
    engine: Any = Depends(get_graphrag_engine),
) -> RagResponse:
    """Responde en lenguaje natural **siempre con citas** verificables."""
    callable_ = _resolve_engine_callable(engine)
    kwargs = _filtered_kwargs(
        callable_,
        {
            "query": payload.query,
            "question": payload.query,
            "user_id": user.id,
            "role": user.role.value,
        },
    )
    try:
        result = _call_engine(callable_, payload.query, kwargs)
        if inspect.isawaitable(result):  # pragma: no cover - motor asíncrono
            result = await result
    except _no_citations_types():
        # SPEC-03 §6 / B7: sin evidencia no se fabrica una respuesta; se
        # devuelve 200 con un ``answer`` explícito y ``citations: []``.
        return _no_evidence_response()
    except HTTPException:
        raise
    except (TypeError, *_engine_error_types()) as exc:
        raise service_unavailable(
            f"GraphRAG no pudo responder con evidencia: {exc}"
        ) from exc
    return _as_rag_response(result)


def _no_evidence_response() -> RagResponse:
    """Respuesta 200 explícita cuando no hay citas verificables (B7)."""
    return RagResponse(
        answer=NO_EVIDENCE_ANSWER,
        subgraph=RagSubgraph(),
        citations=[],
    )


def _call_engine(
    callable_: Callable[..., Any], query: str, kwargs: dict[str, Any]
) -> Any:
    """Invoca el motor con la firma que admita (``query`` posicional si aplica)."""
    try:
        return callable_(**kwargs)
    except TypeError:
        return callable_(query)


def _as_rag_response(result: Any) -> RagResponse:
    """Normaliza y **valida** la respuesta del motor (SPEC-03 §5/§6)."""
    if isinstance(result, RagResponse):
        response = result
    elif isinstance(result, dict):
        response = RagResponse(**result)
    elif callable(getattr(result, "as_dict", None)):
        response = RagResponse(**result.as_dict())
    elif callable(getattr(result, "model_dump", None)):
        response = RagResponse(**result.model_dump(mode="json"))
    else:
        raise service_unavailable("Respuesta GraphRAG no reconocible (SPEC-GAP-3.3).")
    if not response.citations:
        # SPEC-03 §6 / B7: se respeta la respuesta del motor pero sin citas se
        # marca explícitamente la ausencia de evidencia (200, nunca 503).
        if not response.answer.strip():
            response.answer = NO_EVIDENCE_ANSWER
        return response
    return response


__all__ = [
    "router",
    "NO_EVIDENCE_ANSWER",
    "RagRequest",
    "RagResponse",
    "RagSubgraph",
    "RagNode",
    "RagEdge",
    "Citation",
]
