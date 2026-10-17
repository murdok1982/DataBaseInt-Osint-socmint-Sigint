"""Fixtures compartidas de los tests del Informante IA (sin red)."""

from __future__ import annotations

import pytest

from informant_agent.collectors.base import Page, RawDocument
from informant_agent.models import PreflightResponse

ANALYSIS_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
REQUIREMENT_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
CANONICAL_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"

THESIS = "State Bank financed Acme Corp."
REQUIREMENT_QUESTION = "Did State Bank finance Acme Corp in 2021?"


@pytest.fixture
def context() -> PreflightResponse:
    """Contexto de Pre-Flight típico (SPEC-02 §1 Fase 0)."""
    return PreflightResponse(
        preflight_token="tok-123",
        analyses=[
            {"id": ANALYSIS_ID, "code": "AN-1", "thesis_summary": THESIS},
        ],
        requirements=[
            {"id": REQUIREMENT_ID, "question": REQUIREMENT_QUESTION, "priority": "HIGH"},
        ],
        canonical_entities=[
            {"id": CANONICAL_ID, "label": "Organization", "canonical_name": "Acme Corp"},
        ],
    )


@pytest.fixture
def make_document():
    """Fábrica de :class:`RawDocument` para pruebas."""

    def _factory(
        text: str,
        *,
        title: str = "Open-source report",
        source_type: str = "NEWS",
        reliability_rating: str = "B",
        pages: tuple[Page, ...] | list[Page] | None = None,
        assertions=(),
        keywords=(),
        entity_hints=(),
    ) -> RawDocument:
        page_list = tuple(pages) if pages is not None else (Page(page_number=1, text=text),)
        return RawDocument(
            title=title,
            source_type=source_type,
            source_url_or_origin="https://example.test/source",
            reliability_rating=reliability_rating,
            pages=page_list,
            raw_bytes=text.encode("utf-8"),
            assertions=tuple(assertions),
            keywords=tuple(keywords),
            entity_hints=tuple(entity_hints),
        )

    return _factory
