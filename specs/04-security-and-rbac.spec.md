# SPEC-04: Seguridad, RBAC (Humano + M2M) y VPS (v2.2)

> Spec rectora del **Agente 3 (DevOps/Sec)**. Contrato derivado:
> `/contracts/openapi.yaml` (seguridad) y `/deploy/**`.

## 1. Matriz de Roles (4 actores)

| Capacidad / Endpoint | ADMIN | ANALYST (humano) | AI_INFORMANT (M2M) | VIEWER |
| :--- | :---: | :---: | :---: | :---: |
| Login interactivo (usuario/contraseña + JWT) | Sí | Sí | **No** (usa API Key M2M) | Sí |
| Explorar grafo y abrir informes originales | Sí | Sí | Solo lectura vía Pre-Flight | Sí |
| Crear/editar `Analysis` e `IntelRequirement` | Sí | Sí | No | No |
| Bloquear nodos verificados (`locked_by_analyst`) | Sí | Sí | No | No |
| Subir informes OSINT y extraer relaciones | Sí | Sí | **Sí** (con referencias obligatorias) | No |
| Resolver alertas de coherencia (`CoherenceAlert`) | Sí | Sí | No | No |
| Gestionar operadores, API Keys y backups | Sí | No | No | No |

**Reglas:**
- Rol `ANALYST` sustituye al antiguo `OPERATOR` (migración de datos: `OPERATOR → ANALYST`).
- `AI_INFORMANT` **no** es un `User`: es un principal M2M con permisos acotados
  (solo `preflight` de lectura y `ingest/informant`).
- Toda capacidad marcada "No" devuelve `403`.

## 2. Requisitos de Red y Cifrado
- Contraseñas hasheadas exclusivamente con **Argon2id**.
- Access token JWT de **15 min** (`expires_in = 900`) + Refresh Token de **7 días**
  en cookie `HttpOnly`/`Secure`/`SameSite=Strict`. *(v2.2: antes 30 min; se alinea
  con la política de seguridad del equipo, máx. 15 min.)* La configuración
  rechaza valores superiores (15 min / 7 días) al arrancar.
- **2FA (TOTP, RFC 6238)** — SHA-1, periodo 30 s, 6 dígitos, tolerancia ±1 periodo:
  - **Obligatorio para `ADMIN`**: un `ADMIN` sin `totp_secret` configurado **no
    puede iniciar sesión** (`401`). Solo un entorno de desarrollo puede relajarlo
    de forma explícita (`ADMIN_TOTP_REQUIRED=false`, prohibido en producción).
  - Opcional/recomendado para `ANALYST` y `VIEWER`; si el operador tiene
    `totp_secret`, el código pasa a ser obligatorio para él.
  - Un código TOTP ya usado no se acepta de nuevo (anti-repetición).
  - El fallo de 2FA responde `401` con el mismo mensaje genérico que una
    contraseña errónea (no revela qué factor falló).
- API Keys M2M: almacenadas **hasheadas** (Argon2id) en el nodo `ApiKey`; se
  presentan en `X-API-Key`; nunca se registran en claro.
- Docker Compose con dos redes: `public_net` (Nginx + Frontend) e
  `internal_intel_net` (Backend, Redis, Neo4j, Informant Agent). `internal_intel_net`
  es `internal: true` (sin salida a Internet). Solo **backend** y **worker** son
  *dual-homed* (`public_net` + `internal_intel_net`): el backend para recibir de
  Nginx y el worker para el egreso al proveedor de inferencia configurado
  (único destino externo autorizado). Neo4j y Redis viven **solo** en la red interna.
- **TLS 1.3** terminado en Nginx; HSTS.

### 2.1 Protección contra fuerza bruta (v2.2)
- **Aplicación:** tras **5 intentos fallidos** de login en una ventana de
  **15 min** para la misma pareja (IP de cliente, usuario), `POST /auth/login`
  responde `429 TOO_MANY_REQUESTS` con cabecera `Retry-After` (segundos) hasta
  que expire la ventana, **incluso si la contraseña es correcta**. Un login
  correcto antes del bloqueo reinicia el contador.
- **Perímetro:** Nginx limita `/api/v1/auth/login` a **10 peticiones/min por IP**
  (`limit_req`), defensa en profundidad independiente del número de workers.
- La IP de cliente se toma de `X-Real-IP` solo cuando la petición llega desde
  el proxy de confianza (Nginx en la red interna); si no, de la conexión TCP.

## 3. Hardening del VPS
- Firewall UFW: solo 22 (SSH restringido), 80 y 443; el resto cerrado.
- Fail2ban activo sobre SSH.
- Nginx como único punto de entrada TLS.
- Neo4j/Redis **sin** `ports:` publicados en `docker-compose.prod.yml`
  (7474/7687 **jamás** expuestos al host público).

## 4. Criterios de Aceptación
- [ ] Las 28 celdas de la matriz se cumplen: cada "No" devuelve `403`.
- [ ] No existe ningún `ports:` para Neo4j/Redis en producción.
- [ ] JWT de 15 min verificado; refresh solo por cookie `HttpOnly`/`Secure`; la
      configuración rechaza expiraciones superiores.
- [ ] Un `ADMIN` sin TOTP, o con TOTP inválido/reutilizado, recibe `401`.
- [ ] El 6.º intento fallido de login en 15 min devuelve `429` con `Retry-After`.
- [ ] API Key M2M verificada con hash; `AI_INFORMANT` no puede llamar a `/analyses/*` ni resolver alertas.
- [ ] `init-admin-user.py` no usa credenciales por defecto.
- [ ] `vps-hardening.sh` es idempotente.

## 5. Nodos de Plataforma (fuera de la ontología OSINT)

La autenticación no pertenece a SPEC-01, así que se modela como **nodos de
plataforma** separados de las entidades de inteligencia:

### 5.1 `User` (operador humano)
- Propiedades: `id` (UUID), `username` (único), `password_hash` (**Argon2id**),
  `role` (`ADMIN|ANALYST|VIEWER`), `totp_secret: String?`, `created_at`, `disabled`.
- Constraints: `user_id_unique`, `user_username_unique`.
- Dueño: Agente 1 (vive en el grafo). Fallback hasta su implementación: `EnvUserStore`
  (**sin credenciales por defecto**).

### 5.2 `ApiKey` (principal M2M)
- Propiedades: `id` (UUID), `client_name: String`, `key_hash` (**Argon2id**),
  `role: "AI_INFORMANT"`, `created_at`, `revoked: Boolean`.
- Constraint: `apikey_id_unique`, `apikey_key_hash_unique`.
- Gestión exclusiva de `ADMIN` (`deploy/scripts/init-api-keys.py`).

### Criterios de aceptación (plataforma)
- [ ] `username` y `key_hash` únicos; no existe ningún usuario ni API Key por defecto.
- [ ] El hash se verifica con Argon2id; nunca se guarda en claro.
- [ ] `internal_intel_net` aislada: sin `ports:` hacia el host.
