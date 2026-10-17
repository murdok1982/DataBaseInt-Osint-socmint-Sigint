# OSINT Nexus — Instrucciones de Proyecto (SDD v2.0)

Este repositorio se rige por **Spec-Driven Development (SDD)**. Antes de tocar
cualquier archivo, lee y cumple:

1. `.antigravity/sdd-constitution.md` — reglas inquebrantables v2.0.
2. La spec pertinente en `/specs/*.spec.md`.
3. El contrato congelado en `/contracts/` que corresponda.

## Reglas rápidas
- **Spec First:** no se editan `/apps` ni `/deploy` sin spec que lo autorice.
- **Contract First:** cambios de grafo/API actualizan `/contracts` primero (y exigen re-sello).
- **Trazabilidad doble:** todo dato lleva fuente física (`source_report_id` + `page_number` + `exact_quote`) y, si aplica, contexto analítico (`referenced_analysis_id` + `impact_type`).
- **Inmutabilidad analítica:** ningún proceso automático altera/borra un nodo o arista con `locked_by_analyst: true`; solo añade evidencia o crea un `CoherenceAlert`.
- **Prohibición de ingesta ciega:** el Informante IA siempre consulta `/context/preflight` antes de `/ingest/informant`.
- **Cero exposición de DB:** Neo4j/Redis nunca publican puertos; todo pasa por FastAPI + JWT/API Key.
- **Verificación Cruzada:** cada tarea termina con un test de aceptación.
- Si una spec es ambigua, **detente** y eleva una `SPEC-GAP`; no improvises.

## Zonas (no escribir cruzado)
- **Agente 1:** `/apps/backend/app/db/**`, `app/services/entity_resolver.py`
- **Agente 2:** `/apps/backend/app/services/**` (ingesta/coherencia/GraphRAG), `app/schemas/**`, `app/workers/**`
- **Agente 3:** `/apps/backend/app/api/**`, `app/core/**`, `app/main.py`, `/deploy/**`
- **Agente 4:** `/apps/frontend/**`
- **Agente 5:** `/apps/informant-agent/**`
- `/specs/**` y `/contracts/**` (sellado): solo el Orquestador + humano.

## Comandos de verificación
- Backend tests: `cd apps/backend && python -m pytest -q`
- Informant Agent tests: `cd apps/informant-agent && python -m pytest -q`
- Validar contratos: `bash scripts/validate-contracts.sh` (ver `/contracts/README.md`)
- Auditoría SDD (trazabilidad spec→contrato→código→test): `python scripts/audit_sdd.py`

Los prompts operativos completos de cada agente están en
`.antigravity/agent-*-*.prompt.md` y, en formato OpenCode, en `.opencode/agents/`.
