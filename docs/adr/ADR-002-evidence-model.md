# ADR-002 — Modelo de evidencia y cadena de custodia

- **Estado:** Aceptado (2026-10-23)
- **Contexto:** Hito 1, `SPEC.md` §6 y R-01/R-02/R-19/R-20
- **Decisores:** Agente 0 (Gobernanza) + Agente 1 (Datos/Custodia)
- **Contrato:** `contracts/v3/db-contract.sql`

## Contexto

La plataforma v2.2 guardaba el grafo en Neo4j sin una copia probatoria de los
bytes capturados. Para nivel institucional se necesita demostrar:

1. qué se capturó exactamente;
2. quién lo capturó y bajo qué misión/PIR;
3. que el contenido no cambió después de la captura;
4. que el análisis (incluido LLM) no precede a la custodia.

## Decisión

1. **Los bytes son la fuente de verdad.** `staging.raw_intel_ledger` conserva
   `raw_payload_bytes bytea` con los bytes exactos de captura. `raw_payload
   jsonb` es una copia parseada para consulta; la DB verifica que ambos
   representan el mismo JSON.
2. **SHA-256 antes de cualquier LLM.** El colector calcula
   `payload_sha256 = sha256(raw_payload_bytes)` y lo envía junto con los bytes.
   `staging.append_evidence` y el trigger `trg_ledger_verify` recalculan el
   hash; si no coincide, la inserción falla con `23514`.
3. **Canonicalización declarada.** El contrato registra
   `hash_algorithm = 'SHA-256'` y `canonicalization = 'RFC8785-JCS'`. La
   implementación Python canonicaliza antes de serializar; la DB no reinterpreta
   el JSON para el hash, solo verifica los bytes.
4. **Cadena de custodia.** Cada fila encadena `prev_hash` y `record_hash`
   (hash de `prev_hash || evidence_id || payload_sha256 || captured_at_utc ||
   collector_agent`). La inserción se serializa con un advisory lock.
5. **Append-only real.** Triggers bloquean `UPDATE`, `DELETE` y `TRUNCATE` con
   `42501`. Los adjuntos también son append-only y cada uno lleva su SHA-256.
6. **Identidad no repudiable.** `collector_agent` se deriva de `session_user`
   mediante `staging.current_collector_agent()`; el cliente no puede declarar
   otro agente. `MIGRATION_LEGACY` solo se usa en inserción directa del ETL,
   nunca en `append_evidence`.
7. **Retención y legal hold.** `retention_until` y `legal_hold` permiten
   políticas de retención sin borrar evidencia bajo custodia; el borrado físico
   queda fuera del contrato operativo y requiere un procedimiento aprobado.

## Consecuencias

- **Positivas:** evidencia verificable, no repudio, base para Admiralty y para
  detección de contradicciones; el LLM nunca ve datos sin hash.
- **Coste:** duplicación bytes/JSONB, crecimiento append-only y necesidad de
  backups/particionado a largo plazo.
- **Riesgo residual:** SHA-256 sin HMAC no impide que un actor con acceso de
  owner reescriba toda la cadena; la mitigación es separar el rol owner del
  plano de aplicación y exportar sellos periódicos a almacenamiento WORM.
- **Migración:** el ETL inserta con `MIGRATION_LEGACY` y hash de los bytes
  exportados; los casos no mapeables van a cuarentena, no al grafo.
