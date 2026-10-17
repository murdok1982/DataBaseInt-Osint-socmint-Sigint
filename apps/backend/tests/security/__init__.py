"""Tests de aceptación de seguridad y RBAC (zona Agente 3).

Autorizado por: **SPEC-04 §4** (criterios de aceptación) y **SPEC-03 §6**.

- :mod:`tests.security.test_rbac_matrix` — las 15 celdas de la matriz.
- :mod:`tests.security.test_jwt` — expiración de 30 min y refresh cookie.
- :mod:`tests.security.test_argon2` — Argon2id verificable.

Ejecución (solo este subconjunto, sin tocar los tests de otros agentes)::

    cd apps/backend && python -m pytest tests/security -q
"""

__all__: list[str] = []
