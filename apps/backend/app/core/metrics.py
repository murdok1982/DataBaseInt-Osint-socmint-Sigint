"""Métricas Prometheus del backend (SPEC-06 §1 · PLAN.md Hito 6).

Expone, en ``/metrics`` (fuera de ``/api/v1``):

- ``osint_http_requests_total{method,route,status}``
- ``osint_http_request_duration_seconds{method,route}``

La etiqueta ``route`` es la **plantilla** de la ruta (``/api/v1/graph/nodes/{id}``),
nunca la URL real: así la cardinalidad está acotada por el contrato. Las
peticiones que no casan con ninguna ruta se agrupan como ``unmatched``.

Nginx **no** enruta ``/metrics`` al exterior (solo ``/`` y ``/api/``); Prometheus
lo extrae por la red interna ``internal_intel_net``.
"""

from __future__ import annotations

import time
from typing import Any, Awaitable, Callable

from prometheus_client import Counter, Histogram, make_asgi_app
from starlette.routing import Match

HTTP_REQUESTS = Counter(
    "osint_http_requests_total",
    "Peticiones HTTP atendidas, por método, plantilla de ruta y código de estado.",
    ["method", "route", "status"],
)
HTTP_LATENCY = Histogram(
    "osint_http_request_duration_seconds",
    "Latencia de las peticiones HTTP en segundos, por método y plantilla de ruta.",
    ["method", "route"],
)

UNMATCHED_ROUTE = "unmatched"


def route_template(app: Any, scope: dict[str, Any]) -> str:
    """Plantilla de la ruta que atiende ``scope`` (``unmatched`` si ninguna)."""
    for route in getattr(app, "routes", []):
        match, _ = route.matches(scope)
        if match != Match.NONE:
            return str(getattr(route, "path", UNMATCHED_ROUTE))
    return UNMATCHED_ROUTE


class PrometheusMiddleware:
    """Middleware ASGI que registra contador y latencia por petición HTTP."""

    def __init__(self, app: Callable[..., Awaitable[None]]) -> None:
        self.app = app

    async def __call__(
        self, scope: dict[str, Any], receive: Any, send: Any
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            method = str(scope.get("method", "GET"))
            route = route_template(scope.get("app"), scope)
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_LATENCY.labels(method, route).observe(time.perf_counter() - started)


def metrics_app() -> Any:
    """Aplicación ASGI que sirve el formato de texto de Prometheus."""
    return make_asgi_app()


__all__ = [
    "HTTP_LATENCY",
    "HTTP_REQUESTS",
    "PrometheusMiddleware",
    "UNMATCHED_ROUTE",
    "metrics_app",
    "route_template",
]
