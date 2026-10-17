"use client";

/**
 * DossierPanel — Visor Split-View (Vista 2).
 *
 * Autorizado por: SPEC-05 §3 — panel izquierdo: ficha de entidad (propiedades +
 * `risk_score` + `aliases`) y lista de análisis que la investigan (`INVESTIGATES`).
 * Panel derecho: visor de PDF que subraya `exact_quote` usando `page_number` y
 * `/reports/{id}/chunks`. Distingue cita de **informe OSINT** de cita de
 * **análisis humano**.
 * Traza a: `getGraphNode` (vía página), `updateGraphNode`, `deleteGraphNode`,
 * `lockGraphNode` y, a través de `ReportViewer`, `getReport`/`getReportFile`/
 * `getReportChunks`.
 *
 * Las acciones de edición/borrado/bloqueo se ocultan según el rol (`can()` —
 * SPEC-04 §1). La autorización real se aplica en el backend.
 */

import { useEffect, useState } from "react";

import AnalysisCitationCard from "@/components/dossier/AnalysisCitationCard";
import ReportViewer from "@/components/dossier/ReportViewer";
import { edgeDisplay, formatDate, labelColor, labelDisplay } from "@/lib/labels";
import { ApiError } from "@/services/api";
import type { GraphEdge, GraphEdgeType, GraphNode, GraphNodeUpdate } from "@/services/api";

export interface DossierPanelProps {
  node: GraphNode | null;
  /** Aristas del grafo (se filtran las incidentes al nodo). */
  edges: GraphEdge[];
  canEdit: boolean;
  canLock: boolean;
  canDelete: boolean;
  onSave: (id: string, update: GraphNodeUpdate) => Promise<void>;
  onLock: (id: string, locked: boolean) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onOpenAnalysis?: (analysisId: string) => void;
}

interface EvidenceSelection {
  kind: "osint" | "analysis";
  /** Presente si `kind === "osint"`. */
  reportId?: string;
  pageNumber?: number;
  /** Presente si `kind === "analysis"`. */
  analysisId?: string;
  quote: string;
  label: string;
}

function renderAttributeValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  try {
    return JSON.stringify(value);
  } catch {
    return "[valor no serializable]";
  }
}

/** Relaciones puramente analíticas (análisis humano), no cita de informe. */
function isAnalyticRelation(type: GraphEdgeType): boolean {
  return type === "INVESTIGATES";
}

export default function DossierPanel({
  node,
  edges,
  canEdit,
  canLock,
  canDelete,
  onSave,
  onLock,
  onDelete,
  onOpenAnalysis,
}: DossierPanelProps): JSX.Element {
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null);
  const [editing, setEditing] = useState(false);
  const [draftName, setDraftName] = useState("");
  const [draftAliases, setDraftAliases] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setEvidence(null);
    setEditing(false);
    setError(null);
    setDraftName(node?.canonical_name ?? "");
    setDraftAliases((node?.aliases ?? []).join(", "));
  }, [node]);

  if (!node) {
    return (
      <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-base-700 text-center text-sm text-slate-500">
        Selecciona una entidad en el grafo para abrir su dossier.
      </div>
    );
  }

  const incidentEdges = edges.filter((edge) => edge.from === node.id || edge.to === node.id);
  const investigations = incidentEdges.filter((edge) => edge.type === "INVESTIGATES");
  const attributes = Object.entries(node.attributes ?? {});
  const risk = node.risk_score ?? 0;
  const riskPct = Math.round(risk * 100);
  const locked = node.locked_by_analyst === true;

  const handleSave = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      const update: GraphNodeUpdate = {
        canonical_name: draftName.trim() || undefined,
        aliases: draftAliases
          .split(",")
          .map((alias) => alias.trim())
          .filter((alias) => alias.length > 0),
      };
      await onSave(node.id, update);
      setEditing(false);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo guardar el nodo.");
    } finally {
      setBusy(false);
    }
  };

  const handleLock = async (): Promise<void> => {
    setBusy(true);
    setError(null);
    try {
      await onLock(node.id, !locked);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cambiar el bloqueo.");
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (): Promise<void> => {
    if (!window.confirm(`¿Borrar el nodo «${node.canonical_name ?? node.id}»?`)) return;
    setBusy(true);
    setError(null);
    try {
      await onDelete(node.id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo borrar el nodo.");
      setBusy(false);
    }
  };

  const selectEvidence = (edge: GraphEdge): void => {
    if (isAnalyticRelation(edge.type)) {
      setEvidence({
        kind: "analysis",
        analysisId: edge.from,
        quote: edge.exact_quote,
        label: `Investigación: ${edgeDisplay(edge.type)}`,
      });
    } else {
      setEvidence({
        kind: "osint",
        reportId: edge.source_report_id,
        pageNumber: edge.page_number,
        quote: edge.exact_quote,
        label: `${edgeDisplay(edge.type)} · pág. ${edge.page_number}`,
      });
    }
  };

  return (
    <div className="grid h-full min-h-0 gap-3 lg:grid-cols-[360px_1fr]">
      {/* Panel izquierdo: ficha de entidad */}
      <section
        aria-label="Ficha de entidad"
        className="flex min-h-0 flex-col gap-4 overflow-y-auto rounded-lg border border-base-700 bg-base-900/70 p-4"
      >
        <header className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <span
              className="inline-block rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider"
              style={{ backgroundColor: `${labelColor(node.label)}22`, color: labelColor(node.label) }}
            >
              {labelDisplay(node.label)}
            </span>
            <h2 className="mt-1 truncate text-lg font-semibold text-slate-100">
              {node.canonical_name ?? "Sin nombre canónico"}
            </h2>
            <p className="truncate font-mono text-[10px] text-slate-500">{node.id}</p>
          </div>
          <span
            className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-semibold uppercase ${
              locked
                ? "border-neon-cyan/50 text-neon-cyan"
                : "border-neon-amber/50 text-neon-amber"
            }`}
            title={locked ? "Inmutable (Regla 2)" : "Evidencia IA sin verificar"}
          >
            {locked ? "Bloqueado" : "IA sin verificar"}
          </span>
        </header>

        <div>
          <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-500">Risk score</p>
          <div className="h-2 w-full rounded-full bg-base-700" role="presentation">
            <div
              className="h-2 rounded-full"
              style={{
                width: `${riskPct}%`,
                backgroundColor: risk >= 0.7 ? "#ef4444" : risk >= 0.4 ? "#fbbf24" : "#34d399",
              }}
            />
          </div>
          <p className="mt-1 font-mono text-xs text-slate-400">{risk.toFixed(2)}</p>
        </div>

        {(node.aliases ?? []).length > 0 && (
          <div>
            <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-500">Alias</p>
            <ul className="flex flex-wrap gap-1">
              {(node.aliases ?? []).map((alias) => (
                <li
                  key={alias}
                  className="rounded border border-base-600 bg-base-950 px-1.5 py-0.5 text-[11px] text-slate-300"
                >
                  {alias}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div>
          <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-500">Propiedades</p>
          {attributes.length > 0 ? (
            <dl className="flex flex-col gap-1 text-xs">
              {attributes.map(([key, value]) => (
                <div key={key} className="flex justify-between gap-2 border-b border-base-800 py-1">
                  <dt className="text-slate-500">{key}</dt>
                  <dd className="truncate text-right text-slate-200">{renderAttributeValue(value)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-xs text-slate-500">Sin propiedades adicionales.</p>
          )}
        </div>

        {/* Análisis que la investigan (INVESTIGATES) — SPEC-05 §3 */}
        <div>
          <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-500">
            Investigada por ({investigations.length})
          </p>
          {investigations.length === 0 ? (
            <p className="text-xs text-slate-500">Ningún análisis la investiga todavía.</p>
          ) : (
            <ul className="flex flex-col gap-1">
              {investigations.map((edge) => (
                <li key={edge.id} className="flex items-center justify-between gap-2 text-xs">
                  <span className="truncate font-mono text-slate-400">{edge.from}</span>
                  {onOpenAnalysis && (
                    <button
                      type="button"
                      onClick={() => onOpenAnalysis(edge.from)}
                      className="shrink-0 rounded border border-base-600 px-1.5 py-0.5 text-[10px] text-slate-300 transition hover:border-neon-violet hover:text-neon-violet"
                    >
                      Abrir análisis
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Acciones RBAC (ocultas si el rol no las permite — SPEC-04 §1) */}
        {(canEdit || canLock || canDelete) && (
          <div className="flex flex-col gap-2 border-t border-base-700 pt-3">
            {canLock && (
              <button
                type="button"
                onClick={() => void handleLock()}
                disabled={busy}
                className={`rounded border px-2 py-1 text-xs transition disabled:opacity-40 ${
                  locked
                    ? "border-neon-amber/50 text-neon-amber hover:bg-neon-amber/10"
                    : "border-neon-cyan/50 text-neon-cyan hover:bg-neon-cyan/10"
                }`}
              >
                {locked ? "Desbloquear nodo" : "Bloquear nodo (verificado)"}
              </button>
            )}
            {canEdit && !editing && (
              <button
                type="button"
                onClick={() => setEditing(true)}
                disabled={locked}
                title={locked ? "Nodo inmutable: no editable (Regla 2)" : undefined}
                className="rounded border border-base-600 px-2 py-1 text-xs text-slate-200 transition hover:border-neon-amber hover:text-neon-amber disabled:opacity-40"
              >
                Editar / fusionar
              </button>
            )}
            {canDelete && (
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={busy || locked}
                title={locked ? "Nodo inmutable: no borrable (Regla 2)" : undefined}
                className="rounded border border-neon-crimson/40 px-2 py-1 text-xs text-neon-crimson transition hover:bg-neon-crimson/10 disabled:opacity-40"
              >
                Borrar nodo
              </button>
            )}
          </div>
        )}

        {editing && (
          <form
            className="flex flex-col gap-2 rounded border border-base-700 p-2"
            onSubmit={(event) => {
              event.preventDefault();
              void handleSave();
            }}
          >
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Nombre canónico
              <input
                value={draftName}
                onChange={(event) => setDraftName(event.target.value)}
                className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
              />
            </label>
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Alias (separados por coma)
              <textarea
                value={draftAliases}
                onChange={(event) => setDraftAliases(event.target.value)}
                rows={2}
                className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
              />
            </label>
            <div className="flex gap-2">
              <button
                type="submit"
                disabled={busy}
                className="rounded border border-neon-cyan/50 px-2 py-1 text-xs text-neon-cyan transition hover:bg-neon-cyan/10 disabled:opacity-40"
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
        )}

        {error && (
          <p role="alert" className="rounded border border-neon-crimson/40 bg-neon-crimson/10 p-2 text-[11px] text-neon-crimson">
            {error}
          </p>
        )}

        <div className="border-t border-base-700 pt-3">
          <p className="mb-2 text-[11px] uppercase tracking-widest text-slate-500">
            Relaciones ({incidentEdges.length})
          </p>
          {incidentEdges.length === 0 ? (
            <p className="text-xs text-slate-500">Sin relaciones visibles.</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {incidentEdges.map((edge) => {
                const outgoing = edge.from === node.id;
                const counterpart = outgoing ? edge.to : edge.from;
                return (
                  <li key={edge.id} className="rounded border border-base-700 bg-base-950/60 p-2 text-xs">
                    <p className="text-slate-300">
                      <span className="text-neon-cyan">{outgoing ? "→" : "←"}</span>{" "}
                      {edgeDisplay(edge.type)}{" "}
                      <span className="font-mono text-slate-500">
                        {counterpart.slice(0, 8)}… · conf. {edge.confidence.toFixed(2)}
                      </span>
                    </p>
                    {edge.exact_quote && (
                      <blockquote className="mt-1 border-l-2 border-base-600 pl-2 text-[11px] italic text-slate-400">
                        “{edge.exact_quote}”
                      </blockquote>
                    )}
                    <div className="mt-1 flex items-center justify-between text-[10px] text-slate-500">
                      <span>
                        {formatDate(edge.first_seen)} → {formatDate(edge.last_seen)}
                      </span>
                      <button
                        type="button"
                        onClick={() => selectEvidence(edge)}
                        className="rounded border border-base-600 px-1.5 py-0.5 text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan"
                      >
                        {isAnalyticRelation(edge.type) ? "Ver análisis" : "Ver informe origen"}
                      </button>
                    </div>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </section>

      {/* Panel derecho: informe original o cita de análisis humano */}
      <section className="min-h-0 flex-1">
        {evidence?.kind === "osint" && evidence.reportId ? (
          <ReportViewer
            reportId={evidence.reportId}
            pageNumber={evidence.pageNumber}
            quote={evidence.quote}
          />
        ) : evidence?.kind === "analysis" && evidence.analysisId ? (
          <AnalysisCitationCard
            analysisId={evidence.analysisId}
            quote={evidence.quote}
            note={evidence.label}
            onOpenAnalysis={onOpenAnalysis}
          />
        ) : (
          <div className="flex h-full items-center justify-center rounded-md border border-dashed border-base-700 px-4 text-center text-sm text-slate-500">
            Selecciona «Ver informe origen» (cita OSINT) o «Ver análisis» (cita humana) en una
            relación para inspeccionar su evidencia.
          </div>
        )}
      </section>
    </div>
  );
}
