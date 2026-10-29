"use client";

/**
 * Workspace multi-INT v3.1 (SPEC-03/04).
 *
 * Reúne subgrafo multi-capa, colocalización SIGINT+SOCMINT, señales de evasión
 * de alias, mapa MapLibre y timeline. La misión es explícita porque todas las
 * consultas v3.1 están aisladas por `mission_id`; no se persiste en
 * `localStorage` ningún selector, evidencia ni propuesta.
 */

import type { ReactElement } from "react";
import { useCallback, useMemo, useState } from "react";

import MultiLayerGraph, {
  type MultiIntEdge,
  type MultiIntNode,
} from "@/components/graph/MultiLayerGraph";
import EventTimeline from "@/components/map/EventTimeline";
import MultiIntMap, { type MapPoint } from "@/components/map/MultiIntMap";
import EvidenceInspector from "@/components/evidence/EvidenceInspector";
import { ApiError, api } from "@/services/api";
import type {
  AliasEvasionResponse,
  AliasSignal,
  ColocationEvent,
  ColocationResponse,
  MultiLayerSubgraphResponse,
} from "@/services/api";

export interface MultiIntWorkspaceProps {
  canSeeStaged?: boolean;
  canVerifyEvidence?: boolean;
}

type Discipline = "OSINT" | "SOCMINT" | "SIGINT" | "HUMINT" | "TECHINT" | "FININT" | "GEOINT";

const DISCIPLINES: readonly Discipline[] = [
  "SIGINT",
  "SOCMINT",
  "OSINT",
  "HUMINT",
  "FININT",
  "GEOINT",
  "TECHINT",
];

function isoLocal(date: Date): string {
  const pad = (value: number): string => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(
    date.getHours(),
  )}:${pad(date.getMinutes())}`;
}

function defaultFrom(): string {
  const date = new Date();
  date.setDate(date.getDate() - 7);
  return isoLocal(date);
}

function defaultTo(): string {
  return isoLocal(new Date());
}

function toIso(value: string): string | null {
  if (!value) return null;
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : new Date(parsed).toISOString();
}

function asString(value: unknown): string | undefined {
  return typeof value === "string" ? value : undefined;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" ? value : undefined;
}

function parseNodes(response: MultiLayerSubgraphResponse | null): MultiIntNode[] {
  return (response?.nodes ?? [])
    .map((raw) => ({
      entity_id: asString(raw.entity_id) ?? "",
      canonical_name: asString(raw.canonical_name) ?? "(sin nombre)",
      discipline: asString(raw.primary_discipline),
      confidence: asNumber(raw.confidence),
      tlp: asString(raw.tlp),
      depth: asNumber(raw.depth),
    }))
    .filter((node) => node.entity_id.length > 0);
}

function parseEdges(response: MultiLayerSubgraphResponse | null): MultiIntEdge[] {
  return (response?.edges ?? [])
    .map((raw) => ({
      relationship_id: asString(raw.relationship_id) ?? "",
      source_entity_id: asString(raw.source_entity_id) ?? "",
      target_entity_id: asString(raw.target_entity_id) ?? "",
      relationship_type: asString(raw.relationship_type) ?? "RELATED",
      confidence: asNumber(raw.confidence),
    }))
    .filter((edge) => edge.relationship_id && edge.source_entity_id && edge.target_entity_id);
}

function flattenEvents(response: ColocationResponse | null): ColocationEvent[] {
  return (response?.clusters ?? []).flatMap((cluster) => cluster.events ?? []);
}

export default function MultiIntWorkspace({
  canSeeStaged = false,
  canVerifyEvidence = false,
}: MultiIntWorkspaceProps): ReactElement {
  const [missionId, setMissionId] = useState("");
  const [roots, setRoots] = useState("");
  const [from, setFrom] = useState(defaultFrom);
  const [to, setTo] = useState(defaultTo);
  const [selectedDisciplines, setSelectedDisciplines] = useState<Discipline[]>([
    "SIGINT",
    "SOCMINT",
    "OSINT",
  ]);
  const [includeStaged, setIncludeStaged] = useState(false);
  const [depth, setDepth] = useState(2);

  const [subgraph, setSubgraph] = useState<MultiLayerSubgraphResponse | null>(null);
  const [colocation, setColocation] = useState<ColocationResponse | null>(null);
  const [aliasData, setAliasData] = useState<AliasEvasionResponse | null>(null);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEventId, setSelectedEventId] = useState<string | null>(null);
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const [loading, setLoading] = useState<"graph" | "colocation" | "alias" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [centerLat, setCenterLat] = useState("40.4168");
  const [centerLon, setCenterLon] = useState("-3.7038");
  const [radius, setRadius] = useState(500);

  const nodes = useMemo(() => parseNodes(subgraph), [subgraph]);
  const edges = useMemo(() => parseEdges(subgraph), [subgraph]);
  const clusters = useMemo(() => colocation?.clusters ?? [], [colocation]);
  const events = useMemo(() => flattenEvents(colocation), [colocation]);
  const selectedEvent = useMemo(
    () => events.find((event) => event.event_id === selectedEventId) ?? null,
    [events, selectedEventId],
  );

  const mapPoints = useMemo<MapPoint[]>(() => {
    const points: MapPoint[] = [];
    for (const cluster of clusters) {
      const lat = cluster.center?.lat;
      const lon = cluster.center?.lon;
      if (typeof lat !== "number" || typeof lon !== "number") continue;
      for (const event of cluster.events ?? []) {
        if (!event.event_id) continue;
        points.push({
          id: event.event_id,
          lat,
          lon,
          label: `${event.discipline ?? "?"} · ${event.event_type ?? "event"}`,
          discipline: event.discipline,
          tlp: event.tlp,
          eventTime: event.event_time,
        });
      }
    }
    return points;
  }, [clusters]);

  const windowPayload = useCallback((): { from: string; to: string } | null => {
    const start = toIso(from);
    const end = toIso(to);
    if (!start || !end) {
      setError("La ventana temporal no es válida.");
      return null;
    }
    if (Date.parse(end) <= Date.parse(start)) {
      setError("La fecha final debe ser posterior a la inicial.");
      return null;
    }
    return { from: start, to: end };
  }, [from, to]);

  const uuidRoots = useCallback((): string[] => {
    return roots
      .split(/[\s,;]+/)
      .map((value) => value.trim())
      .filter(Boolean);
  }, [roots]);

  const loadSubgraph = useCallback(async (): Promise<void> => {
    const window = windowPayload();
    const rootIds = uuidRoots();
    if (!window) return;
    if (!missionId.trim()) {
      setError("Indica el UUID de la misión.");
      return;
    }
    if (rootIds.length === 0) {
      setError("Indica al menos un entity_id raíz.");
      return;
    }
    setLoading("graph");
    setError(null);
    try {
      const result = await api.getMultiLayerSubgraph({
        mission_id: missionId.trim(),
        root_ids: rootIds,
        depth,
        direction: "BOTH",
        disciplines: selectedDisciplines,
        include_staged: canSeeStaged && includeStaged,
        max_nodes: 300,
        max_edges: 1200,
      });
      setSubgraph(result);
      setSelectedNodeId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cargar el subgrafo.");
    } finally {
      setLoading(null);
    }
  }, [windowPayload, uuidRoots, missionId, depth, selectedDisciplines, canSeeStaged, includeStaged]);

  const runColocation = useCallback(async (): Promise<void> => {
    const window = windowPayload();
    if (!window) return;
    if (!missionId.trim()) {
      setError("Indica el UUID de la misión.");
      return;
    }
    const lat = Number(centerLat);
    const lon = Number(centerLon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      setError("Centro geográfico inválido.");
      return;
    }
    setLoading("colocation");
    setError(null);
    try {
      const result = await api.findSigintSocmintColocation({
        mission_id: missionId.trim(),
        center: { lat, lon },
        radius_m: radius,
        window,
        disciplines: ["SIGINT", "SOCMINT"],
        min_confidence: 0,
        max_time_delta_s: 900,
        limit: 200,
      });
      setColocation(result);
      setSelectedEventId(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo ejecutar la colocalización.");
    } finally {
      setLoading(null);
    }
  }, [windowPayload, missionId, centerLat, centerLon, radius]);

  const runAlias = useCallback(async (): Promise<void> => {
    const window = windowPayload();
    if (!window) return;
    if (!missionId.trim()) {
      setError("Indica el UUID de la misión.");
      return;
    }
    setLoading("alias");
    setError(null);
    try {
      const result = await api.detectAliasEvasion({
        mission_id: missionId.trim(),
        window,
        min_aliases: 3,
        min_platforms: 2,
        max_gap_hours: 48,
        similarity_threshold: 0.55,
        limit: 100,
      });
      setAliasData(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudieron calcular las señales de alias.");
    } finally {
      setLoading(null);
    }
  }, [windowPayload, missionId]);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto">
      {error && (
        <p role="alert" className="rounded border border-neon-crimson/40 bg-neon-crimson/10 px-3 py-2 text-xs text-neon-crimson">
          {error}
        </p>
      )}

      <section className="rounded-lg border border-base-700 bg-base-900 p-3">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-200">
          Contexto de misión
        </h2>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          <label className="text-[11px] text-slate-400">
            Mission ID
            <input
              value={missionId}
              onChange={(event) => setMissionId(event.target.value)}
              placeholder="UUID de intel.missions"
              className="mt-1 w-full rounded border border-base-700 bg-base-950 px-2 py-1 text-xs text-slate-200 focus:border-neon-cyan focus:outline-none"
            />
          </label>
          <label className="text-[11px] text-slate-400">
            Raíces (entity_id, separadas por coma)
            <input
              value={roots}
              onChange={(event) => setRoots(event.target.value)}
              placeholder="uuid-1, uuid-2"
              className="mt-1 w-full rounded border border-base-700 bg-base-950 px-2 py-1 text-xs text-slate-200 focus:border-neon-cyan focus:outline-none"
            />
          </label>
          <label className="text-[11px] text-slate-400">
            Desde
            <input
              type="datetime-local"
              value={from}
              onChange={(event) => setFrom(event.target.value)}
              className="mt-1 w-full rounded border border-base-700 bg-base-950 px-2 py-1 text-xs text-slate-200 focus:border-neon-cyan focus:outline-none"
            />
          </label>
          <label className="text-[11px] text-slate-400">
            Hasta
            <input
              type="datetime-local"
              value={to}
              onChange={(event) => setTo(event.target.value)}
              className="mt-1 w-full rounded border border-base-700 bg-base-950 px-2 py-1 text-xs text-slate-200 focus:border-neon-cyan focus:outline-none"
            />
          </label>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-3 text-[11px] text-slate-400">
          <label className="flex items-center gap-1">
            Profundidad
            <input
              type="number"
              min={1}
              max={5}
              value={depth}
              onChange={(event) => setDepth(Math.max(1, Math.min(5, Number(event.target.value))))}
              className="w-14 rounded border border-base-700 bg-base-950 px-1 py-0.5"
            />
          </label>
          {DISCIPLINES.map((discipline) => (
            <label key={discipline} className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={selectedDisciplines.includes(discipline)}
                onChange={(event) => {
                  setSelectedDisciplines((current) =>
                    event.target.checked
                      ? [...current, discipline]
                      : current.filter((item) => item !== discipline),
                  );
                }}
              />
              {discipline}
            </label>
          ))}
          {canSeeStaged && (
            <label className="flex items-center gap-1">
              <input
                type="checkbox"
                checked={includeStaged}
                onChange={(event) => setIncludeStaged(event.target.checked)}
              />
              Incluir staged
            </label>
          )}
          <button
            type="button"
            onClick={() => void loadSubgraph()}
            disabled={loading !== null}
            className="rounded border border-neon-cyan/60 px-2 py-1 text-[11px] font-semibold text-neon-cyan transition hover:bg-neon-cyan/10 disabled:opacity-40"
          >
            {loading === "graph" ? "Cargando…" : "Cargar subgrafo"}
          </button>
          <button
            type="button"
            onClick={() => void runAlias()}
            disabled={loading !== null}
            className="rounded border border-violet-400/60 px-2 py-1 text-[11px] font-semibold text-violet-300 transition hover:bg-violet-400/10 disabled:opacity-40"
          >
            {loading === "alias" ? "Calculando…" : "Señales de alias"}
          </button>
        </div>
      </section>

      <div className="grid gap-3 xl:grid-cols-[1.3fr_1fr]">
        <section className="min-w-0 rounded-lg border border-base-700 bg-base-900 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
              Subgrafo multi-capa
            </h2>
            <span className="text-[10px] text-slate-400">
              {nodes.length} nodos · {edges.length} aristas
              {subgraph?.truncated ? " · truncado" : ""}
            </span>
          </div>
          <MultiLayerGraph
            nodes={nodes}
            edges={edges}
            selectedNodeId={selectedNodeId}
            onSelectNode={setSelectedNodeId}
            truncated={subgraph?.truncated}
          />
          {selectedNodeId && (
            <p className="mt-2 break-all font-mono text-[10px] text-slate-400">
              Seleccionado: {selectedNodeId}
            </p>
          )}
        </section>

        <section className="rounded-lg border border-base-700 bg-base-900 p-3">
          <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-200">
            Colocalización SIGINT + SOCMINT
          </h2>
          <div className="grid grid-cols-3 gap-2 text-[11px] text-slate-400">
            <label>
              Lat
              <input
                value={centerLat}
                onChange={(event) => setCenterLat(event.target.value)}
                className="mt-1 w-full rounded border border-base-700 bg-base-950 px-2 py-1 text-xs"
              />
            </label>
            <label>
              Lon
              <input
                value={centerLon}
                onChange={(event) => setCenterLon(event.target.value)}
                className="mt-1 w-full rounded border border-base-700 bg-base-950 px-2 py-1 text-xs"
              />
            </label>
            <label>
              Radio m
              <input
                type="number"
                min={10}
                max={5000}
                value={radius}
                onChange={(event) => setRadius(Number(event.target.value))}
                className="mt-1 w-full rounded border border-base-700 bg-base-950 px-2 py-1 text-xs"
              />
            </label>
          </div>
          <button
            type="button"
            onClick={() => void runColocation()}
            disabled={loading !== null}
            className="mt-2 rounded border border-neon-cyan/60 px-2 py-1 text-[11px] font-semibold text-neon-cyan transition hover:bg-neon-cyan/10 disabled:opacity-40"
          >
            {loading === "colocation" ? "Consultando…" : "Buscar coincidencias"}
          </button>
          <p className="mt-2 text-[10px] text-slate-400">
            {clusters.length} clusters · {events.length} eventos
            {colocation?.suppressed ? ` · ${colocation.suppressed} suprimidos por TLP` : ""}
          </p>
          <MultiIntMap
            points={mapPoints}
            selectedPointId={selectedEventId}
            onSelectPoint={setSelectedEventId}
            heightClassName="mt-2 h-56"
          />
        </section>
      </div>

      <section className="rounded-lg border border-base-700 bg-base-900 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
            Timeline y evidencia
          </h2>
          {selectedEvent?.evidence_ids && selectedEvent.evidence_ids.length > 0 && (
            <span className="text-[10px] text-slate-400">
              Evidencias del evento: {selectedEvent.evidence_ids.length}
            </span>
          )}
        </div>
        <EventTimeline
          events={events}
          selectedEventId={selectedEventId}
          onSelectEvent={setSelectedEventId}
        />
        {selectedEvent && (
          <div className="mt-2 rounded border border-base-700 p-2 text-[11px] text-slate-300">
            <p>
              <span className="text-slate-400">Evento:</span>{" "}
              {selectedEvent.event_type} · {selectedEvent.discipline} ·{" "}
              {selectedEvent.event_time ? new Date(selectedEvent.event_time).toLocaleString() : "—"}
            </p>
            <p>
              <span className="text-slate-400">Selector:</span>{" "}
              {selectedEvent.masked_selector ?? "—"} · <span className="text-slate-400">Confianza:</span>{" "}
              {selectedEvent.confidence ?? "—"}
            </p>
            <div className="mt-1 flex flex-wrap gap-1">
              {(selectedEvent.evidence_ids ?? []).map((id) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setEvidenceId(id)}
                  className="rounded border border-base-600 px-1.5 py-0.5 font-mono text-[9px] hover:border-neon-cyan hover:text-neon-cyan"
                >
                  {id.slice(0, 12)}…
                </button>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="rounded-lg border border-base-700 bg-base-900 p-3">
        <h2 className="mb-2 text-xs font-semibold uppercase tracking-wider text-slate-200">
          Señales de evasión de alias
        </h2>
        {aliasData?.signals && aliasData.signals.length > 0 ? (
          <ul className="space-y-1">
            {aliasData.signals.map((signal: AliasSignal, index: number) => (
              <li
                key={signal.signal_id ?? `${signal.signal_type}-${index}`}
                className="rounded border border-base-700 px-2 py-1.5 text-[11px] text-slate-300"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-semibold text-neon-amber">{signal.signal_type}</span>
                  <span className="text-slate-400">
                    score {Math.round((signal.score ?? 0) * 100)}% · {signal.occurrences ?? 0} ocurrencias
                  </span>
                </div>
                <p className="mt-0.5 text-slate-400">{signal.explanation ?? "—"}</p>
                <p className="mt-0.5 text-[10px] text-slate-400">
                  Aliases: {(signal.aliases ?? []).join(", ") || "—"} · Plataformas:{" "}
                  {(signal.platforms ?? []).join(", ") || "—"}
                </p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-slate-400">
            Sin señales. Ejecuta la detección sobre una misión con historial de alias.
          </p>
        )}
      </section>

      {evidenceId && (
        <div
          role="dialog"
          aria-modal="true"
          aria-label="Inspector de evidencia"
          className="fixed inset-0 z-40 flex items-center justify-center bg-base-950/80 p-4"
        >
          <div className="h-[80vh] w-full max-w-3xl">
            <EvidenceInspector
              evidenceId={evidenceId}
              canVerify={canVerifyEvidence}
              onClose={() => setEvidenceId(null)}
            />
          </div>
        </div>
      )}
    </div>
  );
}
