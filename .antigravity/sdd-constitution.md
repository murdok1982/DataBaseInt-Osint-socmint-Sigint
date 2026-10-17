# Constitución Spec-Driven Development (SDD) v2.0 — OSINT Nexus

> Documento de rango superior. Si un agente entra en conflicto con esta
> Constitución, la Constitución gana. La especificación es la única fuente de
> verdad; el código es un artefacto derivado y regenerable.

## Leyes Inquebrantables (v2.0)

1. **Spec & Contract First.** Ningún agente modifica `/apps` o `/deploy` sin
   actualizar antes `/specs` y `/contracts`. Cada cambio cita el ID de spec que
   lo autoriza (`Autorizado por: SPEC-0X §Y`).

2. **Inmutabilidad Analítica (`locked_by_analyst`).** Ningún proceso automático
   del Informante IA puede alterar ni eliminar un nodo o una relación con
   `locked_by_analyst: true`. El motor automático solo puede **añadir nuevas
   evidencias** o **levantar un `CoherenceAlert`** de contradicción.

3. **Prohibición de Ingesta Ciega.** El Informante IA tiene prohibido enviar
   datos a `POST /api/v1/ingest/informant` sin haber consultado antes
   `POST /api/v1/context/preflight`. La ingesta sin pre-vuelo se rechaza (`409`).

4. **Trazabilidad Doble (Fuente + Contexto Analítico).** Todo dato inyectado por
   el Informante IA debe enlazar al documento físico
   (`source_report_id` + `page_number` + `exact_quote`) y, si aplica, al análisis
   humano (`referenced_analysis_id` + `impact_type` ∈ {`CORROBORATES`,
   `CONTRADICTS`, `EXTENDS`, `ANSWERS_REQUIREMENT`}).

5. **Aislamiento en VPS.** La base de datos **jamás** expone sus puertos
   (7474/7687) al host público. Todo acceso externo pasa por Nginx con TLS 1.3 y
   autenticación verificada por FastAPI (JWT para humanos, API Key M2M para la IA).

6. **Verificación Cruzada.** Toda tarea termina con un test de aceptación en
   `/apps/backend/tests/` (o el módulo correspondiente) que demuestre sus
   criterios al 100%.

## Leyes de Coherencia Humano–IA (nuevas en v2.0)

7. **Un humano manda sobre la evidencia.** La conclusión del Analista
   (`conclusion_body`) nunca es sobrescrita por la IA. La IA aporta evidencia y
   vínculos; el Analista decide.

8. **La IA no interpreta, referencia.** El Informante IA no inventa relaciones
   ni deduce: toda tripleta y todo vínculo analítico exige `exact_quote` literal.
   La interpretación estratégica es competencia exclusiva del Analista.

9. **Conflicto = Alerta, no borrado.** Una discrepancia genera un
   `CoherenceAlert` y una arista `[:CONTRADICTS]`; jamás edita la tesis humana.

10. **Fallo ruidoso.** Ante ambigüedad, el agente **se detiene** y eleva
    `SPEC-GAP`. No improvisa.

## Roles y Zonas de Escritura (Ownership)

| Zona | Agente propietario | Solo lectura |
| :--- | :--- | :--- |
| `/specs/**` | Agente 0 + humano | Todos |
| `/contracts/**` (sello) | Agente 0 | Todos |
| `app/db/**`, `entity_resolver.py` | Agente 1 | 2, 3, 4, 5 |
| `app/services/**` (ingesta, coherencia, GraphRAG), `app/schemas/**`, `app/workers/**` | Agente 2 | 1, 3, 4, 5 |
| `app/core/**`, `app/api/**`, `/deploy/**` | Agente 3 | 2, 4, 5 |
| `/apps/frontend/**` | Agente 4 | 1, 2, 3, 5 |
| `/apps/informant-agent/**` | Agente 5 | 1, 2, 3, 4 |

**Un escritor por zona a la vez.** Dos agentes nunca editan el mismo archivo en paralelo.

## Ciclo SDD

1. **Esqueleto** — `.antigravity`, `/specs`, `/contracts`.
2. **Congelar contratos** — solo los contratos; se sellan.
3. **Generación guiada** — el código se deriva de los contratos congelados.
4. **Auditoría SDD** — conformidad spec→contrato→código→test antes del despliegue.

## Definition of Done

- [ ] Existe la spec que autoriza el cambio y está citada.
- [ ] Los contratos afectados están actualizados y **sellados** (hash en `SEAL.md`).
- [ ] El test de aceptación correspondiente pasa.
- [ ] No se introdujeron campos/endpoints/nodos fuera de spec.
- [ ] Trazabilidad doble preservada (`source_report_id` + vínculo analítico si aplica).
- [ ] Se respetó `locked_by_analyst` (ninguna sobrescritura automática).
