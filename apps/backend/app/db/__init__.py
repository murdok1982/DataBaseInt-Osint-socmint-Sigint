"""Zona Agente 1 — capa de acceso al grafo (SPEC-01).

Contratos derivados:
- ``contracts/graph-constraints.cypher`` (SELLADO v1.0.0)
- ``specs/01-graph-ontology.spec.md``

Módulos:
- :mod:`app.db.driver` — factory del driver Neo4j (configuración por entorno).
- :mod:`app.db.repository` — repositorio Cypher parametrizado.
- :mod:`app.db.postgres_driver` — pool PostgreSQL 16 + pgvector (v3, Hito 1).
- :mod:`app.db.postgres_repository` — repositorio v3 sobre el contrato
  ``contracts/v3/db-contract.sql``.
"""

__all__: list[str] = []
