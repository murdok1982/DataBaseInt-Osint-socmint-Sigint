"use client";

/**
 * Timeline espacial (SPEC-04 §3.7).
 *
 * Sincroniza el evento seleccionado con el mapa y la tabla de evidencia. Sólo
 * muestra eventos ya filtrados por TLP en el backend; nunca inventa un evento
 * `TLP:RED` para un rol sin clearance.
 */

import type { ReactElement } from "react";
import { useMemo } from "react";

import type { ColocationEvent } from "@/services/api";

export interface EventTimelineProps {
  events: ColocationEvent[];
  selectedEventId?: string | null;
  onSelectEvent?: (eventId: string) => void;
}

function time(value: string | undefined): number | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}

const DISCIPLINE_COLOR: Record<string, string> = {
  SIGINT: "#fbbf24",
  SOCMINT: "#22d3ee",
  OSINT: "#34d399",
};

export default function EventTimeline({
  events,
  selectedEventId = null,
  onSelectEvent,
}: EventTimelineProps): ReactElement {
  const bounds = useMemo(() => {
    const times = events.map((event) => time(event.event_time)).filter((value): value is number => value !== null);
    if (times.length === 0) return null;
    return { min: Math.min(...times), max: Math.max(...times) };
  }, [events]);

  if (!bounds) {
    return (
      <div className="rounded border border-base-700 bg-base-950/50 p-3 text-xs text-slate-400">
        Sin eventos con fecha en esta consulta.
      </div>
    );
  }

  const span = Math.max(bounds.max - bounds.min, 1);

  return (
    <div className="rounded border border-base-700 bg-base-950/50 p-3">
      <div className="mb-2 flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-400">
        <span>{new Date(bounds.min).toLocaleString()}</span>
        <span>Timeline de eventos</span>
        <span>{new Date(bounds.max).toLocaleString()}</span>
      </div>
      <div className="relative h-20 border-t border-base-700">
        {events.map((event) => {
          const at = time(event.event_time);
          if (at === null) return null;
          const left = ((at - bounds.min) / span) * 100;
          const selected = event.event_id === selectedEventId;
          const color = DISCIPLINE_COLOR[event.discipline ?? ""] ?? "#94a3b8";
          return (
            <button
              key={event.event_id}
              type="button"
              onClick={() => event.event_id && onSelectEvent?.(event.event_id)}
              title={`${event.discipline ?? "?"} · ${event.event_type ?? "evento"} · ${new Date(at).toLocaleString()}`}
              aria-label={`Evento ${event.event_type ?? ""} de ${event.discipline ?? "disciplina desconocida"}`}
              aria-pressed={selected}
              className="absolute top-0 flex h-full -translate-x-1/2 flex-col items-center justify-start pt-1"
              style={{ left: `${Math.min(Math.max(left, 0), 100)}%` }}
            >
              <span
                className="block w-[2px] flex-1"
                style={{ backgroundColor: color, opacity: selected ? 1 : 0.35 }}
              />
              <span
                className="mt-1 h-2.5 w-2.5 rounded-full border"
                style={{
                  backgroundColor: color,
                  borderColor: selected ? "#f8fafc" : "#020617",
                  boxShadow: selected ? "0 0 0 2px rgba(34,211,238,.55)" : undefined,
                }}
              />
              <span className="mt-0.5 max-w-[74px] truncate text-[9px] text-slate-400">
                {event.event_type ?? event.discipline ?? "event"}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
