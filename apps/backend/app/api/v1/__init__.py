"""Rutas de la versión 1 de la API (base ``/api/v1``, SPEC-03 §1).

Un módulo por *tag* del contrato SELLADO v1.0.0:

- :mod:`app.api.v1.auth`    → ``authLogin``, ``authRefresh``, ``authLogout``, ``authMe``
- :mod:`app.api.v1.graph`   → ``listGraphNodes``, ``getGraphNode``, ``updateGraphNode``,
  ``deleteGraphNode``, ``listGraphEdges``, ``getGraphNeighborhood``
- :mod:`app.api.v1.ingest`  → ``createIngestJob``, ``getIngestJob``, ``ingestProgressWs``
- :mod:`app.api.v1.reports` → ``getReport``, ``getReportFile``, ``getReportChunks``
- :mod:`app.api.v1.search`  → ``searchRag``
"""

__all__: list[str] = []
