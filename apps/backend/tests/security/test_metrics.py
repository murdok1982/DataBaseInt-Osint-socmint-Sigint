"""Métricas Prometheus del backend (SPEC-06 §1 · PLAN.md Hito 6).

``/metrics`` vive fuera de ``/api/v1``: no forma parte del contrato OpenAPI.
"""

from __future__ import annotations

from typing import Any


# =====================================================================
# 5) Métricas Prometheus (SPEC-06 §1)
# =====================================================================
def test_metrics_endpoint_counts_api_requests_by_route_template(client: Any) -> None:
    assert client.get("/api/v1/info").status_code == 200
    body = client.get("/metrics").text
    assert (
        'osint_http_requests_total{method="GET",route="/api/v1/info",status="200"}'
        in body
    )
    assert "osint_http_request_duration_seconds" in body


def test_metrics_are_not_part_of_the_api_contract(client: Any) -> None:
    """``/metrics`` vive fuera de ``/api/v1``: no aparece en el OpenAPI."""
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(path.startswith("/metrics") for path in paths)
