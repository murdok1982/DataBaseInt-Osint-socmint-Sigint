/**
 * Cliente HTTP del dashboard (SPEC-03 §1 y SPEC-04 §2): cabeceras, errores del
 * contrato y renovación de sesión 401 → refresh → reintento.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, setAccessToken, setRefreshHandler } from "./api";

const USER = { id: "u-1", username: "ana", role: "ANALYST" };

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requestHeaders(call: number): Record<string, string> {
  const fetchMock = vi.mocked(fetch);
  const init = fetchMock.mock.calls[call]?.[1] as RequestInit | undefined;
  return (init?.headers ?? {}) as Record<string, string>;
}

beforeEach(() => {
  setAccessToken(null);
  setRefreshHandler(null);
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("cabeceras de autenticación", () => {
  it("adjunta el Bearer del access token en memoria", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(200, USER));
    setAccessToken("access-123");

    await api.authMe();

    expect(requestHeaders(0).Authorization).toBe("Bearer access-123");
    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(init.credentials).toBe("include");
  });

  it("no adjunta Bearer en los endpoints públicos de sesión", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(200, { access_token: "x", expires_in: 900 }));
    setAccessToken("access-123");

    await api.authLogin({ username: "ana", password: "secreto" } as never);

    expect(requestHeaders(0).Authorization).toBeUndefined();
  });

  it("no fija Content-Type en cuerpos FormData (lo añade el navegador)", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(202, { job_id: "j-1", status: "QUEUED" }));

    await api.createIngestJob(new File(["texto"], "informe.txt", { type: "text/plain" }), "Informe");

    const init = vi.mocked(fetch).mock.calls[0]?.[1] as RequestInit;
    expect(init.body).toBeInstanceOf(FormData);
    expect(requestHeaders(0)["Content-Type"]).toBeUndefined();
  });
});

describe("errores del contrato", () => {
  it("traduce el sobre { error: { code, message } } a ApiError", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      json(404, { error: { code: "NOT_FOUND", message: "Nodo inexistente" } }),
    );

    const failure = await api.getGraphNode("n-404").catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 404, code: "NOT_FOUND", message: "Nodo inexistente" });
  });

  it("conserva el código HTTP cuando la respuesta no tiene JSON", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      new Response("<html>502</html>", { status: 502, statusText: "Bad Gateway" }),
    );

    const failure = await api.getInfo().catch((error: unknown) => error);

    expect(failure).toMatchObject({ status: 502, code: "HTTP_502", message: "Bad Gateway" });
  });

  it("propaga el 409 CONFLICT de un nodo bloqueado sin reintentar", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      json(409, { error: { code: "CONFLICT", message: "Nodo bloqueado por el analista" } }),
    );

    await expect(api.deleteGraphNode("n-1")).rejects.toMatchObject({
      status: 409,
      code: "CONFLICT",
    });
    expect(fetch).toHaveBeenCalledTimes(1);
  });
});

describe("renovación de sesión (SPEC-04 §2)", () => {
  it("renueva tras un 401 y reintenta una vez con el token nuevo", async () => {
    setAccessToken("caducado");
    setRefreshHandler(async () => {
      setAccessToken("renovado");
      return "renovado";
    });
    vi.mocked(fetch)
      .mockResolvedValueOnce(json(401, { error: { code: "UNAUTHORIZED", message: "expirado" } }))
      .mockResolvedValueOnce(json(200, USER));

    await expect(api.authMe()).resolves.toEqual(USER);

    expect(fetch).toHaveBeenCalledTimes(2);
    expect(requestHeaders(1).Authorization).toBe("Bearer renovado");
  });

  it("falla con 401 si la renovación no devuelve token", async () => {
    setAccessToken("caducado");
    setRefreshHandler(async () => null);
    vi.mocked(fetch).mockResolvedValue(
      json(401, { error: { code: "UNAUTHORIZED", message: "expirado" } }),
    );

    await expect(api.authMe()).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("no intenta renovar desde los endpoints públicos (evita bucles)", async () => {
    const refresh = vi.fn(async () => "nuevo");
    setRefreshHandler(refresh);
    vi.mocked(fetch).mockResolvedValueOnce(
      json(401, { error: { code: "UNAUTHORIZED", message: "sin sesión" } }),
    );

    await expect(api.authRefresh()).rejects.toMatchObject({ status: 401 });
    expect(refresh).not.toHaveBeenCalled();
  });
});

describe("construcción de la URL", () => {
  it("omite los parámetros vacíos y conserva los válidos", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(200, { items: [], next_cursor: null }));

    await api.listGraphNodes({ label: undefined, q: "", limit: 10 } as never);

    const url = String(vi.mocked(fetch).mock.calls[0]?.[0]);
    expect(url).toContain("/api/v1/graph/nodes?limit=10");
    expect(url).not.toContain("label=");
    expect(url).not.toContain("q=");
  });

  it("codifica el identificador de la ruta", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(200, USER));

    await api.getGraphNode("a/b c").catch(() => undefined);

    expect(String(vi.mocked(fetch).mock.calls[0]?.[0])).toContain("/graph/nodes/a%2Fb%20c");
  });
});
