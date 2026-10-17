"""Cliente LLM de extracción y *composition root* del proveedor (SPEC-02 §1.4/§5).

Autorizado por: **SPEC-02 §1.4** (extracción mediante LLM forzando el esquema),
**SPEC-02 §5** (arquitectura FastAPI + Celery/Redis) y **SPEC-04 §2** (cero
secretos por defecto en el repositorio).

Decisiones
----------
- :class:`LLMClient` (Protocol) es la interfaz que consume el pipeline
  (:class:`app.services.nlp_extractor.NLPExtractor`):
  ``extract(chunk, *, context=None)``.
- :class:`NullLLMClient` es **determinista** y **sin red**: devuelve una
  extracción vacía con la raíz del contrato v2.0
  (``{"extracted_triplets": [], "analytical_context_links": []}``). Permite
  operar y testear el sistema completo **sin API key ni proveedor** (modo
  recuperación).
- :func:`get_llm_client` lee ``LLM_PROVIDER`` del entorno. Por defecto ``null``.
  Un proveedor real (p. ej. ``openai``/``anthropic``/``ollama``) se configura por
  variables de entorno y se cablea aquí; mientras no esté implementado, se eleva
  :class:`~app.core.errors.ServiceUnavailable` (**503**), nunca un 500.
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional, Protocol, runtime_checkable

from app.core.errors import ServiceUnavailable
from app.services.chunking import Chunk

#: Proveedor por defecto: sin red, sin credenciales (operable *out of the box*).
DEFAULT_PROVIDER = "null"

#: Aliases aceptados para el cliente nulo.
_NULL_ALIASES = frozenset({"", "null", "none", "noop", "none", "off"})


@runtime_checkable
class LLMClient(Protocol):
    """Interfaz del extractor LLM (SPEC-02 §1 Fase 1).

    Devuelve un ``Mapping`` con ``extracted_triplets`` y/o
    ``analytical_context_links`` validable contra ``contracts/extraction-schema.json``
    (v2.0). El ``context`` es el resultado de la Fase 0 (pre-vuelo) y puede ser
    ``None`` (extracción sin contexto).
    """

    def extract(
        self, chunk: Chunk, *, context: Any = None
    ) -> Mapping[str, Any]:
        ...


class NullLLMClient:
    """Cliente LLM determinista que no extrae nada (0 tripletas).

    Es la implementación por defecto (``LLM_PROVIDER=null``): permite completar
    una ingesta y servir GraphRAG en **modo recuperación** sin proveedor externo
    ni clave de API.
    """

    #: Identificador legible del proveedor (diagnóstico).
    provider = DEFAULT_PROVIDER

    def extract(
        self, chunk: Chunk, *, context: Any = None
    ) -> Mapping[str, Any]:
        """Devuelve una extracción vacía v2.0 (determinista: ignora la entrada)."""
        return {"extracted_triplets": [], "analytical_context_links": []}


def resolve_provider(provider: Optional[str] = None) -> str:
    """Resuelve el proveedor efectivo desde el argumento o ``LLM_PROVIDER``."""
    raw = provider if provider is not None else os.environ.get("LLM_PROVIDER")
    return (raw or DEFAULT_PROVIDER).strip().lower()


def get_llm_client(provider: Optional[str] = None) -> LLMClient:
    """Fábrica del cliente LLM según ``LLM_PROVIDER`` (por defecto ``null``).

    :raises ServiceUnavailable: si el proveedor no está cableado todavía.
    """
    resolved = resolve_provider(provider)
    if resolved in _NULL_ALIASES:
        return NullLLMClient()
    raise ServiceUnavailable(
        f"Proveedor LLM '{resolved}' no implementado. Configura LLM_PROVIDER=null "
        "para operar sin proveedor o cablea el proveedor real en "
        "app/services/llm_client.py (SPEC-02 §1.4)."
    )


__all__ = [
    "DEFAULT_PROVIDER",
    "LLMClient",
    "NullLLMClient",
    "get_llm_client",
    "resolve_provider",
]
