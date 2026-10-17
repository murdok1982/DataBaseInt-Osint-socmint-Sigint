"""Zona Agente 3 — núcleo transversal del backend (config, seguridad, RBAC).

Autorizado por:
- **SPEC-04 §2** (Argon2id, JWT 15 min + refresh cookie HttpOnly/Secure, 2FA).
- **SPEC-06 §1** (bitácora de auditoría, ``app.core.audit``).
- **SPEC-04 §1** (matriz de roles RBAC).
- **SPEC-03 §1** (convenciones de la API: ``Bearer`` JWT, envoltura de errores).

Módulos:
- :mod:`app.core.config` — settings desde variables de entorno.
- :mod:`app.core.security` — hashing Argon2id y emisión/verificación de JWT.
- :mod:`app.core.users` — directorio de operadores (contrato interno).
- :mod:`app.core.rbac` — roles, permisos y dependencia ``require_role``.
- :mod:`app.core.errors` — envoltura de error ``{"error": {code, message}}``.
"""

__all__: list[str] = []
