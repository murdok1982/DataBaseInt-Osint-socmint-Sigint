/**
 * AuthContext (SPEC-05 §1 y §7): hidratación con la cookie de refresh, login,
 * logout y la tabla RBAC de la UI. La seguridad real vive en el backend; estas
 * pruebas garantizan que la UI oculta lo que el rol no puede usar.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { AuthProvider, RBAC_MATRIX, useAuth } from "./AuthContext";
import { getAccessToken, setAccessToken } from "@/services/api";

const TOKEN = { access_token: "access-1", token_type: "bearer", expires_in: 900 };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function wrapper({ children }: { children: ReactNode }) {
  return <AuthProvider>{children}</AuthProvider>;
}

/** Enruta las llamadas de fetch por ruta, sin depender del orden. */
function routeFetch(routes: Record<string, () => Response>): void {
  vi.mocked(fetch).mockImplementation(async (input) => {
    const url = String(input);
    const key = Object.keys(routes).find((route) => url.endsWith(route));
    if (!key) return json(404, { error: { code: "NOT_FOUND", message: url } });
    return routes[key]!();
  });
}

beforeEach(() => {
  setAccessToken(null);
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

describe("hidratación de la sesión", () => {
  it("queda anónimo si no hay cookie de refresh válida", async () => {
    routeFetch({ "/auth/refresh": () => json(401, { error: { code: "UNAUTHORIZED", message: "x" } }) });

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.status).toBe("anonymous"));
    expect(result.current.user).toBeNull();
    expect(result.current.can("graph:read")).toBe(false);
  });

  it("re-hidrata el usuario con la cookie de refresh", async () => {
    routeFetch({
      "/auth/refresh": () => json(200, TOKEN),
      "/auth/me": () => json(200, { id: "u-1", username: "ana", role: "ANALYST" }),
    });

    const { result } = renderHook(() => useAuth(), { wrapper });

    await waitFor(() => expect(result.current.status).toBe("authenticated"));
    expect(result.current.role).toBe("ANALYST");
    expect(getAccessToken()).toBe("access-1");
  });
});

describe("login y logout", () => {
  it("login guarda el access token y el perfil del operador", async () => {
    routeFetch({
      "/auth/refresh": () => json(401, { error: { code: "UNAUTHORIZED", message: "x" } }),
      "/auth/login": () => json(200, TOKEN),
      "/auth/me": () => json(200, { id: "a-1", username: "root", role: "ADMIN" }),
    });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe("anonymous"));

    await act(async () => {
      await result.current.login({ username: "root", password: "x", totp_code: "123456" } as never);
    });

    expect(result.current.status).toBe("authenticated");
    expect(result.current.can("graph:delete")).toBe(true);
    expect(getAccessToken()).toBe("access-1");
  });

  it("logout limpia la sesión aunque el servidor falle", async () => {
    routeFetch({
      "/auth/refresh": () => json(200, TOKEN),
      "/auth/me": () => json(200, { id: "u-1", username: "ana", role: "ANALYST" }),
      "/auth/logout": () => json(500, { error: { code: "INTERNAL_ERROR", message: "x" } }),
    });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe("authenticated"));

    await act(async () => {
      await result.current.logout().catch(() => undefined);
    });

    expect(result.current.status).toBe("anonymous");
    expect(getAccessToken()).toBeNull();
  });
});

describe("matriz RBAC de la UI", () => {
  it("ADMIN puede borrar nodos; ANALYST no", () => {
    expect(RBAC_MATRIX["graph:delete"]).toEqual(["ADMIN"]);
  });

  it("VIEWER solo lee: no ingesta, no resuelve alertas, no edita", () => {
    const viewerAllowed = (Object.keys(RBAC_MATRIX) as (keyof typeof RBAC_MATRIX)[]).filter(
      (action) => RBAC_MATRIX[action].includes("VIEWER"),
    );
    expect(viewerAllowed.sort()).toEqual(["analysis:read", "graph:read", "rag:query", "reports:read"]);
  });

  it("can() devuelve false para acciones fuera del rol del operador", async () => {
    routeFetch({
      "/auth/refresh": () => json(200, TOKEN),
      "/auth/me": () => json(200, { id: "v-1", username: "lector", role: "VIEWER" }),
    });
    const { result } = renderHook(() => useAuth(), { wrapper });
    await waitFor(() => expect(result.current.status).toBe("authenticated"));

    expect(result.current.can("graph:read")).toBe(true);
    expect(result.current.can("ingest:create")).toBe(false);
    expect(result.current.can("admin:manage")).toBe(false);
  });
});

describe("contrato del hook", () => {
  it("useAuth fuera del proveedor falla con un mensaje claro", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => renderHook(() => useAuth())).toThrow("useAuth() debe usarse dentro de <AuthProvider>.");
    spy.mockRestore();
  });
});
