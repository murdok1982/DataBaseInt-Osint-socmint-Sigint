# SPEC-04: Seguridad, RBAC (Humano + M2M) y VPS (v2.0)

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
- Access token JWT (**30 min**) + Refresh Token en cookie `HttpOnly`/`Secure`.
- **2FA (TOTP)** obligatorio para `ADMIN` y opcional/recomendado para `ANALYST`.
- API Keys M2M: almacenadas **hasheadas** (Argon2id) en el nodo `ApiKey`; se
  presentan en `X-API-Key`; nunca se registran en claro.
- Docker Compose con dos redes: `public_net` (Nginx + Frontend) e
  `internal_intel_net` (Backend, Redis, Neo4j, Informant Agent).
- **TLS 1.3** terminado en Nginx; HSTS.

## 3. Hardening del VPS
- Firewall UFW: solo 22 (SSH restringido), 80 y 443; el resto cerrado.
- Fail2ban activo sobre SSH.
- Nginx como único punto de entrada TLS.
- Neo4j/Redis **sin** `ports:` publicados en `docker-compose.prod.yml`
  (7474/7687 **jamás** expuestos al host público).

## 4. Criterios de Aceptación
- [ ] Las 28 celdas de la matriz se cumplen: cada "No" devuelve `403`.
- [ ] No existe ningún `ports:` para Neo4j/Redis en producción.
- [ ] JWT de 30 min verificado; refresh solo por cookie `HttpOnly`/`Secure`.
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
