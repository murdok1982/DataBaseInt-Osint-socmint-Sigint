"""Tests de aceptación de seguridad y RBAC (zona Agente 3).

Autorizado por: **SPEC-04 §4** (criterios de aceptación) y **SPEC-03 §6**.

- :mod:`tests.security.test_rbac_matrix` — las 28 celdas de la matriz.
- :mod:`tests.security.test_jwt` — expiración de 15 min y refresh cookie.
- :mod:`tests.security.test_argon2` — Argon2id verificable.
- :mod:`tests.security.test_login_hardening` — 2FA TOTP y bloqueo 429.
- :mod:`tests.security.test_audit_log` — bitácora de auditoría (SPEC-06 §1).

Ejecución (solo este subconjunto, sin tocar los tests de otros agentes)::

    cd apps/backend && python -m pytest tests/security -q
"""

__all__: list[str] = []
