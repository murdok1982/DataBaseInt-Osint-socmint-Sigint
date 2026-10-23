# OSINT Nexus — Instrucciones de Proyecto (SDD v3.0)

Este repositorio se rige por **Spec-Driven Development (SDD)**. Antes de tocar
cualquier archivo, lee y cumple:

1. `.antigravity/sdd-constitution.md` — reglas inquebrantables.
2. `SPEC.md` v3.0.0 (aprobada el 2026-10-23) y las specs históricas de
   `/specs/*.spec.md` que sigan vigentes.
3. El contrato correspondiente: `contracts/v3/db-contract.sql`,
   `contracts/v3/agent-contracts.json` o los contratos v2.2 sellados.

## Reglas rápidas

- **Spec First:** no se edita `/apps`, `/deploy` ni `/scripts` sin requisito
  EARS, tarea de `PLAN.md` y contrato asociado.
- **Contract First:** todo cambio de esquema, API o mensaje inter-agente se
  refleja primero en `/contracts` y exige re-sello en `contracts/SEAL.md`.
- **Trazabilidad doble:** todo dato validado enlaza `supporting_evidence_ids`
  con `staging.raw_intel_ledger`; el ledger conserva bytes, SHA-256, TLP y
  cadena de custodia. Sin evidencia válida no hay entidad ni relación.
- **Inmutabilidad:** el ledger es append-only. El grafo validado no se borra:
  se revoca (`revoked = true`) con motivo y actor. Ningún proceso automático
  altera un nodo bloqueado por analista.
- **Separación de agentes:** solo `ATENEA` (`svc_atenea`) escribe el grafo
  validado. `HERMES`, `ARACNE` y `ARGOS` solo escriben el ledger. `DIRECTOR_CELL`
  gobierna misiones, PIR, EEI y tasking. El `session_user` de PostgreSQL es la
  identidad no repudiable; no se acepta `collector_agent` enviado por el cliente.
- **Cero exposición de DB:** PostgreSQL, Neo4j y Redis nunca publican puertos.
  Todo pasa por FastAPI + JWT/API Key + RLS/TLP.
- **OPSEC:** una violación de política HUMINT aborta la acción y genera alerta;
  no se degrada a warning.
- **Verificación Cruzada:** cada tarea termina con un test de aceptación. Los
  tests que requieren Docker se ejecutan en CI; un `skip` local no cierra el
  hito.
- Si una spec es ambigua, **detente** y eleva una `SPEC-GAP`; no improvises.

## Zonas de escritura v3 (no escribir cruzado)

| Agente | Zona |
|---|---|
| **Agente 0 — Gobernanza** | `SPEC.md`, `PLAN.md`, `AUDIT.md`, `/contracts/**`, `AGENTS.md`, `scripts/audit_sdd.py`, `scripts/validate-contracts.sh` |
| **Agente 1 — Datos/Custodia** | `apps/backend/app/db/**`, `migrations/**`, `scripts/migrate/**`, `contracts/v3/db-contract.sql` |
| **Agente 2 — Servicios** | `apps/backend/app/services/**`, `app/schemas/**`, `app/workers/**` |
| **Agente 3 — API/Core/Deploy** | `apps/backend/app/api/**`, `app/core/**`, `app/main.py`, `deploy/**` |
| **Agente 4 — Frontend** | `apps/frontend/**` |
| **Agente 5 — Agentes** | `apps/agents/**`, `apps/informant-agent/**` |

`/specs/**` y `/contracts/**` sellados: solo el Orquestador + humano.

## Estado del Hito 1

- `contracts/v3/db-contract.sql`: aprobado en SPEC, **pendiente de Testcontainers
  y sello criptográfico**.
- `apps/backend/app/db/postgres_repository.py`: subconjunto compatible con
  `GraphRepository`; no sustituye aún al repositorio Neo4j en producción.
- `deploy/docker-compose.prod.yml`: servicio `postgres` (`pgvector/pgvector:pg16`)
  en `internal_intel_net`, sin `ports:`.
- Tests: `apps/backend/tests/v3/db/` (T-H1-01…T-H1-10). Requieren Docker.

## Comandos de verificación

- Backend tests: `cd apps/backend && python -m pytest -q`
- Informant Agent tests: `cd apps/informant-agent && python -m pytest -q`
- Validar contratos: `bash scripts/validate-contracts.sh`
- Auditoría SDD: `python scripts/audit_sdd.py`
- Contratos v3 (sintaxis): ver `contracts/v3/README.md`
- Tests PostgreSQL v3: `cd apps/backend && python -m pytest tests/v3/db -q`

Los prompts operativos completos de cada agente están en
`.antigravity/agent-*-*.prompt.md` y, en formato OpenCode, en `.opencode/agents/`.
Si un prompt contradice este archivo, prevalece `SPEC.md` y este `AGENTS.md`.
