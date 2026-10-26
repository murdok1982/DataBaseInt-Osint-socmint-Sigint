/**
 * Cliente API tipado — OSINT Nexus (v2.1.0).
 *
 * Autorizado por: SPEC-05 §1 (`Cliente API` → tipos derivados de OpenAPI) y
 * SPEC-03 §1/§6 (convenciones: base `/api/v1`, JSON, `Bearer` JWT, API Key M2M,
 * errores `{ error: { code, message } }`).
 *
 * ⚠️  Este cliente expone UNO POR UNO los **38 `operationId`** de
 *     `contracts/openapi.yaml` (v2.1.0, SELLADO). No inventa endpoints ni
 *     campos: si un campo falta, se eleva `SPEC-GAP` (ver README §6).
 *
 *     Los tokens NO viven aquí: el `AuthContext` inyecta el access token con
 *     `setAccessToken()` y registra `setRefreshHandler()` para renovarlo ante un
 *     401 (el refresh viaja en cookie HttpOnly `Secure`, SPEC-04 §2, por eso
 *     todas las peticiones se hacen con `credentials: "include"`).
 */

import type { components, operations } from "@/types/api";

type Schemas = components["schemas"];

/* ------------------------------------------------------------------ *
 * Tipos re-exportados (todos derivados del contrato, ninguno a mano)  *
 * ------------------------------------------------------------------ */
export type EntityLabel = Schemas["EntityLabel"];
export type GraphNode = Schemas["GraphNode"];
export type GraphNodeUpdate = Schemas["GraphNodeUpdate"];
export type GraphEdge = Schemas["GraphEdge"];
export type Subgraph = Schemas["Subgraph"];
export type Report = Schemas["Report"];
export type Chunk = Schemas["Chunk"];
export type IngestJob = Schemas["IngestJob"];
export type PreflightRequest = Schemas["PreflightRequest"];
export type PreflightResponse = Schemas["PreflightResponse"];
export type Analysis = Schemas["Analysis"];
export type AnalysisCreate = Schemas["AnalysisCreate"];
export type AnalysisUpdate = Schemas["AnalysisUpdate"];
export type IntelRequirement = Schemas["IntelRequirement"];
export type IntelRequirementCreate = Schemas["IntelRequirementCreate"];
export type CoherenceAlert = Schemas["CoherenceAlert"];
export type CoherenceAlertResolve = Schemas["CoherenceAlertResolve"];
export type RagResponse = Schemas["RagResponse"];
export type RagCitation = RagResponse["citations"][number];
export type LoginRequest = Schemas["LoginRequest"];
export type TokenResponse = Schemas["TokenResponse"];
export type User = Schemas["User"];
export type Role = NonNullable<User["role"]>;
export type ApiErrorBody = Schemas["Error"];
export type GraphEdgeType = GraphEdge["type"];

/* v3.1 — multi-INT, evidencia y aprobación (SPEC-01..04). */
export type GeoPoint = Schemas["GeoPoint"];
export type ColocationRequest = Schemas["ColocationRequest"];
export type ColocationResponse = Schemas["ColocationResponse"];
export type ColocationCluster = Schemas["ColocationCluster"];
export type ColocationEvent = Schemas["ColocationEvent"];
export type MultiLayerSubgraphRequest = Schemas["SubgraphRequest"];
export type MultiLayerSubgraphResponse = Schemas["SubgraphResponse"];
export type AliasEvasionRequest = Schemas["AliasEvasionRequest"];
export type AliasEvasionResponse = Schemas["AliasEvasionResponse"];
export type AliasSignal = Schemas["AliasSignal"];
export type Proposal = Schemas["Proposal"];
export type ProposalList = Schemas["ProposalList"];
export type ProposalDecision = Schemas["ProposalDecision"];
export type ProposalStatus = NonNullable<Proposal["status"]>;
export type Evidence = Schemas["Evidence"];
export type EvidenceVerification = Schemas["EvidenceVerification"];

/* Enums derivados (para listas de UI y validaciones). */
export const ENTITY_LABEL_VALUES = [
  "Person",
  "Organization",
  "CyberAsset",
  "Location",
  "Event",
  "Report",
  "Analysis",
  "IntelRequirement",
  "CoherenceAlert",
] as const satisfies readonly EntityLabel[];

export const ROLE_VALUES = ["ADMIN", "ANALYST", "VIEWER"] as const satisfies readonly Role[];

export const ANALYSIS_STATUS_VALUES = ["ACTIVE", "UNDER_REVIEW", "CLOSED"] as const;
export const PRIORITY_VALUES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;
export const COHERENCE_ALERT_TYPE_VALUES = [
  "CONTRADICTION",
  "HIGH_VALUE_CORROBORATION",
  "NEW_ALIAS_MERGE",
] as const;
export const COHERENCE_STATUS_VALUES = ["PENDING_ANALYST", "RESOLVED", "DISMISSED"] as const;
export const REQUIREMENT_STATUS_VALUES = ["OPEN", "ANSWERED"] as const;

export type AnalysisStatus = NonNullable<Analysis["status"]>;
export type Priority = NonNullable<IntelRequirement["priority"]>;
export type RequirementStatus = NonNullable<IntelRequirement["status"]>;
export type CoherenceAlertType = NonNullable<CoherenceAlert["alert_type"]>;
export type CoherenceStatus = NonNullable<CoherenceAlert["status"]>;

/* Payloads/respuestas derivados de `operations[...]` (codegen-safe) */
export type ListGraphNodesQuery = NonNullable<operations["listGraphNodes"]["parameters"]["query"]>;
export type ListGraphEdgesQuery = NonNullable<operations["listGraphEdges"]["parameters"]["query"]>;
export type NeighborhoodQuery = NonNullable<operations["getGraphNeighborhood"]["parameters"]["query"]>;
export type ListAnalysesQuery = NonNullable<operations["listAnalyses"]["parameters"]["query"]>;
export type ListCoherenceAlertsQuery = NonNullable<
  operations["listCoherenceAlerts"]["parameters"]["query"]
>;
export type RagQuery = NonNullable<operations["searchRag"]["requestBody"]>["content"]["application/json"];
export type ListProposalsQuery = NonNullable<
  operations["listProposals"]["parameters"]["query"]
>;
export type LockGraphNodeBody = NonNullable<
  NonNullable<operations["lockGraphNode"]["requestBody"]>["content"]
>["application/json"];
export type GraphNodeList = NonNullable<
  operations["listGraphNodes"]["responses"][200]["content"]["application/json"]
>;
export type GraphEdgeList = NonNullable<
  operations["listGraphEdges"]["responses"][200]["content"]["application/json"]
>;
export type ChunkList = NonNullable<
  operations["getReportChunks"]["responses"][200]["content"]["application/json"]
>;
export type CoherenceAlertList = NonNullable<
  operations["listCoherenceAlerts"]["responses"][200]["content"]["application/json"]
>;
export type AnalysisList = NonNullable<
  operations["listAnalyses"]["responses"][200]["content"]["application/json"]
>;
export type IntelRequirementList = NonNullable<
  operations["listAnalysisRequirements"]["responses"][200]["content"]["application/json"]
>;
export type ServiceInfo = NonNullable<
  operations["getInfo"]["responses"][200]["content"]["application/json"]
>;

/* ------------------------------------------------------------------ *
 * Configuración                                                       *
 * ------------------------------------------------------------------ */
const API_BASE: string = process.env.NEXT_PUBLIC_API_BASE_URL ?? "/api/v1";

let accessToken: string | null = null;
let refreshHandler: (() => Promise<string | null>) | null = null;

/** Inyecta/limpia el access token en memoria (lo gestiona `AuthContext`). */
export function setAccessToken(token: string | null): void {
  accessToken = token;
}

export function getAccessToken(): string | null {
  return accessToken;
}

/** Registra el callback de renovación (401 → refresh → reintento). */
export function setRefreshHandler(handler: (() => Promise<string | null>) | null): void {
  refreshHandler = handler;
}

/* ------------------------------------------------------------------ *
 * Error tipado (formato de error de SPEC-03 §1)                        *
 * ------------------------------------------------------------------ */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

/* ------------------------------------------------------------------ *
 * Núcleo HTTP                                                         *
 * ------------------------------------------------------------------ */
type QueryValue = string | number | boolean | undefined;

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  query?: Record<string, QueryValue>;
  body?: unknown;
  /** Cabeceras adicionales (p. ej. `X-Preflight-Token`). */
  headers?: Record<string, string>;
  /** `false` para endpoints públicos/renovación (no adjunta Bearer ni reintenta). */
  auth?: boolean;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const base = `${API_BASE}${path}`;
  if (!query) return base;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

async function send(path: string, options: RequestOptions): Promise<Response> {
  const headers: Record<string, string> = { ...(options.headers ?? {}) };
  let payload: BodyInit | undefined;

  if (options.body !== undefined) {
    if (options.body instanceof FormData) {
      payload = options.body;
      // NO fijar Content-Type: el navegador añade el boundary del multipart.
    } else {
      headers["Content-Type"] = "application/json";
      payload = JSON.stringify(options.body);
    }
  }

  if (options.auth !== false && accessToken) {
    headers["Authorization"] = `Bearer ${accessToken}`;
  }

  return fetch(buildUrl(path, options.query), {
    method: options.method ?? "GET",
    headers,
    body: payload,
    credentials: "include", // cookie refresh HttpOnly (SPEC-04 §2)
    signal: options.signal,
  });
}

async function toApiError(response: Response): Promise<ApiError> {
  let code = `HTTP_${response.status}`;
  let message = response.statusText || "Error de red";
  try {
    const data = (await response.json()) as ApiErrorBody;
    if (data.error) {
      code = data.error.code ?? code;
      message = data.error.message ?? message;
    }
  } catch {
    // respuesta sin cuerpo JSON: se conserva el mensaje por defecto
  }
  return new ApiError(response.status, code, message);
}

async function withRefresh(path: string, options: RequestOptions): Promise<Response> {
  let response = await send(path, options);
  if (response.status === 401 && options.auth !== false && refreshHandler) {
    const token = await refreshHandler();
    if (token) {
      response = await send(path, options);
    }
  }
  return response;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await withRefresh(path, options);
  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function requestBlob(path: string, options: RequestOptions = {}): Promise<Blob> {
  const response = await withRefresh(path, options);
  if (!response.ok) throw await toApiError(response);
  return response.blob();
}

/* ------------------------------------------------------------------ *
 * WebSocket (ingesta en vivo)                                         *
 * ------------------------------------------------------------------ *
 * Los WebSocket del navegador NO permiten cabecera `Authorization`, así que
 * el handshake se autentica con la cookie de sesión en mismo origen
 * (`credentials: include`). Ver SPEC-GAP-1 en el README.                 */
function websocketUrl(path: string): string {
  if (/^https?:\/\//.test(API_BASE)) {
    return `${API_BASE.replace(/^http/, "ws")}${path}`;
  }
  if (typeof window !== "undefined") {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    return `${protocol}//${window.location.host}${API_BASE}${path}`;
  }
  return `${API_BASE}${path}`;
}

/* ------------------------------------------------------------------ *
 * API pública - 38 operationId                                        *
 * ------------------------------------------------------------------ */
export const api = {
  /* ── auth ───────────────────────────────────────────────────────── */
  /** `authLogin` — POST /auth/login (público; 2FA opcional vía `totp_code`) */
  authLogin: (body: LoginRequest): Promise<TokenResponse> =>
    request<TokenResponse>("/auth/login", { method: "POST", body, auth: false }),

  /** `authRefresh` — POST /auth/refresh (cookie refresh HttpOnly) */
  authRefresh: (): Promise<TokenResponse> =>
    request<TokenResponse>("/auth/refresh", { method: "POST", auth: false }),

  /** `authLogout` — POST /auth/logout (invalida el refresh token) */
  authLogout: (): Promise<void> => request<void>("/auth/logout", { method: "POST", auth: false }),

  /** `authMe` — GET /auth/me (perfil y rol del operador) */
  authMe: (): Promise<User> => request<User>("/auth/me"),

  /* ── graph ──────────────────────────────────────────────────────── */
  /** `listGraphNodes` — GET /graph/nodes (VIEWER+) */
  listGraphNodes: (query: ListGraphNodesQuery = {}): Promise<GraphNodeList> =>
    request<GraphNodeList>("/graph/nodes", { query }),

  /** `getGraphNode` — GET /graph/nodes/{id} (VIEWER+) */
  getGraphNode: (id: string): Promise<GraphNode> =>
    request<GraphNode>(`/graph/nodes/${encodeURIComponent(id)}`),

  /** `updateGraphNode` — PUT /graph/nodes/{id} (ANALYST+; 409 si bloqueado) */
  updateGraphNode: (id: string, body: GraphNodeUpdate): Promise<GraphNode> =>
    request<GraphNode>(`/graph/nodes/${encodeURIComponent(id)}`, { method: "PUT", body }),

  /** `deleteGraphNode` — DELETE /graph/nodes/{id} (ADMIN) */
  deleteGraphNode: (id: string): Promise<void> =>
    request<void>(`/graph/nodes/${encodeURIComponent(id)}`, { method: "DELETE" }),

  /** `lockGraphNode` — POST /graph/nodes/{id}/lock (ANALYST+; `locked` por defecto true) */
  lockGraphNode: (id: string, body: LockGraphNodeBody = { locked: true }): Promise<GraphNode> =>
    request<GraphNode>(`/graph/nodes/${encodeURIComponent(id)}/lock`, { method: "POST", body }),

  /** `listGraphEdges` — GET /graph/edges (VIEWER+) */
  listGraphEdges: (query: ListGraphEdgesQuery = {}): Promise<GraphEdgeList> =>
    request<GraphEdgeList>("/graph/edges", { query }),

  /** `getGraphNeighborhood` — GET /graph/neighborhood/{id}?depth= (VIEWER+, máx 3) */
  getGraphNeighborhood: (id: string, query: NeighborhoodQuery = {}): Promise<Subgraph> =>
    request<Subgraph>(`/graph/neighborhood/${encodeURIComponent(id)}`, { query }),

  /* ── v3.1 graph / correlation / evidence / approvals ───────────────── */

  /** `getMultiLayerSubgraph` — POST /graph/subgraph (VIEWER+, presupuesto acotado) */
  getMultiLayerSubgraph: (body: MultiLayerSubgraphRequest): Promise<MultiLayerSubgraphResponse> =>
    request<MultiLayerSubgraphResponse>("/graph/subgraph", { method: "POST", body }),

  /** `findSigintSocmintColocation` — POST /correlation/colocation (VIEWER+) */
  findSigintSocmintColocation: (body: ColocationRequest): Promise<ColocationResponse> =>
    request<ColocationResponse>("/correlation/colocation", { method: "POST", body }),

  /** `detectAliasEvasion` — POST /analytics/alias-evasion (VIEWER+) */
  detectAliasEvasion: (body: AliasEvasionRequest): Promise<AliasEvasionResponse> =>
    request<AliasEvasionResponse>("/analytics/alias-evasion", { method: "POST", body }),

  /** `listProposals` — GET /proposals?status=AGENT_STAGED (ANALYST+) */
  listProposals: (query: ListProposalsQuery = {}): Promise<ProposalList> =>
    request<ProposalList>("/proposals", { query }),

  /** `getProposal` — GET /proposals/{id} (ANALYST+) */
  getProposal: (proposalId: string): Promise<Proposal> =>
    request<Proposal>(`/proposals/${encodeURIComponent(proposalId)}`),

  /** `approveProposal` — POST /proposals/{id}/approve (ANALYST+, version obligatoria) */
  approveProposal: (proposalId: string, body: ProposalDecision): Promise<Proposal> =>
    request<Proposal>(`/proposals/${encodeURIComponent(proposalId)}/approve`, {
      method: "POST",
      body,
    }),

  /** `rejectProposal` — POST /proposals/{id}/reject (ANALYST+, version obligatoria) */
  rejectProposal: (proposalId: string, body: ProposalDecision): Promise<Proposal> =>
    request<Proposal>(`/proposals/${encodeURIComponent(proposalId)}/reject`, {
      method: "POST",
      body,
    }),

  /** `getEvidence` — GET /evidence/{id} (VIEWER+, metadatos; nunca bytes sin hash) */
  getEvidence: (evidenceId: string): Promise<Evidence> =>
    request<Evidence>(`/evidence/${encodeURIComponent(evidenceId)}`),

  /** `verifyEvidence` — GET /evidence/{id}/verify (ANALYST+, SHA-256 + cadena) */
  verifyEvidence: (evidenceId: string): Promise<EvidenceVerification> =>
    request<EvidenceVerification>(`/evidence/${encodeURIComponent(evidenceId)}/verify`),

  /* ── context (Pre-Flight, Fase 0) ───────────────────────────────── */
  /** `postContextPreflight` — POST /context/preflight (AI_INFORMANT | ANALYST) */
  postContextPreflight: (body: PreflightRequest): Promise<PreflightResponse> =>
    request<PreflightResponse>("/context/preflight", { method: "POST", body }),

  /* ── ingest ─────────────────────────────────────────────────────── */
  /** `createIngestJob` — POST /ingest (multipart, ANALYST+) */
  createIngestJob: (file: File, title: string): Promise<IngestJob> => {
    const form = new FormData();
    form.append("file", file);
    form.append("title", title);
    return request<IngestJob>("/ingest", { method: "POST", body: form });
  },

  /**
   * `createInformantIngestJob` — POST /ingest/informant (AI_INFORMANT, M2M).
   * Requiere `X-Preflight-Token` vigente; sin él responde `409` (Regla 3).
   * Se expone por completitud del contrato; el dashboard humano no la invoca.
   */
  createInformantIngestJob: (
    file: File,
    payload: string,
    preflightToken: string,
  ): Promise<IngestJob> => {
    const form = new FormData();
    form.append("file", file);
    form.append("payload", payload);
    return request<IngestJob>("/ingest/informant", {
      method: "POST",
      body: form,
      headers: { "X-Preflight-Token": preflightToken },
    });
  },

  /** `getIngestJob` — GET /ingest/{job_id} (VIEWER+) */
  getIngestJob: (jobId: string): Promise<IngestJob> =>
    request<IngestJob>(`/ingest/${encodeURIComponent(jobId)}`),

  /** `ingestProgressWs` — WS /ingest/{job_id}/ws (VIEWER+, progreso en vivo) */
  ingestProgressWs: (jobId: string): WebSocket =>
    new WebSocket(websocketUrl(`/ingest/${encodeURIComponent(jobId)}/ws`)),

  /* ── reports ────────────────────────────────────────────────────── */
  /** `getReport` — GET /reports/{id} (VIEWER+) */
  getReport: (id: string): Promise<Report> => request<Report>(`/reports/${encodeURIComponent(id)}`),

  /** `getReportFile` — GET /reports/{id}/file (VIEWER+, PDF binario) */
  getReportFile: (id: string): Promise<Blob> =>
    requestBlob(`/reports/${encodeURIComponent(id)}/file`),

  /** `getReportChunks` — GET /reports/{id}/chunks (VIEWER+, para el subrayado) */
  getReportChunks: (id: string): Promise<ChunkList> =>
    request<ChunkList>(`/reports/${encodeURIComponent(id)}/chunks`),

  /* ── analyses ───────────────────────────────────────────────────── */
  /** `listAnalyses` — GET /analyses?status= (VIEWER+, expedientes analíticos) */
  listAnalyses: (query: ListAnalysesQuery = {}): Promise<AnalysisList> =>
    request<AnalysisList>("/analyses", { query }),

  /** `createAnalysis` — POST /analyses (ANALYST+) */
  createAnalysis: (body: AnalysisCreate): Promise<Analysis> =>
    request<Analysis>("/analyses", { method: "POST", body }),

  /** `getAnalysis` — GET /analyses/{id} (VIEWER+) */
  getAnalysis: (id: string): Promise<Analysis> =>
    request<Analysis>(`/analyses/${encodeURIComponent(id)}`),

  /** `updateAnalysis` — PUT /analyses/{id} (ANALYST+; única autoridad humana) */
  updateAnalysis: (id: string, body: AnalysisUpdate): Promise<Analysis> =>
    request<Analysis>(`/analyses/${encodeURIComponent(id)}`, { method: "PUT", body }),

  /** `createIntelRequirement` — POST /analyses/{id}/requirements (ANALYST+) */
  createIntelRequirement: (id: string, body: IntelRequirementCreate): Promise<IntelRequirement> =>
    request<IntelRequirement>(`/analyses/${encodeURIComponent(id)}/requirements`, {
      method: "POST",
      body,
    }),

  /** `listAnalysisRequirements` — GET /analyses/{id}/requirements (VIEWER+, RFI del expediente) */
  listAnalysisRequirements: (id: string): Promise<IntelRequirementList> =>
    request<IntelRequirementList>(`/analyses/${encodeURIComponent(id)}/requirements`),

  /* ── coherence ──────────────────────────────────────────────────── */
  /** `listCoherenceAlerts` — GET /coherence/alerts?status= (ANALYST+) */
  listCoherenceAlerts: (query: ListCoherenceAlertsQuery = {}): Promise<CoherenceAlertList> =>
    request<CoherenceAlertList>("/coherence/alerts", { query }),

  /** `resolveCoherenceAlert` — PATCH /coherence/alerts/{alert_id}/resolve (ANALYST+) */
  resolveCoherenceAlert: (
    alertId: string,
    body: CoherenceAlertResolve,
  ): Promise<CoherenceAlert> =>
    request<CoherenceAlert>(`/coherence/alerts/${encodeURIComponent(alertId)}/resolve`, {
      method: "PATCH",
      body,
    }),

  /* ── search ─────────────────────────────────────────────────────── */
  /** `searchRag` — POST /search/rag (VIEWER+, siempre con citations) */
  searchRag: (body: RagQuery): Promise<RagResponse> =>
    request<RagResponse>("/search/rag", { method: "POST", body }),

  /* ── system ─────────────────────────────────────────────────────── */
  /** `getInfo` — GET /info (público: salud y versión) */
  getInfo: (): Promise<ServiceInfo> => request<ServiceInfo>("/info", { auth: false }),
};

export type ApiClient = typeof api;
