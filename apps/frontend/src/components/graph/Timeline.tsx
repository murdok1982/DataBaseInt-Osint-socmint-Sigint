"use client";

/**
 * Timeline — línea temporal de inteligencia.
 * Autorizado por: SPEC-05 §2 (filtro por rango temporal `first_seen`/`last_seen`).
 * La ventana seleccionada se traduce a los query params `from`/`to` de
 * `listGraphNodes` y a `min_confidence`/rango de `listGraphEdges`.
 */

export interface TimeWindow {
  from: string;
  to: string;
}

export interface TimelineProps {
  /** Cota inferior del conjunto de datos (ISO). */
  min: string;
  /** Cota superior del conjunto de datos (ISO). */
  max: string;
  value: TimeWindow;
  onChange: (next: TimeWindow) => void;
  disabled?: boolean;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

export default function Timeline({
  min,
  max,
  value,
  onChange,
  disabled = false,
}: TimelineProps): ReactElement {
  const minTime = new Date(min).getTime();
  const maxTime = new Date(max).getTime();
  const span = Math.max(maxTime - minTime, 1);

  const fromTime = value.from ? new Date(value.from).getTime() : minTime;
  const toTime = value.to ? new Date(value.to).getTime() : maxTime;

  const leftPct = clamp(((fromTime - minTime) / span) * 100, 0, 100);
  const rightPct = clamp(((toTime - minTime) / span) * 100, 0, 100);

  return (
    <section
      aria-label="Línea temporal de inteligencia"
      className="flex flex-col gap-2 rounded-lg border border-base-700 bg-base-900/70 p-4"
    >
      <div className="flex items-center justify-between text-xs text-slate-400">
        <span className="font-semibold uppercase tracking-widest">Línea temporal</span>
        <span className="font-mono text-slate-400">
          {minTime > 0 ? min.slice(0, 10) : "—"} → {maxTime > 0 ? max.slice(0, 10) : "—"}
        </span>
      </div>

      <div
        className="relative h-2 rounded-full bg-base-700"
        role="presentation"
        aria-hidden="true"
      >
        <div
          className="absolute h-2 rounded-full bg-gradient-to-r from-neon-cyan/70 to-neon-violet/70"
          style={{ left: `${leftPct}%`, right: `${100 - rightPct}%` }}
        />
      </div>

      <div className="grid grid-cols-2 gap-2">
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Inicio
          <input
            type="date"
            value={value.from ? value.from.slice(0, 10) : ""}
            min={min ? min.slice(0, 10) : undefined}
            max={max ? max.slice(0, 10) : undefined}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, from: event.target.value })}
            className="rounded border border-base-600 bg-base-950 px-2 py-1.5 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none disabled:opacity-40"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-slate-400">
          Fin
          <input
            type="date"
            value={value.to ? value.to.slice(0, 10) : ""}
            min={min ? min.slice(0, 10) : undefined}
            max={max ? max.slice(0, 10) : undefined}
            disabled={disabled}
            onChange={(event) => onChange({ ...value, to: event.target.value })}
            className="rounded border border-base-600 bg-base-950 px-2 py-1.5 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none disabled:opacity-40"
          />
        </label>
      </div>
    </section>
  );
}import type { ReactElement } from "react";

