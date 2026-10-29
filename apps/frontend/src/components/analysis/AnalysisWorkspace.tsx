"use client";

/**
 * AnalysisWorkspace — Workspace de Análisis Humano (Vista 3).
 *
 * Autorizado por: SPEC-05 §4 — alta/edición de `Analysis`
 * (`thesis_summary`, `conclusion_body`, `status`); creación y listado de
 * `IntelRequirement` (RFI) con `priority`; muestra `coherence_score` e historial
 * de evidencias (`CORROBORATES`/`EXTENDS`). La IA **no** edita aquí: el Analista
 * es la única autoridad (Regla 7).
 *
 * Traza a: `listAnalyses` (expedientes), `getAnalysis`, `createAnalysis`,
 * `updateAnalysis`, `createIntelRequirement`, `listAnalysisRequirements` (RFI
 * del expediente) y `listGraphEdges` (evidencia analítica).
 *
 * Las acciones de escritura se ocultan si el rol no es ANALYST/ADMIN
 * (`analysis:write`, SPEC-04 §1).
 */

import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ANALYSIS_EVIDENCE_EDGE_TYPES, edgeDisplay } from "@/lib/labels";
import { api, ApiError } from "@/services/api";
import type {
  Analysis,
  AnalysisStatus,
  GraphEdge,
  IntelRequirement,
  Priority,
  RequirementStatus,
} from "@/services/api";
import { ANALYSIS_STATUS_VALUES, PRIORITY_VALUES } from "@/services/api";

export interface AnalysisWorkspaceProps {
  canWrite: boolean;
  /** Entidad objetivo preseleccionada (desde el grafo / dossier). */
  initialTargetEntityId?: string | null;
  /** Abre la evidencia de un informe OSINT en el visor. */
  onOpenReport?: (reportId: string, pageNumber: number, quote: string) => void;
  /** Abrir la Vista 2 (dossier) sobre la entidad objetivo. */
  onOpenEntity?: (entityId: string) => void;
  /** Notifica cambios (para refrescar el grafo/alertas del dashboard). */
  onChanged?: () => void;
}

const STATUS_LABEL: Record<AnalysisStatus, string> = {
  ACTIVE: "Activo",
  UNDER_REVIEW: "En revisión",
  CLOSED: "Cerrado",
};

const PRIORITY_LABEL: Record<Priority, string> = {
  CRITICAL: "Crítico",
  HIGH: "Alto",
  MEDIUM: "Medio",
  LOW: "Bajo",
};

const PRIORITY_COLOR: Record<Priority, string> = {
  CRITICAL: "text-neon-crimson",
  HIGH: "text-neon-amber",
  MEDIUM: "text-neon-cyan",
  LOW: "text-slate-400",
};

const REQUIREMENT_STATUS_LABEL: Record<RequirementStatus, string> = {
  OPEN: "Abierto",
  ANSWERED: "Respondido",
};

const REQUIREMENT_STATUS_STYLE: Record<RequirementStatus, string> = {
  OPEN: "border-neon-amber/50 text-neon-amber",
  ANSWERED: "border-neon-emerald/50 text-neon-emerald",
};

interface AnalysisOption {
  id: string;
  name: string;
}

function optionsFromAnalyses(analyses: Analysis[]): AnalysisOption[] {
  return analyses
    .filter((analysis): analysis is Analysis & { id: string } => typeof analysis.id === "string")
    .map((analysis) => ({
      id: analysis.id,
      name: analysis.title ?? analysis.code ?? analysis.id,
    }));
}

export default function AnalysisWorkspace({
  canWrite,
  initialTargetEntityId = null,
  onOpenReport,
  onOpenEntity,
  onChanged,
}: AnalysisWorkspaceProps): ReactElement {
  const [options, setOptions] = useState<AnalysisOption[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [requirements, setRequirements] = useState<IntelRequirement[]>([]);
  const [openRfis, setOpenRfis] = useState<IntelRequirement[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [creating, setCreating] = useState(false);
  const [draftCode, setDraftCode] = useState("");
  const [draftTitle, setDraftTitle] = useState("");
  const [draftThesis, setDraftThesis] = useState("");
  const [draftConclusion, setDraftConclusion] = useState("");

  const [editing, setEditing] = useState(false);
  const [editStatus, setEditStatus] = useState<AnalysisStatus>("ACTIVE");

  const [rfiQuestion, setRfiQuestion] = useState("");
  const [rfiPriority, setRfiPriority] = useState<Priority>("MEDIUM");
  const [rfiTarget, setRfiTarget] = useState("");

  const refreshOptions = useCallback(async (): Promise<void> => {
    try {
      const [analysisList, edgeList] = await Promise.all([
        api.listAnalyses(),
        api.listGraphEdges({ limit: 500 }),
      ]);
      setOptions(optionsFromAnalyses(analysisList.items ?? []));
      setEdges(edgeList.items ?? []);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cargar el grafo analítico.");
    }
  }, []);

  useEffect(() => {
    void refreshOptions();
  }, [refreshOptions]);

  useEffect(() => {
    if (!selectedId) {
      setAnalysis(null);
      setRequirements([]);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const [detail, rfiList] = await Promise.all([
          api.getAnalysis(selectedId),
          api.listAnalysisRequirements(selectedId),
        ]);
        if (!active) return;
        setAnalysis(detail);
        setRequirements(rfiList.items ?? []);
        setDraftTitle(detail.title ?? "");
        setDraftThesis(detail.thesis_summary ?? "");
        setDraftConclusion(detail.conclusion_body ?? "");
        setEditStatus(detail.status ?? "ACTIVE");
      } catch (err) {
        if (!active) return;
        setError(err instanceof ApiError ? err.message : "No se pudo cargar el expediente.");
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [selectedId]);

  const evidence = useMemo<GraphEdge[]>(() => {
    if (!selectedId) return [];
    return edges.filter(
      (edge) =>
        ANALYSIS_EVIDENCE_EDGE_TYPES.includes(edge.type) &&
        (edge.from === selectedId || edge.to === selectedId),
    );
  }, [edges, selectedId]);

  const handleCreate = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const created = await api.createAnalysis({
        code: draftCode.trim(),
        title: draftTitle.trim(),
        thesis_summary: draftThesis.trim(),
        conclusion_body: draftConclusion.trim() || undefined,
      });
      setCreating(false);
      setDraftCode("");
      setDraftTitle("");
      setDraftThesis("");
      setDraftConclusion("");
      if (created.id) {
        const newId = created.id;
        setOptions((previous) => [
          { id: newId, name: created.title ?? created.code ?? newId },
          ...previous,
        ]);
        setSelectedId(newId);
      }
      await refreshOptions();
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo crear el expediente.");
    } finally {
      setBusy(false);
    }
  };

  const handleUpdate = async (): Promise<void> => {
    if (!analysis?.id) return;
    setBusy(true);
    setError(null);
    try {
      const updated = await api.updateAnalysis(analysis.id, {
        title: draftTitle.trim(),
        thesis_summary: draftThesis.trim(),
        conclusion_body: draftConclusion.trim(),
        status: editStatus,
      });
      setAnalysis(updated);
      setEditing(false);
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo actualizar el expediente.");
    } finally {
      setBusy(false);
    }
  };

  const handleCreateRequirement = async (): Promise<void> => {
    if (!analysis?.id) return;
    setBusy(true);
    setError(null);
    try {
      const created = await api.createIntelRequirement(analysis.id, {
        question: rfiQuestion.trim(),
        priority: rfiPriority,
        target_entity_id: rfiTarget.trim() || (initialTargetEntityId ?? undefined),
      });
      setRequirements((previous) => [created, ...previous]);
      setRfiQuestion("");
      setRfiTarget("");
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo crear el requerimiento.");
    } finally {
      setBusy(false);
    }
  };

  const handleLoadOpenRfis = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const keywords = analysis?.code?.trim() ? [analysis.code] : ["RFI"];
      const preflight = await api.postContextPreflight({ keywords });
      setOpenRfis(preflight.requirements);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudieron consultar los RFIs abiertos.");
    } finally {
      setBusy(false);
    }
  };

  const coherence = analysis?.coherence_score;

  return (
    <div className="grid h-full min-h-0 gap-3 lg:grid-cols-[300px_1fr]">
      {/* Lista de expedientes (listAnalyses, VIEWER+) */}
      <section
        aria-label="Expedientes analíticos"
        className="flex min-h-0 flex-col gap-3 overflow-y-auto rounded-lg border border-base-700 bg-base-900/70 p-4"
      >
        <header className="flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
            Expedientes
          </h2>
          {canWrite && (
            <button
              type="button"
              onClick={() => setCreating((value) => !value)}
              className="rounded border border-neon-violet/50 px-2 py-0.5 text-[11px] text-neon-violet transition hover:bg-neon-violet/10"
            >
              {creating ? "Cancelar" : "Nuevo"}
            </button>
          )}
        </header>

        {options.length === 0 ? (
          <p className="text-xs text-slate-400">No hay expedientes analíticos en el grafo.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {options.map((option) => (
              <li key={option.id}>
                <button
                  type="button"
                  onClick={() => setSelectedId(option.id)}
                  className={`w-full truncate rounded border px-2 py-1 text-left text-xs transition ${
                    option.id === selectedId
                      ? "border-neon-violet/60 text-neon-violet"
                      : "border-base-700 text-slate-300 hover:border-neon-violet/40"
                  }`}
                >
                  {option.name}
                </button>
              </li>
            ))}
          </ul>
        )}

        {creating && canWrite && (
          <form
            className="flex flex-col gap-2 border-t border-base-700 pt-3"
            onSubmit={(event) => {
              event.preventDefault();
              void handleCreate();
            }}
          >
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Código *
              <input
                required
                value={draftCode}
                onChange={(event) => setDraftCode(event.target.value)}
                placeholder="AN-2026-014"
                className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Título *
              <input
                required
                value={draftTitle}
                onChange={(event) => setDraftTitle(event.target.value)}
                className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Tesis (thesis_summary) *
              <textarea
                required
                rows={3}
                value={draftThesis}
                onChange={(event) => setDraftThesis(event.target.value)}
                className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Conclusión (opcional)
              <textarea
                rows={2}
                value={draftConclusion}
                onChange={(event) => setDraftConclusion(event.target.value)}
                className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="rounded border border-neon-violet/50 px-2 py-1 text-xs text-neon-violet transition hover:bg-neon-violet/10 disabled:opacity-40"
            >
              Crear expediente
            </button>
          </form>
        )}
      </section>

      {/* Detalle del expediente */}
      <section className="flex min-h-0 flex-col gap-3 overflow-y-auto">
        {error && (
          <p role="alert" className="rounded border border-neon-crimson/40 bg-neon-crimson/10 p-2 text-xs text-neon-crimson">
            {error}
          </p>
        )}

        {!selectedId ? (
          <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-base-700 text-center text-sm text-slate-400">
            Selecciona un expediente o crea uno nuevo.
          </div>
        ) : loading || !analysis ? (
          <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
            Cargando expediente…
          </div>
        ) : (
          <>
            <article className="rounded-lg border border-base-700 bg-base-900/70 p-4">
              <header className="flex items-start justify-between gap-3">
                <div className="min-w-0">
                  <p className="font-mono text-[11px] text-slate-400">{analysis.code ?? "—"}</p>
                  <h2 className="truncate text-lg font-semibold text-slate-100">
                    {analysis.title ?? "Sin título"}
                  </h2>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span className="rounded border border-base-600 px-2 py-0.5 text-[10px] uppercase text-slate-300">
                    {STATUS_LABEL[analysis.status ?? "ACTIVE"]}
                  </span>
                  {canWrite && !editing && (
                    <button
                      type="button"
                      onClick={() => setEditing(true)}
                      className="rounded border border-base-600 px-2 py-0.5 text-[11px] text-slate-200 transition hover:border-neon-violet hover:text-neon-violet"
                    >
                      Editar
                    </button>
                  )}
                </div>
              </header>

              {typeof coherence === "number" && (
                <div className="mt-3">
                  <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-400">
                    coherence_score
                  </p>
                  <div className="flex items-center gap-2">
                    <div className="h-2 flex-1 rounded-full bg-base-700" role="presentation">
                      <div
                        className="h-2 rounded-full bg-neon-emerald"
                        style={{ width: `${Math.round(coherence * 100)}%` }}
                      />
                    </div>
                    <span className="font-mono text-xs text-slate-300">
                      {coherence.toFixed(2)}
                    </span>
                  </div>
                </div>
              )}

              {editing && canWrite ? (
                <form
                  className="mt-3 flex flex-col gap-2"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handleUpdate();
                  }}
                >
                  <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                    Título
                    <input
                      value={draftTitle}
                      onChange={(event) => setDraftTitle(event.target.value)}
                      className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                    Tesis (thesis_summary)
                    <textarea
                      rows={3}
                      value={draftThesis}
                      onChange={(event) => setDraftThesis(event.target.value)}
                      className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
                    />
                  </label>
                  <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                    Conclusión (conclusion_body)
                    <textarea
                      rows={4}
                      value={draftConclusion}
                      onChange={(event) => setDraftConclusion(event.target.value)}
                      className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
                    />
                    <span className="text-[10px] text-slate-400">
                      La IA nunca sobrescribe la conclusión (Regla 7).
                    </span>
                  </label>
                  <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                    Estado
                    <select
                      value={editStatus}
                      onChange={(event) => setEditStatus(event.target.value as AnalysisStatus)}
                      className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
                    >
                      {ANALYSIS_STATUS_VALUES.map((status) => (
                        <option key={status} value={status}>
                          {STATUS_LABEL[status]}
                        </option>
                      ))}
                    </select>
                  </label>
                  <div className="flex gap-2">
                    <button
                      type="submit"
                      disabled={busy}
                      className="rounded border border-neon-violet/50 px-2 py-1 text-xs text-neon-violet transition hover:bg-neon-violet/10 disabled:opacity-40"
                    >
                      Guardar
                    </button>
                    <button
                      type="button"
                      onClick={() => setEditing(false)}
                      className="rounded border border-base-600 px-2 py-1 text-xs text-slate-300"
                    >
                      Cancelar
                    </button>
                  </div>
                </form>
              ) : (
                <div className="mt-3 flex flex-col gap-3 text-sm text-slate-300">
                  <div>
                    <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-400">
                      Tesis
                    </p>
                    <p className="whitespace-pre-wrap">{analysis.thesis_summary ?? "—"}</p>
                  </div>
                  <div>
                    <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-400">
                      Conclusión
                    </p>
                    <p className="whitespace-pre-wrap">{analysis.conclusion_body ?? "—"}</p>
                  </div>
                </div>
              )}
            </article>

            {/* Requerimientos de inteligencia (RFI) */}
            <article className="rounded-lg border border-base-700 bg-base-900/70 p-4">
              <header className="mb-2 flex items-center justify-between">
                <h3 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
                  Requerimientos (RFI)
                </h3>
                {canWrite && (
                  <button
                    type="button"
                    onClick={() => void handleLoadOpenRfis()}
                    disabled={busy}
                    className="rounded border border-base-600 px-2 py-0.5 text-[10px] text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan disabled:opacity-40"
                  >
                    RFIs abiertos (preflight)
                  </button>
                )}
              </header>

              {requirements.length === 0 ? (
                <p className="text-xs text-slate-400">
                  Sin RFIs creados en esta sesión para este expediente.
                </p>
              ) : (
                <ul className="mb-3 flex flex-col gap-1">
                  {requirements.map((requirement) => (
                    <li
                      key={requirement.id}
                      className="flex items-center justify-between gap-2 rounded border border-base-700 bg-base-950/60 p-2 text-xs"
                    >
                      <span className="min-w-0 truncate text-slate-200">
                        {requirement.question}
                      </span>
                      <span
                        className={`shrink-0 font-semibold ${
                          requirement.priority
                            ? PRIORITY_COLOR[requirement.priority]
                            : "text-slate-400"
                        }`}
                      >
                        {requirement.priority ? PRIORITY_LABEL[requirement.priority] : "—"}
                      </span>
                    </li>
                  ))}
                </ul>
              )}

              {openRfis && (
                <div className="mb-3 rounded border border-base-700 bg-base-950/40 p-2">
                  <p className="mb-1 text-[10px] uppercase tracking-widest text-slate-400">
                    RFIs abiertos (preflight global · {openRfis.length})
                  </p>
                  <ul className="flex flex-col gap-1">
                    {openRfis.map((rfi) => (
                      <li key={rfi.id} className="text-[11px] text-slate-300">
                        <span
                          className={`mr-1 font-semibold ${
                            rfi.priority ? PRIORITY_COLOR[rfi.priority] : "text-slate-400"
                          }`}
                        >
                          {rfi.priority ? PRIORITY_LABEL[rfi.priority] : "—"}
                        </span>
                        {rfi.question}
                      </li>
                    ))}
                  </ul>
                  <p className="mt-1 text-[10px] text-slate-400">
                    SPEC-GAP-4: el contrato v2.0.0 no expone un GET para listar los
                    requerimientos de un expediente (solo POST de creación).
                  </p>
                </div>
              )}

              {canWrite && (
                <form
                  className="flex flex-col gap-2 border-t border-base-700 pt-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    void handleCreateRequirement();
                  }}
                >
                  <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                    Pregunta (question) *
                    <input
                      required
                      value={rfiQuestion}
                      onChange={(event) => setRfiQuestion(event.target.value)}
                      className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
                    />
                  </label>
                  <div className="grid grid-cols-2 gap-2">
                    <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                      Prioridad *
                      <select
                        value={rfiPriority}
                        onChange={(event) => setRfiPriority(event.target.value as Priority)}
                        className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
                      >
                        {PRIORITY_VALUES.map((priority) => (
                          <option key={priority} value={priority}>
                            {PRIORITY_LABEL[priority]}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                      Entidad objetivo (UUID)
                      <input
                        value={rfiTarget}
                        onChange={(event) => setRfiTarget(event.target.value)}
                        placeholder={initialTargetEntityId ?? "opcional"}
                        className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
                      />
                    </label>
                  </div>
                  <button
                    type="submit"
                    disabled={busy}
                    className="self-start rounded border border-neon-cyan/50 px-2 py-1 text-xs text-neon-cyan transition hover:bg-neon-cyan/10 disabled:opacity-40"
                  >
                    Crear RFI
                  </button>
                </form>
              )}
            </article>

            {/* Historial de evidencias (CORROBORATES / EXTENDS / CONTRADICTS…) */}
            <article className="rounded-lg border border-base-700 bg-base-900/70 p-4">
              <h3 className="mb-2 text-xs font-semibold uppercase tracking-widest text-slate-400">
                Evidencias ({evidence.length})
              </h3>
              {evidence.length === 0 ? (
                <p className="text-xs text-slate-400">
                  Sin evidencias enlazadas a este expediente.
                </p>
              ) : (
                <ul className="flex flex-col gap-2">
                  {evidence.map((edge) => (
                    <li
                      key={edge.id}
                      className="rounded border border-base-700 bg-base-950/60 p-2 text-xs"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span
                          className={
                            edge.type === "CONTRADICTS"
                              ? "font-semibold text-neon-crimson"
                              : edge.type === "CORROBORATES"
                                ? "font-semibold text-neon-emerald"
                                : "text-slate-300"
                          }
                        >
                          {edgeDisplay(edge.type)}
                        </span>
                        <span className="font-mono text-[10px] text-slate-400">
                          conf. {edge.confidence.toFixed(2)}
                        </span>
                      </div>
                      <blockquote className="mt-1 border-l-2 border-base-600 pl-2 text-[11px] italic text-slate-400">
                        “{edge.exact_quote}”
                      </blockquote>
                      <div className="mt-1 flex items-center justify-between text-[10px] text-slate-400">
                        <span className="font-mono">
                          informe {edge.source_report_id.slice(0, 8)}… · pág. {edge.page_number}
                        </span>
                        {onOpenReport && (
                          <button
                            type="button"
                            onClick={() =>
                              onOpenReport(edge.source_report_id, edge.page_number, edge.exact_quote)
                            }
                            className="rounded border border-base-600 px-1.5 py-0.5 text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan"
                          >
                            Ver informe
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
            </article>

            {onOpenEntity && initialTargetEntityId && (
              <button
                type="button"
                onClick={() => onOpenEntity(initialTargetEntityId)}
                className="self-start rounded border border-base-600 px-2 py-1 text-[11px] text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan"
              >
                Ver entidad objetivo en el dossier
              </button>
            )}
          </>
        )}
      </section>
    </div>
  );
}
