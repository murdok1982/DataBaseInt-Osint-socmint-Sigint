/**
 * Cada método público de `api` debe llamar al método y ruta del contrato
 * `contracts/openapi.yaml` (SPEC-03 §6). Esta tabla es la red de seguridad
 * contra renombres o rutas mal escritas en el cliente.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "./api";

type Case = {
  operationId: string;
  call: () => Promise<unknown>;
  method: string;
  path: string;
};

const file = () => new File(["contenido"], "informe.txt", { type: "text/plain" });

const CASES: Case[] = [
  { operationId: "authLogin", call: () => api.authLogin({ username: "a", password: "b" } as never), method: "POST", path: "/auth/login" },
  { operationId: "authRefresh", call: () => api.authRefresh(), method: "POST", path: "/auth/refresh" },
  { operationId: "authLogout", call: () => api.authLogout(), method: "POST", path: "/auth/logout" },
  { operationId: "authMe", call: () => api.authMe(), method: "GET", path: "/auth/me" },
  { operationId: "listGraphNodes", call: () => api.listGraphNodes({ limit: 5 }), method: "GET", path: "/graph/nodes?limit=5" },
  { operationId: "getGraphNode", call: () => api.getGraphNode("n1"), method: "GET", path: "/graph/nodes/n1" },
  { operationId: "updateGraphNode", call: () => api.updateGraphNode("n1", {} as never), method: "PUT", path: "/graph/nodes/n1" },
  { operationId: "deleteGraphNode", call: () => api.deleteGraphNode("n1"), method: "DELETE", path: "/graph/nodes/n1" },
  { operationId: "lockGraphNode", call: () => api.lockGraphNode("n1"), method: "POST", path: "/graph/nodes/n1/lock" },
  { operationId: "listGraphEdges", call: () => api.listGraphEdges({}), method: "GET", path: "/graph/edges" },
  { operationId: "getGraphNeighborhood", call: () => api.getGraphNeighborhood("n1", {}), method: "GET", path: "/graph/neighborhood/n1" },
  { operationId: "postContextPreflight", call: () => api.postContextPreflight({} as never), method: "POST", path: "/context/preflight" },
  { operationId: "createIngestJob", call: () => api.createIngestJob(file(), "Título"), method: "POST", path: "/ingest" },
  { operationId: "createInformantIngestJob", call: () => api.createInformantIngestJob(file(), "{}", "tok"), method: "POST", path: "/ingest/informant" },
  { operationId: "getIngestJob", call: () => api.getIngestJob("j1"), method: "GET", path: "/ingest/j1" },
  { operationId: "getReport", call: () => api.getReport("r1"), method: "GET", path: "/reports/r1" },
  { operationId: "getReportFile", call: () => api.getReportFile("r1"), method: "GET", path: "/reports/r1/file" },
  { operationId: "getReportChunks", call: () => api.getReportChunks("r1"), method: "GET", path: "/reports/r1/chunks" },
  { operationId: "listAnalyses", call: () => api.listAnalyses({}), method: "GET", path: "/analyses" },
  { operationId: "createAnalysis", call: () => api.createAnalysis({} as never), method: "POST", path: "/analyses" },
  { operationId: "getAnalysis", call: () => api.getAnalysis("a1"), method: "GET", path: "/analyses/a1" },
  { operationId: "updateAnalysis", call: () => api.updateAnalysis("a1", {} as never), method: "PUT", path: "/analyses/a1" },
  { operationId: "createIntelRequirement", call: () => api.createIntelRequirement("a1", {} as never), method: "POST", path: "/analyses/a1/requirements" },
  { operationId: "listAnalysisRequirements", call: () => api.listAnalysisRequirements("a1"), method: "GET", path: "/analyses/a1/requirements" },
  { operationId: "listCoherenceAlerts", call: () => api.listCoherenceAlerts({}), method: "GET", path: "/coherence/alerts" },
  { operationId: "resolveCoherenceAlert", call: () => api.resolveCoherenceAlert("c1", {} as never), method: "PATCH", path: "/coherence/alerts/c1/resolve" },
  { operationId: "searchRag", call: () => api.searchRag({ query: "x" } as never), method: "POST", path: "/search/rag" },
  { operationId: "getInfo", call: () => api.getInfo(), method: "GET", path: "/info" },
];

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify({}), { status: 200, headers: { "Content-Type": "application/json" } })),
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("contrato de rutas del cliente (openapi.yaml)", () => {
  it.each(CASES)("$operationId → $method /api/v1$path", async ({ call, method, path }) => {
    await call();

    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe(method);
    expect(url).toBe(`/api/v1${path}`);
  });

  it("cubre las 29 operaciones del contrato (28 HTTP + ingestProgressWs por WebSocket)", () => {
    const operations = new Set([...CASES.map((c) => c.operationId), "ingestProgressWs"]);
    expect(operations.size).toBe(29);
  });
});

describe("ingestProgressWs", () => {
  it("abre el WebSocket de progreso en el mismo origen", () => {
    const sockets: string[] = [];
    vi.stubGlobal(
      "WebSocket",
      class {
        constructor(url: string) {
          sockets.push(url);
        }
      },
    );

    api.ingestProgressWs("j-7");

    expect(sockets).toHaveLength(1);
    expect(sockets[0]).toMatch(/^wss?:\/\/.+\/api\/v1\/ingest\/j-7\/ws$/);
  });
});
