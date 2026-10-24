"use client";

/**
 * Dashboard — shell de las 4 vistas navegables de SPEC-05 §0/§1.
 *
 *  1. Grafo interactivo ........ components/graph/    (`listGraphNodes`, `listGraphEdges`, `getGraphNeighborhood`)
 *  2. Visor Split-View ......... components/dossier/  (`getGraphNode`, `updateGraphNode`, `lockGraphNode`, `deleteGraphNode`, reports/*)
 *  3. Workspace de Análisis .... components/analysis/ (`createAnalysis`, `getAnalysis`, `updateAnalysis`, `createIntelRequirement`)
 *  4. Bandeja de Coherencia .... components/coherence/(`listCoherenceAlerts`, `resolveCoherenceAlert`)
 *  Transversales ............... components/ingest/ (`createIngestJob`, `ingestProgressWs`) + components/search/ (`searchRag`)
 *
 * Autorizado por: SPEC-05 §0-§7 y SPEC-03 §3-§8.
 * Las acciones de edición/borrado/bloqueo y las vistas de análisis/coherencia se
 * ocultan según el rol (SPEC-04 §1); la seguridad real vive en el backend.
 */

import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";

import AnalysisWorkspace from "@/components/analysis/AnalysisWorkspace";
import CoherenceInbox from "@/components/coherence/CoherenceInbox";
import DossierPanel from "@/components/dossier/DossierPanel";
import ReportViewer from "@/components/dossier/ReportViewer";
import GraphCanvas from "@/components/graph/GraphCanvas";
import GraphFilters, { DEFAULT_FILTERS, type GraphFilterState } from "@/components/graph/GraphFilters";
import Timeline, { type TimeWindow } from "@/components/graph/Timeline";
import IngestDropzone from "@/components/ingest/IngestDropzone";
import SearchConsole from "@/components/search/SearchConsole";
import { useAuth, type RbacAction } from "@/context/AuthContext";
import { ApiError, api } from "@/services/api";
import type { CoherenceAlert, GraphEdge, GraphNode, GraphNodeUpdate, Subgraph } from "@/services/api";

type DashboardView = "graph" | "dossier" | "analysis" | "coherence";

interface ViewTab {
  id: DashboardView;
  label: string;
  spec: string;
  action: RbacAction | null;
}

const VIEW_TABS: readonly ViewTab[] = [
  { id: "graph", label: "Grafo", spec: "SPEC-05 §2", action: "graph:read" },
  { id: "dossier", label: "Dossier", spec: "SPEC-05 §3", action: "graph:read" },
  { id: "analysis", label: "Análisis", spec: "SPEC-05 §4", action: "analysis:read" },
  { id: "coherence", label: "Coherencia", spec: "SPEC-05 §5", action: "coherence:read" },
];

interface ReportCitationModal {
  reportId: string;
  pageNumber: number;
  quote: string;
}

function mergeById<T extends { id: string }>(current: T[], incoming: T[]): T[] {
  const map = new Map(current.map((item) => [item.id, item]));
  for (const item of incoming) map.set(item.id, item);
  return [...map.values()];
}

function toIsoStart(date: string): string | undefined {
  return date ? new Date(`${date}T00:00:00.000Z`).toISOString() : undefined;
}

function toIsoEnd(date: string): string | undefined {
  return date ? new Date(`${date}T23:59:59.999Z`).toISOString() : undefined;
}

export default function DashboardPage(): ReactElement {
  const { status, user, role, logout, can } = useAuth();
  const router = useRouter();

  const [view, setView] = useState<DashboardView>("graph");

  const [filters, setFilters] = useState<GraphFilterState>(DEFAULT_FILTERS);
  const [timeWindow, setTimeWindow] = useState<TimeWindow>({ from: "", to: "" });
  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [searchHighlight, setSearchHighlight] = useState<string[]>([]);
  const [alerts, setAlerts] = useState<CoherenceAlert[]>([]);
  const [reportModal, setReportModal] = useState<ReportCitationModal | null>(null);

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  const canCoherenceRead = can("coherence:read");

  // Carga del grafo — `listGraphNodes` + `listGraphEdges` (VIEWER+).
  useEffect(() => {
    if (status !== "authenticated") return;
    let active = true;
    setLoading(true);
    setError(null);

    (async () => {
      try {
        const [nodeList, edgeList] = await Promise.all([
          api.listGraphNodes({
            label: filters.label || undefined,
            q: filters.q.trim() || undefined,
            from: toIsoStart(timeWindow.from),
            to: toIsoEnd(timeWindow.to),
            min_confidence: filters.minConfidence > 0 ? filters.minConfidence : undefined,
            limit: 500,
          }),
          api.listGraphEdges({
            min_confidence: filters.minConfidence > 0 ? filters.minConfidence : undefined,
            limit: 500,
          }),
        ]);
        if (!active) return;
        setNodes(nodeList.items ?? []);
        setEdges(edgeList.items ?? []);
      } catch (err) {
        if (!active) return;
        setError(err instanceof ApiError ? err.message : "No se pudo cargar el grafo.");
      } finally {
        if (active) setLoading(false);
      }
    })();

    return () => {
      active = false;
    };
  }, [status, filters, timeWindow, reloadToken]);

  // Alertas de coherencia (sólo ANALYST/ADMIN) → marcan contradicciones del grafo.
  const loadAlerts = useCallback(async (): Promise<void> => {
    if (!canCoherenceRead) {
      setAlerts([]);
      return;
    }
    try {
      const list = await api.listCoherenceAlerts({});
      setAlerts(list.items ?? []);
    } catch {
      // La bandeja es ANALYST+; un fallo aquí no debe romper el grafo.
      setAlerts([]);
    }
  }, [canCoherenceRead]);

  useEffect(() => {
    void loadAlerts();
  }, [loadAlerts, reloadToken]);

  const timelineBounds = useMemo<{ min: string; max: string }>(() => {
    const times = edges
      .flatMap((edge) => [Date.parse(edge.first_seen), Date.parse(edge.last_seen)])
      .filter((value) => !Number.isNaN(value));
    if (times.length === 0) {
      const now = new Date().toISOString();
      return { min: now, max: now };
    }
    return {
      min: new Date(Math.min(...times)).toISOString(),
      max: new Date(Math.max(...times)).toISOString(),
    };
  }, [edges]);

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) ?? null,
    [nodes, selectedNodeId],
  );

  const contradictionNodeIds = useMemo<string[]>(() => {
    const ids = new Set<string>();
    for (const alert of alerts) {
      if (alert.alert_type === "CONTRADICTION" && alert.entity_id) ids.add(alert.entity_id);
    }
    return [...ids];
  }, [alerts]);

  // Resaltado de rutas críticas: resultado GraphRAG o vecindad del nodo activo.
  const highlightIds = useMemo<string[]>(() => {
    if (searchHighlight.length > 0) return searchHighlight;
    if (!selectedNodeId) return [];
    const incident = edges
      .filter((edge) => edge.from === selectedNodeId || edge.to === selectedNodeId)
      .map((edge) => edge.id);
    return [selectedNodeId, ...incident];
  }, [searchHighlight, selectedNodeId, edges]);

  const handleSelectNode = useCallback((node: GraphNode | null): void => {
    setSelectedNodeId(node?.id ?? null);
    if (node) {
      setSearchHighlight([]);
      // SPEC-05 §2: selección de nodo → abre el Dossier sin perder el grafo.
      setView("dossier");
    }
  }, []);

  const handleExpand = useCallback(async (nodeId: string): Promise<void> => {
    try {
      const subgraph = await api.getGraphNeighborhood(nodeId, { depth: 1 });
      setNodes((previous) => mergeById(previous, subgraph.nodes ?? []));
      setEdges((previous) => mergeById(previous, subgraph.edges ?? []));
      setView("graph");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo expandir la vecindad.");
    }
  }, []);

  const handleSubgraph = useCallback((subgraph: Subgraph, ids: string[]): void => {
    setNodes((previous) => mergeById(previous, subgraph.nodes ?? []));
    setEdges((previous) => mergeById(previous, subgraph.edges ?? []));
    setSearchHighlight(ids);
    setView("graph");
  }, []);

  const handleSaveNode = useCallback(
    async (id: string, update: GraphNodeUpdate): Promise<void> => {
      const updated = await api.updateGraphNode(id, update); // ANALYST+
      setNodes((previous) => mergeById(previous, [updated]));
    },
    [],
  );

  const handleLockNode = useCallback(async (id: string, locked: boolean): Promise<void> => {
    const updated = await api.lockGraphNode(id, { locked }); // ANALYST+
    setNodes((previous) => mergeById(previous, [updated]));
  }, []);

  const handleDeleteNode = useCallback(async (id: string): Promise<void> => {
    await api.deleteGraphNode(id); // ADMIN
    setNodes((previous) => previous.filter((node) => node.id !== id));
    setEdges((previous) => previous.filter((edge) => edge.from !== id && edge.to !== id));
    setSelectedNodeId(null);
    setView("graph");
  }, []);

  const handleOpenEntity = useCallback((entityId: string): void => {
    setSelectedNodeId(entityId);
    setView("dossier");
  }, []);

  const handleLogout = useCallback(async (): Promise<void> => {
    await logout();
    router.replace("/login");
  }, [logout, router]);

  if (status === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-400">
        Cargando sesión…
      </div>
    );
  }

  if (status === "anonymous") {
    return (
      <div className="flex min-h-screen items-center justify-center text-sm text-slate-400">
        Redirigiendo al acceso seguro…
      </div>
    );
  }

  const roleBadge =
    role === "ADMIN"
      ? "border-neon-crimson/50 text-neon-crimson"
      : role === "ANALYST"
        ? "border-neon-amber/50 text-neon-amber"
        : "border-base-600 text-slate-300";

  const visibleTabs = VIEW_TABS.filter((tab) => tab.action === null || can(tab.action));

  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-3 border-b border-base-700 bg-base-900/80 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="inline-block h-2.5 w-2.5 rounded-full bg-neon-cyan" aria-hidden="true" />
          <h1 className="text-sm font-semibold uppercase tracking-[0.2em] text-slate-200">
            OSINT Nexus
          </h1>
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setReloadToken((token) => token + 1)}
            className="rounded border border-base-600 px-2 py-1 text-[11px] text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan"
          >
            Refrescar
          </button>
          {role && (
            <span className={`rounded border px-2 py-0.5 text-[11px] font-semibold ${roleBadge}`}>
              {role}
            </span>
          )}
          <span className="text-xs text-slate-400">{user?.username ?? "operador"}</span>
          <button
            type="button"
            onClick={() => void handleLogout()}
            className="rounded border border-base-600 px-2 py-1 text-[11px] text-slate-300 transition hover:border-neon-crimson hover:text-neon-crimson"
          >
            Salir
          </button>
        </div>
      </header>

      {/* Navegación entre las 4 vistas (SPEC-05 §0) */}
      <nav
        aria-label="Vistas del dashboard"
        className="flex items-center gap-1 border-b border-base-700 bg-base-900/50 px-4"
      >
        {visibleTabs.map((tab) => (
          <button
            key={tab.id}
            type="button"
            aria-current={view === tab.id ? "page" : undefined}
            onClick={() => setView(tab.id)}
            title={tab.spec}
            className={`-mb-px border-b-2 px-3 py-2 text-xs font-semibold uppercase tracking-wider transition ${
              view === tab.id
                ? "border-neon-cyan text-neon-cyan"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {error && (
        <p
          role="alert"
          className="border-b border-neon-crimson/40 bg-neon-crimson/10 px-4 py-2 text-xs text-neon-crimson"
        >
          {error}
        </p>
      )}

      <div className="grid min-h-0 flex-1 gap-3 overflow-hidden p-3 lg:grid-cols-[340px_1fr]">
        {/* Transversales: consola GraphRAG + ingesta drag & drop */}
        <aside className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <SearchConsole
            onSubgraph={handleSubgraph}
            onOpenReport={(reportId, pageNumber, quote) =>
              setReportModal({ reportId, pageNumber, quote })
            }
            onOpenAnalysis={(analysisId) => {
              setSelectedNodeId(analysisId);
              setView("analysis");
            }}
          />
          <IngestDropzone onCompleted={() => setReloadToken((token) => token + 1)} />
        </aside>

        <main className="min-h-0 overflow-hidden">
          {view === "graph" && (
            <div className="flex h-full min-h-0 flex-col gap-3">
              <div className="grid shrink-0 gap-3 md:grid-cols-2 xl:grid-cols-[1fr_360px]">
                <GraphFilters
                  value={filters}
                  onChange={setFilters}
                  onReset={() => {
                    setFilters(DEFAULT_FILTERS);
                    setTimeWindow({ from: "", to: "" });
                  }}
                  disabled={loading}
                  resultCount={nodes.length}
                />
                <Timeline
                  min={timelineBounds.min}
                  max={timelineBounds.max}
                  value={timeWindow}
                  onChange={setTimeWindow}
                  disabled={loading}
                />
              </div>
              <div className="flex shrink-0 items-center justify-between gap-2 text-[11px] text-slate-400">
                <span>
                  {selectedNode
                    ? `Seleccionado: ${selectedNode.canonical_name ?? selectedNode.id}`
                    : "Doble clic en un nodo para expandir su vecindad."}
                </span>
                <div className="flex gap-2">
                  <button
                    type="button"
                    disabled={!selectedNodeId}
                    onClick={() => selectedNodeId && void handleExpand(selectedNodeId)}
                    className="rounded border border-base-600 px-2 py-0.5 text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan disabled:opacity-40"
                  >
                    Expandir vecindad
                  </button>
                  <button
                    type="button"
                    disabled={!selectedNodeId}
                    onClick={() => selectedNodeId && handleOpenEntity(selectedNodeId)}
                    className="rounded border border-base-600 px-2 py-0.5 text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan disabled:opacity-40"
                  >
                    Abrir dossier
                  </button>
                </div>
              </div>
              <div className="min-h-0 flex-1">
                <GraphCanvas
                  nodes={nodes}
                  edges={edges}
                  selectedNodeId={selectedNodeId}
                  highlightElementIds={highlightIds}
                  contradictionNodeIds={contradictionNodeIds}
                  onSelectNode={handleSelectNode}
                  onExpandNode={(node) => void handleExpand(node.id)}
                  loading={loading}
                />
              </div>
            </div>
          )}

          {view === "dossier" && (
            <DossierPanel
              node={selectedNode}
              edges={edges}
              canEdit={can("graph:update")}
              canLock={can("graph:lock")}
              canDelete={can("graph:delete")}
              onSave={handleSaveNode}
              onLock={handleLockNode}
              onDelete={handleDeleteNode}
              onOpenAnalysis={(analysisId) => {
                setView("analysis");
                setSelectedNodeId(analysisId);
              }}
            />
          )}

          {view === "analysis" && (
            <AnalysisWorkspace
              canWrite={can("analysis:write")}
              initialTargetEntityId={selectedNodeId}
              onOpenReport={(reportId, pageNumber, quote) =>
                setReportModal({ reportId, pageNumber, quote })
              }
              onOpenEntity={handleOpenEntity}
              onChanged={() => setReloadToken((token) => token + 1)}
            />
          )}

          {view === "coherence" && canCoherenceRead && (
            <CoherenceInbox
              canResolve={can("coherence:resolve")}
              onOpenAnalysis={(analysisId) => {
                setSelectedNodeId(analysisId);
                setView("analysis");
              }}
              onOpenEntity={handleOpenEntity}
              onChanged={() => setReloadToken((token) => token + 1)}
            />
          )}
        </main>
      </div>

      {/* Visor de evidencia (cita OSINT) reutilizable desde cualquier vista */}
      {reportModal && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Informe de la cita"
          className="fixed inset-0 z-40 flex items-center justify-center bg-base-950/80 p-4"
        >
          <div className="flex h-[85vh] w-full max-w-4xl flex-col rounded-lg border border-base-700 bg-base-900 p-3">
            <button
              type="button"
              onClick={() => setReportModal(null)}
              className="mb-2 self-end rounded border border-base-600 px-2 py-0.5 text-xs text-slate-300 transition hover:border-neon-crimson hover:text-neon-crimson"
            >
              Cerrar
            </button>
            <div className="min-h-0 flex-1">
              <ReportViewer
                reportId={reportModal.reportId}
                pageNumber={reportModal.pageNumber}
                quote={reportModal.quote}
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
