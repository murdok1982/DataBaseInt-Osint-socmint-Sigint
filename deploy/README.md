# Despliegue en VPS — OSINT Nexus

Guía paso a paso para levantar el stack completo en un **VPS limpio**
(Ubuntu 22.04/24.04 LTS). Deriva de **SPEC-04 §2/§3** (red, cifrado, hardening),
**SPEC-02 §5** (worker Celery/Redis) y los contratos sellados en `/contracts`.

```
deploy/
├── docker-compose.prod.yml        # stack de producción (2 redes, 1 solo ports:)
├── .env.example                   # plantilla de secretos (copiar a .env)
├── informant-agent.Dockerfile     # imagen del Informante IA (M2M)
├── nginx/
│   ├── nginx.conf                 # proxy inverso TLS 1.3 (único punto de entrada)
│   ├── certs/                     # fullchain.pem + privkey.pem (no versionado)
│   └── webroot/                   # ACME (/.well-known/acme-challenge)
└── scripts/
    ├── apply-constraints.sh       # aplica contracts/graph-constraints.cypher
    ├── smoke-test.sh              # verificación de aceptación (endpoints reales)
    ├── init-admin-user.py         # primer operador ADMIN (Argon2id)
    ├── init-api-keys.py           # API Key M2M AI_INFORMANT
    └── vps-hardening.sh           # UFW + fail2ban + SSH (idempotente)
```

> Todos los comandos se ejecutan **desde la raíz del repositorio**.
> Define una vez: `export COMPOSE="docker compose -f deploy/docker-compose.prod.yml"`.

---

## 1. Arquitectura desplegada

| Servicio | Imagen | Red | `ports:` | Rol |
| :--- | :--- | :--- | :--- | :--- |
| `nginx` | `nginx:1.27-alpine` | `public_net` | **80, 443** | Único entrada TLS (SPEC-04 §3) |
| `frontend` | build `apps/frontend` | `public_net` | — | Dashboard Next.js |
| `backend` | build `apps/backend` | `public_net` + `internal_intel_net` | — | FastAPI `/api/v1` |
| `worker` | build `apps/backend` (misma imagen) | `internal_intel_net` | — | Celery: `osint.workers.ingest_report` |
| `informant-agent` | `deploy/informant-agent.Dockerfile` | `internal_intel_net` | — | Job M2M por lotes |
| `neo4j` | `neo4j:5-enterprise` | `internal_intel_net` | — | Grafo (jamás expuesto) |
| `redis` | `redis:7-alpine` | `internal_intel_net` | — | Estado de jobs + broker Celery |

**Regla 4 (Constitución):** Neo4j y Redis **nunca** publican `ports:`. En este
compose existe **exactamente un `ports:`** (Nginx). Puedes comprobarlo:

```bash
docker compose -f deploy/docker-compose.prod.yml config | grep -c '^ *ports:'
# => 1
```

---

## 2. Prerrequisitos

- **VPS**: 4 vCPU / **8 GB RAM** / 40 GB SSD (Neo4j reserva 2G de heap + 1G de
  pagecache). Ajusta `NEO4J_server_memory_heap_max__size` si tienes menos RAM.
- **Docker Engine ≥ 24** con `docker compose` v2 (`docker compose version`).
- **Dominio** con registro DNS apuntando al VPS (paso 3).
- Puertos **22, 80, 443** abiertos en el proveedor.
- `git`, `curl`, `python3` (opcional, para scripts fuera de contenedor).

---

## 3. DNS

Crea un registro **A** `osint-nexus.example.com → <IP_DEL_VPS>`
(y opcional `api.osint-nexus.example.com`). Verifica:

```bash
dig +short osint-nexus.example.com
```

---

## 4. Certificados TLS

Nginx espera `deploy/nginx/certs/fullchain.pem` y `privkey.pem`.

### Opción A — Let's Encrypt con certbot (recomendado)

Los certificados **no** pueden versionarse; genéralos en el host:

```bash
sudo apt-get install -y certbot
mkdir -p deploy/nginx/webroot deploy/nginx/certs

# Con Nginx ya sirviendo el webroot (paso 6) — renovación sin parar el servicio:
sudo certbot certonly --webroot -w "$PWD/deploy/nginx/webroot" \
  -d osint-nexus.example.com --agree-tos -m tu@correo.tld --non-interactive

sudo cp /etc/letsencrypt/live/osint-nexus.example.com/fullchain.pem deploy/nginx/certs/fullchain.pem
sudo cp /etc/letsencrypt/live/osint-nexus.example.com/privkey.pem  deploy/nginx/certs/privkey.pem
sudo chmod 644 deploy/nginx/certs/fullchain.pem
sudo chmod 600 deploy/nginx/certs/privkey.pem
```

> Si emites **antes** de levantar el stack, usa `--standalone` (certbot abre el
> puerto 80) y luego copia los PEM como arriba.
> Renovación: `sudo certbot renew --quiet && docker compose ... restart nginx`.

### Opción B — Certificados propios

Coloca tu `fullchain.pem` y `privkey.pem` en `deploy/nginx/certs/`.
Solo se negocian **TLS 1.3** (SPEC-04 §2).

---

## 5. `.env` (secretos)

```bash
cp deploy/.env.example deploy/.env
```

Genera los secretos **en el propio VPS** (nunca uses los `CHANGE_ME`):

```bash
python3 - <<'PY'
import secrets
print("JWT_SECRET_KEY=" + secrets.token_urlsafe(64))
print("PREFLIGHT_SECRET=" + secrets.token_urlsafe(48))
print("NEO4J_PASSWORD=" + secrets.token_urlsafe(32))
print("INFORMANT_API_KEY=" + secrets.token_urlsafe(32))   # provisional; ver paso 9
PY
```

Edita `deploy/.env` y completa al menos:

| Variable | Notas |
| :--- | :--- |
| `PUBLIC_HOSTNAME` | Dominio real (sin `https://`). |
| `ENVIRONMENT` | `production` (exige `JWT_SECRET_KEY`). |
| `JWT_SECRET_KEY`, `PREFLIGHT_SECRET` | Del generador anterior. |
| `NEO4J_PASSWORD`, `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes` | Ver §Neo4j. |
| `INFORMANT_API_KEY` | Clave M2M en claro (paso 9). |
| `CELERY_BROKER_URL`, `WORKER_CONCURRENCY` | Worker Celery (SPEC-02 §5). |
| `STORAGE_RAW_DIR=/storage/raw_reports`, `STORAGE_CHUNKS_DIR=/storage/processed_chunks` | Coinciden con el volumen `reports_data:/storage`. |

> `deploy/.env` está en `.gitignore` (`.env`): **jamás** se versiona.

---

## 6. Build y arranque

```bash
export COMPOSE="docker compose -f deploy/docker-compose.prod.yml"

$COMPOSE build            # construye frontend, backend y worker (misma imagen)
$COMPOSE up -d            # arranca todo el stack

$COMPOSE ps               # todos "running"; informant-agent termina (job)
$COMPOSE logs -f backend  # revisa el arranque de la API
```

Notas:
- `worker` reutiliza la imagen del backend (`osint-nexus-backend:latest`): un
  solo `build` para ambos.
- El contenedor **`informant-agent` es un job** (`restart: "no"`): se ejecuta y
  termina. Si `deploy/informant-sources/` está vacío acabará con error, es normal.
- Neo4j tarda ~20-40 s en aceptar conexiones la primera vez (crea el usuario con
  `NEO4J_AUTH`).

---

## 7. Aplicar los constraints del grafo

```bash
bash deploy/scripts/apply-constraints.sh
```

Es **idempotente** (`CREATE ... IF NOT EXISTS`): úsalo tras cada arranque de
Neo4j o al cambiar `contracts/graph-constraints.cypher`. Aplica el contrato
sellado vía `docker compose exec neo4j cypher-shell` y **verifica** que existen
las 25 constraints y los 12 índices esperados.

> ⚠️ **SPEC-GAP-3.8 (índice vectorial).** `entity_embedding_idx` es un índice
> vectorial **multi-label** (`FOR (n:Person|Organization|CyberAsset)`). Neo4j
> **5.x** solo admite índices vectoriales de un único label; el soporte
> multi-label llegó en **Neo4j 2026.01+**. El script lo reporta como `[GAP]` y
> termina en `0` (salvo `STRICT_VECTOR_INDEX=1`). El resto del contrato se aplica
> y verifica. Ver §"Decisiones y SPEC-GAP".

---

## 8. Primer operador ADMIN

`init-admin-user.py` pide la contraseña de forma interactiva y emite el hash
Argon2id + un fragmento listo para `.env`. Ejecútalo **dentro del backend**
(tiene `argon2-cffi`):

```bash
$COMPOSE exec backend python - --username admin < deploy/scripts/init-admin-user.py
```

Copia la línea **`OSINT_USERS_JSON=...`** (o `OSINT_ADMIN_USERNAME` +
`OSINT_ADMIN_PASSWORD_HASH`) a `deploy/.env` y recrea el backend:

```bash
$COMPOSE up -d --force-recreate backend
```

Criterio de aceptación (SPEC-04 §4): **sin credenciales por defecto**; hasta que
definas el `OSINT_USERS_JSON`, todo login responde `401`.

---

## 9. API Key M2M (Informante IA)

Genera la clave M2M (rol `AI_INFORMANT`, hasheada con Argon2id, SPEC-04 §5.2):

```bash
$COMPOSE exec backend python - --client-name informant-agent --generate \
  < deploy/scripts/init-api-keys.py
```

El script imprime la **clave en claro una sola vez** y un fragmento
`OSINT_API_KEYS_JSON=[...]`. En `deploy/.env`:

- pega el fragmento en `OSINT_API_KEYS_JSON=...` (lo verifica el backend), y
- define `INFORMANT_API_KEY=<clave en claro>` (la usa el informant-agent).

```bash
$COMPOSE up -d --force-recreate backend informant-agent
```

---

## 10. Arrancar el Informante IA

Es un job de lote que hace `preflight → extraer → validar → ingerir`
(SPEC-02 §1). Coloca fuentes OSINT en `deploy/informant-sources/`
(`*.json` o `*.txt`, montado read-only en `/sources`) y ejecútalo:

```bash
mkdir -p deploy/informant-sources
$COMPOSE run --rm informant-agent run --source-dir /sources
```

Programa ejecuciones con `cron`/`systemd-timer` (p. ej. cada hora) usando el
mismo `docker compose run`. El agente **nunca** toca Neo4j/Redis: solo la API.

---

## 11. Verificación (smoke test)

Contra el stack vivo, recorre los endpoints reales del contrato:

```bash
SMOKE_USERNAME=admin \
SMOKE_PASSWORD='<password de init-admin-user>' \
SMOKE_API_KEY='<clave M2M del paso 9>' \
PUBLIC_HOSTNAME=osint-nexus.example.com \
bash deploy/scripts/smoke-test.sh
```

Comprueba (Fig. `contracts/openapi.yaml`):

| # | Endpoint | Esperado |
| :-- | :--- | :--- |
| 1 | `GET /api/v1/info` (público) | `200` |
| 2 | `POST /api/v1/auth/login` | `200` |
| 3 | `POST /api/v1/context/preflight` (M2M) | `200` + `preflight_token` |
| 4 | `POST /api/v1/ingest/informant` (token) | `202` |
| 5 | `GET /api/v1/graph/nodes` | `200` |
| 6 | `POST /api/v1/search/rag` | `200` |
| 7 | `GET /api/v1/coherence/alerts` | `200` |

Cualquier desviación ⇒ **exit ≠ 0**. Es repetible/idempotente (no muta estado
persistente). Variables opcionales: `SMOKE_BASE_URL` (si no usas
`PUBLIC_HOSTNAME`), `SMOKE_TOTP_CODE`, `SMOKE_INSECURE=1` (certs autofirmados).

---

## 12. Hardening del VPS (SPEC-04 §3)

```bash
sudo bash deploy/scripts/vps-hardening.sh
```

Idempotente: UFW (solo 22/80/443), `deny incoming`, Fail2ban sobre SSH y
endurecimiento de `sshd` (sin login de contraseña, sin root con contraseña).

> Ejecútalo **al final**, cuando ya entres por SSH con clave pública (el script
> desactiva `PasswordAuthentication`).

---

## 13. Operación

```bash
$COMPOSE ps                      # estado
$COMPOSE logs -f backend worker  # logs
$COMPOSE restart nginx           # tras renovar TLS
$COMPOSE pull && $COMPOSE build && $COMPOSE up -d   # actualizar
```

**Backups** (volúmenes nombrados):

```bash
docker run --rm -v deploy_neo4j_data:/d -v "$PWD/backups":/b alpine \
  tar czf /b/neo4j-$(date +%F).tgz -C /d .
# análogo para deploy_reports_data; y copia deploy/.env fuera del VPS.
```

---

## Decisiones y SPEC-GAP

### Neo4j: ¿`5-enterprise` o `5-community`? → **Enterprise**

- **Índice vectorial** (`entity_embedding_idx`): soportado en **ambas** ediciones
  desde Neo4j 5.11/5.13 (Community indexa `LIST<INTEGER|FLOAT>`). No es el
  factor decisivo.
- **Constraint de existencia de propiedades** (`REQUIRE n.prop IS NOT NULL`,
  usadas en `contracts/graph-constraints.cypher` §2): **solo Enterprise
  Edition**. Community falla/ignora esos constraints ⇒ el contrato sellado no se
  puede aplicar íntegro.
- Por eso se mantiene `neo4j:5-enterprise` y se añade
  **`NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`** (sin esta variable el contenedor
  Enterprise **no arranca**). La aceptación de licencia es responsabilidad del
  operador (Neo4j Enterprise License Agreement).
- **APOC** (`apoc.refactor.mergeNodes`, requerido por la Entity Resolution del
  contrato §7.1) **no** se instala por defecto: `NEO4J_PLUGINS` descarga plugins
  y Neo4j vive en `internal_intel_net` (sin salida a Internet). Si lo necesitas,
  pre-instala APOC en la imagen/volumen y habilítalo — ver SPEC-GAP-3.9.

### SPEC-GAP-3.7 — Worker sin salida a Internet

`worker` se despliega en `internal_intel_net` (aislada, sin egress) por
indicación de arquitectura (Regla 4). Si `LLM_PROVIDER` apunta a un proveedor
**externo** (p. ej. OpenAI), el worker no podrá llamarlo y los jobs quedarán
`FAILED`. Opciones: (a) adjuntar también `public_net` (como el backend), o (b)
usar un LLM local dentro de `internal_intel_net`. **Requiere decisión del
Orquestador**; hoy se implementa la red interna según lo indicado.

### SPEC-GAP-3.8 — Índice vectorial multi-label

Ver §7. `entity_embedding_idx` usa sintaxis multi-label soportada solo desde
Neo4j **2026.01+**. Con `neo4j:5-*` ese índice no se crea (el resto sí).

### SPEC-GAP-3.9 — APOC en red aislada

`NEO4J_PLUGINS` requiere egress; Neo4j está en red interna. Pre-instalar el
plugin o abrir egress puntual durante el aprovisionamiento.

### Nota — 2FA/TOTP

`openapi.yaml` declara `totp_code` en `LoginRequest` y SPEC-04 §2 lo exige para
`ADMIN`; el backend actual (`app/api/v1/auth.py`) **no** valida TOTP (zona
Agente 3, fuera del alcance de este paquete). El smoke test lo soporta vía
`SMOKE_TOTP_CODE` cuando se implemente.

### Nota — ruta del contrato dentro del contenedor

`app/schemas/extraction.py` y `app/db/repository.py` resuelven el contrato con
`Path(__file__).resolve().parents[4] / "contracts"`. Por eso el `Dockerfile` del
backend usa `WORKDIR /srv/osint/apps/backend` y el compose monta
`../contracts:/srv/osint/contracts:ro` (backend y worker). Con `WORKDIR /app`
el cálculo lanzaba `IndexError` en el contenedor.

---

## Troubleshooting

| Síntoma | Causa / solución |
| :--- | :--- |
| `required variable INFORMANT_API_KEY is missing` | Falta `deploy/.env` (o la variable). `cp deploy/.env.example deploy/.env`. |
| Neo4j reinicia en bucle | Falta `NEO4J_ACCEPT_LICENSE_AGREEMENT=yes`. |
| `apply-constraints.sh`: `[GAP] entity_embedding_idx` | Esperado en Neo4j 5.x (SPEC-GAP-3.8). |
| Smoke test `[FAIL] /auth/login 401` | No definiste `OSINT_USERS_JSON`/`OSINT_ADMIN_*` o recreaste sin `--force-recreate`. |
| Smoke test `[FAIL] /context/preflight 401` | `OSINT_API_KEYS_JSON` no coincide con `SMOKE_API_KEY`/`INFORMANT_API_KEY`. |
| `GET /graph/nodes 503` | Backend no alcanza Neo4j (aún arrancando o constraints no aplicados). |
| `502` en Nginx | Falta `deploy/nginx/certs/{fullchain,privkey}.pem` o el backend no está arriba. |
