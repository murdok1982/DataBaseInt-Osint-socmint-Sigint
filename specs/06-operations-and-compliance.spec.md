# SPEC-06: Operación, Auditoría y Cumplimiento (v2.2)

> Spec rectora del **Agente 3 (DevOps/Sec)** para los requisitos no funcionales
> de una plataforma de inteligencia de titularidad estatal. Complementa SPEC-04
> (control de acceso) con la **rendición de cuentas**: quién hizo qué, cuándo y
> cómo se recupera el servicio. No modifica la ontología (SPEC-01) ni el
> contrato de datos; solo añade la cabecera `X-Request-ID` (ver §1.3).

## 1. Bitácora de auditoría de accesos

### 1.1 Alcance
Toda petición HTTP a `/api/v1/**` genera **exactamente un** evento de auditoría
en el logger `osint.audit`, en una línea **JSON** por evento, emitido a `stdout`
(lo recoge el driver de logs de Docker, §4).

### 1.2 Campos del evento (`event = "http_request"`)
| Campo | Contenido |
| :--- | :--- |
| `ts` | Marca de tiempo UTC ISO-8601 |
| `request_id` | Identificador de correlación (§1.3) |
| `actor_id` | `id` del `User` o de la `ApiKey`; `null` si anónimo |
| `actor_role` | `ADMIN`/`ANALYST`/`VIEWER`/`AI_INFORMANT`; `null` si anónimo |
| `method` | Verbo HTTP |
| `route` | **Plantilla** de ruta (`/api/v1/graph/nodes/{id}`), nunca la URL real |
| `resource_id` | Valor del parámetro de ruta `{id}`/`{job_id}`/`{alert_id}`, si existe |
| `status` | Código de respuesta |
| `duration_ms` | Latencia del servidor |
| `client_ip` | IP del cliente (misma regla de proxy de confianza que SPEC-04 §2.1) |

### 1.3 Correlación
- La respuesta incluye siempre `X-Request-ID`. Si el cliente envía un
  `X-Request-ID` con formato seguro (`[A-Za-z0-9-]{8,64}`) se reutiliza; si no,
  el servidor genera un UUID4.

### 1.4 Eventos de seguridad (`event = "auth"`)
Además del evento HTTP, `/auth/login` emite un evento con `outcome` ∈
{`SUCCESS`, `FAILURE`, `LOCKED`} y:
- en `SUCCESS`: `actor_id` y `actor_role`;
- en `FAILURE`/`LOCKED`: `username_sha256` (SHA-256 truncado a 16 hex del
  usuario en minúsculas), **nunca** el usuario en claro (puede contener una
  contraseña tecleada por error).

### 1.5 Prohibiciones (datos que **jamás** se registran)
Cuerpos de petición o respuesta, *query strings*, cabeceras `Authorization`,
`X-API-Key`, `X-Preflight-Token` y `Cookie`, contraseñas, códigos TOTP, tokens y
contenido de informes. Los errores devueltos al cliente no exponen trazas ni
detalles internos (sobre `{error:{code,message}}` de SPEC-03 §1).

## 2. Retención y minimización de datos
- Los periodos de conservación de informes (`/storage/raw_reports`), chunks,
  grafo y bitácora de auditoría los **fija el marco legal de la institución
  usuaria**, no el equipo de desarrollo.
- **SPEC-GAP-06.1 (abierta):** pendiente de que el responsable del tratamiento
  comunique los plazos. Hasta entonces **no se automatiza ningún borrado**
  (Constitución, Ley 10: ante ambigüedad, no improvisar). El borrado manual de
  un nodo sigue siendo exclusivo de `ADMIN` (`deleteGraphNode`) y queda en la
  bitácora (§1).
- Los volúmenes `neo4j_data`, `reports_data` y las copias (§3) deben residir en
  almacenamiento **cifrado en reposo** del VPS.

## 3. Copias de seguridad y restauración
| Objetivo | Valor |
| :--- | :--- |
| **RPO** (pérdida máxima de datos) | 24 h (copia diaria) |
| **RTO** (tiempo máximo de recuperación) | 4 h |
| Retención de copias | `BACKUP_RETENTION_DAYS` (por defecto 30) |

- `deploy/scripts/backup.sh` genera, en una sola ejecución:
  1. copia **en caliente** de Neo4j (`neo4j-admin database backup`, Enterprise);
  2. archivo `tar.gz` del volumen de informes (`/storage`);
  3. **cifrado simétrico AES-256** (GnuPG) con la frase de
     `BACKUP_PASSPHRASE_FILE` (fichero `0600`, fuera del repositorio);
  4. manifiesto `SHA256SUMS` de los artefactos cifrados.
- Nunca se guarda una copia sin cifrar fuera del contenedor.
- `deploy/scripts/restore.sh` verifica el `SHA256SUMS` **antes** de descifrar y
  exige confirmación explícita (`--yes`) porque sobrescribe datos.
- Se ensaya una restauración completa al menos **una vez por trimestre** y se
  anota en el runbook (`deploy/README.md`).

## 4. Observabilidad
- Logs en JSON a `stdout`; cada servicio de `docker-compose.prod.yml` rota sus
  logs (`json-file`, `max-size` 10 MB, `max-file` 10).
- *Health check* público: `GET /api/v1/info` (`getInfo`). El compose declara un
  `healthcheck` del backend sobre ese endpoint.
- **SPEC-GAP-06.2 (abierta, no bloqueante):** métricas (Prometheus) y envío de
  la bitácora a un SIEM institucional quedan fuera de v2.2; el destino lo
  decide la institución (Constitución: solo destinos autorizados).

## 5. Rotación de secretos
| Secreto | Procedimiento | Efecto |
| :--- | :--- | :--- |
| `JWT_SECRET_KEY` | Nuevo valor en `.env` + `up -d --force-recreate backend` | Invalida todas las sesiones |
| `PREFLIGHT_SECRET` | Ídem | Invalida los `preflight_token` vigentes |
| API Key M2M | `init-api-keys.py` emite una nueva; la anterior se marca `revoked: true` | La clave revocada responde `401` |
| `NEO4J_PASSWORD` | `ALTER USER` en Neo4j + `.env` + recrear backend/worker | — |
| Secreto TOTP de un operador | Regenerar con `init-admin-user.py --totp` | El código anterior deja de valer |

Rotación mínima: **cada 90 días** y de inmediato ante sospecha de compromiso.

## 6. Criterios de Aceptación
- [ ] Cada petición a `/api/v1` produce un único evento JSON `http_request` con
      los campos de §1.2 y la respuesta lleva `X-Request-ID`.
- [ ] Ningún evento contiene cabeceras de credenciales, query string, cuerpo,
      contraseña, TOTP ni token (§1.5).
- [ ] Un `X-Request-ID` del cliente con formato inválido se sustituye por uno
      generado por el servidor.
- [ ] El login emite un evento `auth` con `outcome`, sin usuario en claro en
      `FAILURE`/`LOCKED`.
- [ ] `backup.sh` y `restore.sh` existen, cifran/verifican y `restore.sh` no
      actúa sin `--yes`.
- [ ] Todos los servicios de producción rotan logs y el backend declara
      `healthcheck`.
