# Contratos (Contracts)

Interfaces **congeladas** entre Backend, Base de Datos, Frontend e Informante IA.
Derivan de `/specs`. Una vez sellados, ningún agente los altera sin una nueva
spec aprobada por el Orquestador.

| Archivo | Deriva de | Autoría inicial | Versión |
| :--- | :--- | :--- | :--- |
| `graph-constraints.cypher` | SPEC-01 | Agente 1 | v2.0.0 |
| `extraction-schema.json` | SPEC-02 | Agente 2 | v2.0.0 |
| `openapi.yaml` | SPEC-03 + SPEC-04 | Agentes 3 y 4 | v2.0.0 |

## Ciclo de sellado
1. Los autores generan la versión inicial (borrador).
2. El Orquestador valida coherencia cruzada (nodos ↔ relaciones ↔ endpoints ↔ extracción ↔ RBAC).
3. Se registra en `SEAL.md` con fecha, versión y hash; a partir de ahí, **sellado**.

## Validación
- `bash scripts/validate-contracts.sh` — esquema JSON (ajv), OpenAPI (Redocly) y
  coherencia de nodos/relaciones vs SPEC-01.
- `python scripts/audit_sdd.py` — auditoría de trazabilidad spec → contrato → código → test.

```bash
# Contratos
bash scripts/validate-contracts.sh

# Auditoría (requiere el backend instalado para importar la app)
python scripts/audit_sdd.py
```

## Regla
Si el código contradice un contrato sellado, **gana el contrato**: el código se
corrige o se abre una nueva spec y se re-sella.
