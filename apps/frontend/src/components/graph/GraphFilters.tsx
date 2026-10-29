"use client";

/**
 * GraphFilters — filtros del grafo (Vista 1).
 *
 * Autorizado por: SPEC-05 §2 (filtros por `label`, `min_confidence` y búsqueda
 * de texto; el rango temporal lo aporta `Timeline`).
 * Traza a: `listGraphNodes` (query `label`, `q`, `min_confidence`) y
 * `listGraphEdges` (query `min_confidence`).
 *
 * Nota: el parámetro `q` (full-text) existe en el contrato v2.0.0
 * (`GET /graph/nodes?q=`), resolviendo el antiguo SPEC-GAP de búsqueda
 * estructurada.
 */

import type { ReactElement } from "react";
import { ANALYTIC_NODE_LABELS, ENTITY_LABELS, LABEL_META, OSINT_NODE_LABELS } from "@/lib/labels";
import type { EntityLabel } from "@/services/api";

export interface GraphFilterState {
  label: EntityLabel | "";
  /** Umbral de confianza 0..1 (aplica a nodos y aristas). */
  minConfidence: number;
  /** Búsqueda de texto libre (full-text sobre nombre/alias/valor). */
  q: string;
}

export const DEFAULT_FILTERS: GraphFilterState = {
  label: "",
  minConfidence: 0,
  q: "",
};

export interface GraphFiltersProps {
  value: GraphFilterState;
  onChange: (next: GraphFilterState) => void;
  onReset: () => void;
  disabled?: boolean;
  resultCount?: number;
}

export default function GraphFilters({
  value,
  onChange,
  onReset,
  disabled = false,
  resultCount,
}: GraphFiltersProps): ReactElement {
  return (
    <section
      aria-label="Filtros del grafo"
      className="flex flex-col gap-4 rounded-lg border border-base-700 bg-base-900/70 p-4"
    >
      <header className="flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
          Filtros tácticos
        </h2>
        <button
          type="button"
          onClick={onReset}
          disabled={disabled}
          className="rounded border border-base-600 px-2 py-0.5 text-[11px] text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan disabled:opacity-40"
        >
          Reiniciar
        </button>
      </header>

      <label className="flex flex-col gap-1 text-xs text-slate-400">
        Búsqueda de texto (nombre / alias / valor)
        <input
          type="search"
          value={value.q}
          disabled={disabled}
          placeholder="p. ej. 185.220.101.4, alias…"
          onChange={(event) => onChange({ ...value, q: event.target.value })}
          className="rounded border border-base-600 bg-base-950 px-2 py-1.5 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none disabled:opacity-40"
        />
      </label>

      <label className="flex flex-col gap-1 text-xs text-slate-400">
        Tipo de entidad (label)
        <select
          value={value.label}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, label: event.target.value as EntityLabel | "" })}
          className="rounded border border-base-600 bg-base-950 px-2 py-1.5 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none disabled:opacity-40"
        >
          <option value="">Todas</option>
          <optgroup label="OSINT">
            {OSINT_NODE_LABELS.map((label) => (
              <option key={label} value={label}>
                {LABEL_META[label].display}
              </option>
            ))}
          </optgroup>
          <optgroup label="Analítico">
            {ANALYTIC_NODE_LABELS.map((label) => (
              <option key={label} value={label}>
                {LABEL_META[label].display}
              </option>
            ))}
          </optgroup>
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-slate-400">
        <span className="flex items-center justify-between">
          Confianza mínima
          <span className="font-mono text-neon-cyan">{value.minConfidence.toFixed(2)}</span>
        </span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={value.minConfidence}
          disabled={disabled}
          onChange={(event) => onChange({ ...value, minConfidence: Number(event.target.value) })}
          className="accent-neon-cyan disabled:opacity-40"
          aria-valuemin={0}
          aria-valuemax={1}
          aria-valuenow={value.minConfidence}
        />
      </label>

      <p className="text-[10px] uppercase tracking-widest text-slate-400">
        {ENTITY_LABELS.length} labels del contrato
      </p>

      {typeof resultCount === "number" && (
        <p className="text-[11px] text-slate-400">
          {resultCount} nodo{resultCount === 1 ? "" : "s"} en pantalla
        </p>
      )}
    </section>
  );
}
