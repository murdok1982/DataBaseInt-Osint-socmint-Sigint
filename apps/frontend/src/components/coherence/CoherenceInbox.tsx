"use client";

/**
 * CoherenceInbox — Bandeja de Coherencia (Vista 4).
 *
 * Autorizado por: SPEC-05 §5 — lista `GET /coherence/alerts` (`PENDING_ANALYST`
 * primero); cada alerta muestra `alert_type`, `description`, el `Analysis`
 * afectado y la entidad implicada; botones Aprobar / Descartar
 * (`PATCH /coherence/alerts/{alert_id}/resolve`). Una `CONTRADICTION` muestra la
 * discrepancia **sin alterar la tesis** (Regla 9).
 *
 * Traza a: `listCoherenceAlerts`, `resolveCoherenceAlert` y, para el análisis
 * afectado, `getAnalysis`.
 *
 * SPEC-GAP-5: el contrato v2.0.0 no expone `conflict_detail` en `CoherenceAlert`
 * (vive en la arista `[:CONTRADICTS]`, SPEC-01 §3.2, no consultable por la API).
 * Se muestra por tanto `description` como discrepancia, sin inventar campos.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { formatDate } from "@/lib/labels";
import { api, ApiError } from "@/services/api";
import type {
  Analysis,
  CoherenceAlert,
  CoherenceAlertType,
  CoherenceStatus,
} from "@/services/api";

export interface CoherenceInboxProps {
  canResolve: boolean;
  onOpenAnalysis?: (analysisId: string) => void;
  onOpenEntity?: (entityId: string) => void;
  onChanged?: () => void;
}

const ALERT_TYPE_LABEL: Record<CoherenceAlertType, string> = {
  CONTRADICTION: "Contradicción",
  HIGH_VALUE_CORROBORATION: "Corroboración de alto valor",
  NEW_ALIAS_MERGE: "Fusión de alias",
};

const STATUS_LABEL: Record<CoherenceStatus, string> = {
  PENDING_ANALYST: "Pendiente del analista",
  RESOLVED: "Resuelta",
  DISMISSED: "Descartada",
};

const STATUS_STYLE: Record<CoherenceStatus, string> = {
  PENDING_ANALYST: "border-neon-amber/50 text-neon-amber",
  RESOLVED: "border-neon-emerald/50 text-neon-emerald",
  DISMISSED: "border-base-600 text-slate-400",
};

const STATUS_FILTERS: readonly (CoherenceStatus | "")[] = [
  "",
  "PENDING_ANALYST",
  "RESOLVED",
  "DISMISSED",
];

function sortAlerts(alerts: CoherenceAlert[]): CoherenceAlert[] {
  return [...alerts].sort((a, b) => {
    const pendingA = a.status === "PENDING_ANALYST" ? 0 : 1;
    const pendingB = b.status === "PENDING_ANALYST" ? 0 : 1;
    if (pendingA !== pendingB) return pendingA - pendingB;
    return Date.parse(b.created_at ?? "") - Date.parse(a.created_at ?? "");
  });
}

export default function CoherenceInbox({
  canResolve,
  onOpenAnalysis,
  onOpenEntity,
  onChanged,
}: CoherenceInboxProps): JSX.Element {
  const [alerts, setAlerts] = useState<CoherenceAlert[]>([]);
  const [statusFilter, setStatusFilter] = useState<CoherenceStatus | "">("");
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});
  const [analysisCache, setAnalysisCache] = useState<Record<string, Analysis>>({});

  const load = useCallback(async (status: CoherenceStatus | ""): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const list = await api.listCoherenceAlerts(status ? { status } : {});
      setAlerts(sortAlerts(list.items ?? []));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cargar la bandeja de coherencia.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(statusFilter);
  }, [load, statusFilter]);

  // Resuelve los `code` de los análisis afectados (para mostrar contexto).
  useEffect(() => {
    const ids = Array.from(
      new Set(
        alerts
          .map((alert) => alert.analysis_id)
          .filter((id): id is string => typeof id === "string" && id.length > 0),
      ),
    ).filter((id) => !analysisCache[id]);
    if (ids.length === 0) return;

    let active = true;
    (async () => {
      const results = await Promise.allSettled(ids.map((id) => api.getAnalysis(id)));
      if (!active) return;
      setAnalysisCache((previous) => {
        const next = { ...previous };
        results.forEach((result, index) => {
          if (result.status === "fulfilled") next[ids[index]] = result.value;
        });
        return next;
      });
    })();
    return () => {
      active = false;
    };
    // `analysisCache` se omite a propósito para evitar bucles.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [alerts]);

  const handleResolve = async (alert: CoherenceAlert, status: "RESOLVED" | "DISMISSED"): Promise<void> => {
    if (!alert.id) return;
    setBusyId(alert.id);
    setError(null);
    try {
      const updated = await api.resolveCoherenceAlert(alert.id, {
        status,
        resolution_note: notes[alert.id]?.trim() || undefined,
      });
      setAlerts((previous) => sortAlerts(previous.map((item) => (item.id === alert.id ? updated : item))));
      onChanged?.();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo resolver la alerta.");
    } finally {
      setBusyId(null);
    }
  };

  const pendingCount = useMemo(
    () => alerts.filter((alert) => alert.status === "PENDING_ANALYST").length,
    [alerts],
  );

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <header className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-base-700 bg-base-900/70 p-4">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
            Bandeja de coherencia
          </h2>
          <p className="text-[11px] text-slate-500">
            {pendingCount} pendiente{pendingCount === 1 ? "" : "s"} del analista
          </p>
        </div>
        <label className="flex items-center gap-2 text-[11px] text-slate-400">
          Estado
          <select
            value={statusFilter}
            onChange={(event) => setStatusFilter(event.target.value as CoherenceStatus | "")}
            className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
          >
            {STATUS_FILTERS.map((status) => (
              <option key={status || "ALL"} value={status}>
                {status ? STATUS_LABEL[status] : "Todas"}
              </option>
            ))}
          </select>
        </label>
      </header>

      {error && (
        <p role="alert" className="rounded border border-neon-crimson/40 bg-neon-crimson/10 p-2 text-xs text-neon-crimson">
          {error}
        </p>
      )}

      {loading ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
          Cargando alertas…
        </div>
      ) : alerts.length === 0 ? (
        <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed border-base-700 text-center text-sm text-slate-500">
          Sin alertas para el filtro seleccionado.
        </div>
      ) : (
        <ol className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
          {alerts.map((alert) => {
            const alertType = alert.alert_type;
            const status = alert.status ?? "PENDING_ANALYST";
            const isContradiction = alertType === "CONTRADICTION";
            const affected = alert.analysis_id ? analysisCache[alert.analysis_id] : undefined;
            const busy = busyId === alert.id;
            return (
              <li
                key={alert.id}
                className={`rounded-lg border bg-base-900/70 p-4 ${
                  isContradiction && status === "PENDING_ANALYST"
                    ? "border-neon-crimson/50"
                    : "border-base-700"
                }`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0">
                    <span
                      className={`inline-block rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${
                        isContradiction
                          ? "border-neon-crimson/50 text-neon-crimson"
                          : "border-base-600 text-slate-300"
                      }`}
                    >
                      {alertType ? ALERT_TYPE_LABEL[alertType] : "Alerta"}
                    </span>
                    <p className="mt-1 font-mono text-[10px] text-slate-500">
                      {alert.id} · {formatDate(alert.created_at)}
                    </p>
                  </div>
                  <span
                    className={`shrink-0 rounded border px-2 py-0.5 text-[10px] uppercase ${STATUS_STYLE[status]}`}
                  >
                    {STATUS_LABEL[status]}
                  </span>
                </div>

                {/* Discrepancia (CONTRADICTION) — sin alterar la tesis (Regla 9) */}
                <div className="mt-3 rounded border border-base-700 bg-base-950/60 p-3">
                  <p className="mb-1 text-[10px] uppercase tracking-widest text-slate-500">
                    {isContradiction ? "Discrepancia detectada" : "Descripción"}
                  </p>
                  <p className="text-sm text-slate-200">{alert.description ?? "—"}</p>
                  {isContradiction && (
                    <p className="mt-2 text-[10px] text-slate-500">
                      La tesis y `conclusion_body` del analista permanecen intactos (Regla 9).
                      El `conflict_detail` de la arista `[:CONTRADICTS]` no es consultable vía
                      API (SPEC-GAP-5).
                    </p>
                  )}
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
                  <span>
                    Análisis:{" "}
                    {alert.analysis_id ? (
                      <button
                        type="button"
                        onClick={() => alert.analysis_id && onOpenAnalysis?.(alert.analysis_id)}
                        className="text-neon-violet underline-offset-2 hover:underline"
                      >
                        {affected?.code ?? affected?.title ?? alert.analysis_id}
                      </button>
                    ) : (
                      "—"
                    )}
                  </span>
                  <span>
                    Entidad:{" "}
                    {alert.entity_id ? (
                      <button
                        type="button"
                        onClick={() => alert.entity_id && onOpenEntity?.(alert.entity_id)}
                        className="font-mono text-neon-cyan underline-offset-2 hover:underline"
                      >
                        {alert.entity_id}
                      </button>
                    ) : (
                      "—"
                    )}
                  </span>
                </div>

                {canResolve && status === "PENDING_ANALYST" && (
                  <div className="mt-3 flex flex-col gap-2 border-t border-base-700 pt-3">
                    <input
                      value={notes[alert.id ?? ""] ?? ""}
                      onChange={(event) =>
                        setNotes((previous) => ({ ...previous, [alert.id ?? ""]: event.target.value }))
                      }
                      placeholder="Nota de resolución (opcional)"
                      className="rounded border border-base-600 bg-base-950 px-2 py-1 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
                    />
                    <div className="flex gap-2">
                      <button
                        type="button"
                        onClick={() => void handleResolve(alert, "RESOLVED")}
                        disabled={busy}
                        className="rounded border border-neon-emerald/50 px-2 py-1 text-xs text-neon-emerald transition hover:bg-neon-emerald/10 disabled:opacity-40"
                      >
                        Aprobar
                      </button>
                      <button
                        type="button"
                        onClick={() => void handleResolve(alert, "DISMISSED")}
                        disabled={busy}
                        className="rounded border border-base-600 px-2 py-1 text-xs text-slate-300 transition hover:border-neon-crimson hover:text-neon-crimson disabled:opacity-40"
                      >
                        Descartar
                      </button>
                    </div>
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
