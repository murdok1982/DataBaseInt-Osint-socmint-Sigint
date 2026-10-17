"""Recolectores: interfaz ``Collector`` + implementaciones de ejemplo."""

from __future__ import annotations

from pathlib import Path

from informant_agent.collectors import (
    Collector,
    LocalJsonCollector,
    TextDirCollector,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_local_json_collector_reads_documents():
    collector = LocalJsonCollector(FIXTURES)
    documents = sorted(collector.collect(), key=lambda d: d.title)

    assert len(documents) == 2
    titles = {doc.title for doc in documents}
    assert "State Bank financing of Acme Corp" in titles

    acme = next(d for d in documents if d.title.startswith("State Bank financing"))
    assert acme.source_type == "NEWS"
    assert acme.reliability_rating == "B"
    assert acme.pages[0].page_number == 1
    assert "financed Acme Corp" in acme.pages[0].text
    assert acme.assertions[0].predicate == "FINANCED"
    assert acme.local_path is not None
    assert acme.content_bytes()  # hay bytes físicos para el multipart


def test_text_dir_collector_reads_single_page(tmp_path: Path):
    (tmp_path / "note.txt").write_text("Cyber Asset 10.0.0.1 located in Madrid.", encoding="utf-8")
    collector = TextDirCollector(tmp_path)
    documents = list(collector.collect())

    assert len(documents) == 1
    doc = documents[0]
    assert doc.title == "note"
    assert doc.source_type == "NEWS"
    assert len(doc.pages) == 1
    assert doc.pages[0].text.startswith("Cyber Asset")


def test_implementations_satisfy_collector_protocol():
    assert isinstance(LocalJsonCollector(FIXTURES), Collector)
    assert isinstance(TextDirCollector(FIXTURES), Collector)
