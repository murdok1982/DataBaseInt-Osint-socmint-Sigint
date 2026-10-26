"""Harness Hermes v3.1 — contrato, OPSEC y gateway de tools.

Zona: Agente 5 — ``apps/agents/**``.

El paquete no importa ``app.*`` del backend: se despliega como proceso separado
y sólo comparte los contratos JSON de ``/contracts/v3``. El gateway inyecta un
``HermesBackend`` que implementa la persistencia con ``svc_hermes``.
"""

from __future__ import annotations

from .models import TOOL_MODELS, ToolEnvelope, ToolResult

__all__ = ["TOOL_MODELS", "ToolEnvelope", "ToolResult"]
