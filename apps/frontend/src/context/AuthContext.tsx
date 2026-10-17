"use client";

/**
 * AuthContext — estado JWT + 2FA + rol del operador y helper `can(action)`.
 *
 * Autorizado por: SPEC-05 §1 (`Auth/sesión + 2FA`) y §7, y SPEC-04 §1 (matriz
 * RBAC de 4 actores; `ANALYST` sustituye a `OPERATOR`) y SPEC-04 §2 (JWT 30 min
 * + refresh cookie HttpOnly/Secure; TOTP para ADMIN, opcional para ANALYST).
 *
 * IMPORTANTE: la UI sólo OCULTA acciones; la seguridad real vive en el backend
 * (cada "No" de la matriz devuelve 403, SPEC-04 §4). `can()` es ergonomía de UI,
 * nunca un control de acceso.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { ReactNode } from "react";

import { api, setAccessToken, setRefreshHandler } from "@/services/api";
import type { LoginRequest, Role, User } from "@/services/api";

/* ------------------------------------------------------------------ *
 * Matriz RBAC replicada de SPEC-04 §1 (4 actores)                     *
 * ------------------------------------------------------------------ *
 * | Capacidad / Endpoint                              | ADMIN | ANALYST | VIEWER |
 * | Explorar grafo y abrir informes originales        |  Sí   |   Sí    |  Sí    |
 * | Consultas GraphRAG (POST /search/rag)             |  Sí   |   Sí    |  Sí    |
 * | Subir informes OSINT (POST /ingest)               |  Sí   |   Sí    |  No    |
 * | Crear/editar Analysis e IntelRequirement          |  Sí   |   Sí    |  No    |
 * | Bloquear nodos (POST /graph/nodes/{id}/lock)      |  Sí   |   Sí    |  No    |
 * | Editar/fusionar nodos (PUT /graph/nodes/{id})     |  Sí   |   Sí    |  No    |
 * | Resolver alertas de coherencia                    |  Sí   |   Sí    |  No    |
 * | Borrar nodos (DELETE /graph/nodes/{id})           |  Sí   |   No    |  No    |
 * | Gestionar operadores, API Keys, backups           |  Sí   |   No    |  No    |
 *
 * `AI_INFORMANT` no es un `User`: es un principal M2M (API Key) y no accede a
 * este dashboard humano (SPEC-04 §1).
 */
export type RbacAction =
  | "graph:read" // GET /graph/nodes, /graph/nodes/{id}, /graph/edges, /graph/neighborhood/{id}
  | "reports:read" // GET /reports/{id}, /reports/{id}/file, /reports/{id}/chunks
  | "rag:query" // POST /search/rag
  | "ingest:create" // POST /ingest
  | "analysis:read" // GET /analyses/{id}
  | "analysis:write" // POST /analyses, PUT /analyses/{id}, POST /analyses/{id}/requirements
  | "coherence:read" // GET /coherence/alerts
  | "coherence:resolve" // PATCH /coherence/alerts/{alert_id}/resolve
  | "graph:update" // PUT /graph/nodes/{id}
  | "graph:lock" // POST /graph/nodes/{id}/lock
  | "graph:delete" // DELETE /graph/nodes/{id}
  | "admin:manage"; // operadores, API Keys, backups (fuera del contrato; reservado a ADMIN)

const ALL_ROLES: readonly Role[] = ["ADMIN", "ANALYST", "VIEWER"];
const ANALYST_PLUS: readonly Role[] = ["ADMIN", "ANALYST"];
const ADMIN_ONLY: readonly Role[] = ["ADMIN"];

/** Tabla única de verdad de la UI para ocultar acciones. */
export const RBAC_MATRIX: Readonly<Record<RbacAction, readonly Role[]>> = {
  "graph:read": ALL_ROLES,
  "reports:read": ALL_ROLES,
  "rag:query": ALL_ROLES,
  "ingest:create": ANALYST_PLUS,
  "analysis:read": ALL_ROLES,
  "analysis:write": ANALYST_PLUS,
  "coherence:read": ANALYST_PLUS,
  "coherence:resolve": ANALYST_PLUS,
  "graph:update": ANALYST_PLUS,
  "graph:lock": ANALYST_PLUS,
  "graph:delete": ADMIN_ONLY,
  "admin:manage": ADMIN_ONLY,
};

export type AuthStatus = "loading" | "authenticated" | "anonymous";

export interface AuthContextValue {
  user: User | null;
  role: Role | null;
  status: AuthStatus;
  /** Login interactivo; `totp_code` sólo si el operador tiene 2FA activo. */
  login: (credentials: LoginRequest) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<boolean>;
  /** ¿El rol actual puede ejecutar la acción? (oculta UI; el backend manda). */
  can: (action: RbacAction) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/** Margen de seguridad antes de expirar el access token (SPEC-04 §2: 30 min). */
const REFRESH_MARGIN_MS = 60_000;

export function AuthProvider({ children }: { children: ReactNode }): JSX.Element {
  const [user, setUser] = useState<User | null>(null);
  const [status, setStatus] = useState<AuthStatus>("loading");
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearSession = useCallback((): void => {
    if (refreshTimer.current) {
      clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    }
    setAccessToken(null);
    setUser(null);
    setStatus("anonymous");
  }, []);

  const scheduleRefresh = useCallback(
    (expiresInSeconds: number): void => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
      const delay = Math.max(expiresInSeconds * 1000 - REFRESH_MARGIN_MS, 5_000);
      refreshTimer.current = setTimeout(() => {
        api
          .authRefresh()
          .then((token) => {
            setAccessToken(token.access_token);
            scheduleRefresh(token.expires_in);
          })
          .catch(() => clearSession());
      }, delay);
    },
    [clearSession],
  );

  const refresh = useCallback(async (): Promise<boolean> => {
    try {
      const token = await api.authRefresh();
      setAccessToken(token.access_token);
      scheduleRefresh(token.expires_in);
      return true;
    } catch {
      clearSession();
      return false;
    }
  }, [clearSession, scheduleRefresh]);

  // Reintento automático ante 401 en cualquier llamada del cliente API.
  useEffect(() => {
    setRefreshHandler(async () => {
      try {
        const token = await api.authRefresh();
        setAccessToken(token.access_token);
        return token.access_token;
      } catch {
        clearSession();
        return null;
      }
    });
    return () => setRefreshHandler(null);
  }, [clearSession]);

  // Bootstrap: al recargar, la cookie refresh HttpOnly permite re-hidratar sesión.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = await api.authRefresh();
        if (cancelled) return;
        setAccessToken(token.access_token);
        const me = await api.authMe();
        if (cancelled) return;
        setUser(me);
        setStatus("authenticated");
        scheduleRefresh(token.expires_in);
      } catch {
        if (!cancelled) clearSession();
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [clearSession, scheduleRefresh]);

  const login = useCallback(
    async (credentials: LoginRequest): Promise<void> => {
      const token = await api.authLogin(credentials);
      setAccessToken(token.access_token);
      const me = await api.authMe();
      setUser(me);
      setStatus("authenticated");
      scheduleRefresh(token.expires_in);
    },
    [scheduleRefresh],
  );

  const logout = useCallback(async (): Promise<void> => {
    try {
      await api.authLogout();
    } finally {
      clearSession();
    }
  }, [clearSession]);

  const can = useCallback(
    (action: RbacAction): boolean => {
      const currentRole = user?.role;
      if (!currentRole) return false;
      return RBAC_MATRIX[action].includes(currentRole);
    },
    [user],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      role: user?.role ?? null,
      status,
      login,
      logout,
      refresh,
      can,
    }),
    [user, status, login, logout, refresh, can],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth() debe usarse dentro de <AuthProvider>.");
  }
  return context;
}
