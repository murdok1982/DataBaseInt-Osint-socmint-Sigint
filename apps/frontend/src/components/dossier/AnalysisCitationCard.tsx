"use client";

/**
 * AnalysisCitationCard — evidencia de **análisis humano**.
 *
 * Autorizado por: SPEC-05 §3 (distingue cita de informe OSINT de cita de
 * análisis humano). Un análisis humano no tiene informe original ni PDF: se
 * referencia por `analysis_id` (GraphRAG `citations[].analysis_id`, Regla 4).
 */

export interface AnalysisCitationCardProps {
  analysisId: string;
  quote: string;
  /** Texto opcional (p. ej. `new_angle_summary` / `conflict_detail`). */
  note?: string;
  onOpenAnalysis?: (analysisId: string) => void;
}

export default function AnalysisCitationCard({
  analysisId,
  quote,
  note,
  onOpenAnalysis,
}: AnalysisCitationCardProps): ReactElement {
  return (
    <div className="rounded-md border border-neon-violet/40 bg-base-950/60 p-3">
      <p className="mb-2 flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-neon-violet">
        <span className="rounded border border-neon-violet/50 px-1.5 py-0.5">
          Cita de análisis humano
        </span>
      </p>
      {quote && (
        <blockquote className="border-l-2 border-neon-violet/50 pl-2 text-sm italic text-slate-300">
          “{quote}”
        </blockquote>
      )}
      {note && <p className="mt-1 text-xs text-slate-400">{note}</p>}
      <div className="mt-2 flex items-center justify-between text-[11px] text-slate-500">
        <span className="font-mono">{analysisId}</span>
        {onOpenAnalysis && (
          <button
            type="button"
            onClick={() => onOpenAnalysis(analysisId)}
            className="rounded border border-base-600 px-1.5 py-0.5 text-slate-300 transition hover:border-neon-violet hover:text-neon-violet"
          >
            Abrir análisis
          </button>
        )}
      </div>
    </div>
  );
}import type { ReactElement } from "react";

