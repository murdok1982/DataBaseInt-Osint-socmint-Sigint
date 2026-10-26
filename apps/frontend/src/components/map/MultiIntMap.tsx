"use client";

/**
 * Mapa multi-INT (SPEC-04 §3.4, decisión DEC-E: MapLibre GL).
 *
 * No carga tiles externos por defecto: el estilo es un fondo oscuro local y
 * los puntos vienen de la consulta de colocalización. Así no se filtra a un
 * proveedor externo qué zonas consulta el analista. Un despliegue puede
 * inyectar un estilo propio con `NEXT_PUBLIC_MAP_STYLE_URL`.
 */

import type { ReactElement } from "react";
import { useEffect, useRef, useState } from "react";

export interface MapPoint {
  id: string;
  lat: number;
  lon: number;
  label: string;
  discipline?: string;
  tlp?: string;
  eventTime?: string;
}

export interface MultiIntMapProps {
  points: MapPoint[];
  selectedPointId?: string | null;
  onSelectPoint?: (pointId: string) => void;
  heightClassName?: string;
}

const EMPTY_STYLE = {
  version: 8 as const,
  sources: {},
  layers: [
    {
      id: "background",
      type: "background" as const,
      paint: { "background-color": "#070b14" },
    },
  ],
};

function toGeoJson(points: MapPoint[], selectedPointId: string | null | undefined) {
  return {
    type: "FeatureCollection" as const,
    features: points.map((point) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [point.lon, point.lat] },
      properties: {
        id: point.id,
        label: point.label,
        discipline: point.discipline ?? "UNKNOWN",
        tlp: point.tlp ?? "",
        eventTime: point.eventTime ?? "",
        selected: point.id === selectedPointId,
      },
    })),
  };
}

export default function MultiIntMap({
  points,
  selectedPointId = null,
  onSelectPoint,
  heightClassName = "h-72",
}: MultiIntMapProps): ReactElement {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<import("maplibre-gl").Map | null>(null);
  const onSelectRef = useRef(onSelectPoint);
  onSelectRef.current = onSelectPoint;
  const pointsRef = useRef(points);
  pointsRef.current = points;
  const selectedRef = useRef(selectedPointId);
  selectedRef.current = selectedPointId;
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let disposed = false;
    (async () => {
      try {
        const maplibregl = await import("maplibre-gl");
        if (disposed || !containerRef.current) return;
        const styleUrl = process.env.NEXT_PUBLIC_MAP_STYLE_URL;
        const map = new maplibregl.Map({
          container: containerRef.current,
          style: styleUrl || EMPTY_STYLE,
          center: [0, 20],
          zoom: 1.6,
          attributionControl: false,
        });
        mapRef.current = map;

        map.on("load", () => {
          if (disposed) return;
          map.addSource("multiint-points", {
            type: "geojson",
            data: toGeoJson(pointsRef.current, selectedRef.current),
          });
          map.addLayer({
            id: "multiint-halo",
            type: "circle",
            source: "multiint-points",
            paint: {
              "circle-radius": ["case", ["get", "selected"], 14, 0],
              "circle-color": "#22d3ee",
              "circle-opacity": 0.18,
            },
          });
          map.addLayer({
            id: "multiint-points",
            type: "circle",
            source: "multiint-points",
            paint: {
              "circle-radius": ["case", ["get", "selected"], 8, 5.5],
              "circle-color": [
                "match",
                ["get", "discipline"],
                "SIGINT",
                "#fbbf24",
                "SOCMINT",
                "#22d3ee",
                "OSINT",
                "#34d399",
                "HUMINT",
                "#a78bfa",
                "GEOINT",
                "#38bdf8",
                "#94a3b8",
              ],
              "circle-stroke-width": 1,
              "circle-stroke-color": "#e2e8f0",
            },
          });
          map.addLayer({
            id: "multiint-labels",
            type: "symbol",
            source: "multiint-points",
            layout: {
              "text-field": ["get", "label"],
              "text-size": 10,
              "text-offset": [0, 1.1],
              "text-anchor": "top",
            },
            paint: {
              "text-color": "#cbd5e1",
              "text-halo-color": "#020617",
              "text-halo-width": 1,
            },
          });
          map.on("click", "multiint-points", (event) => {
            const id = event.features?.[0]?.properties?.id;
            if (typeof id === "string") onSelectRef.current?.(id);
          });
          map.on("mouseenter", "multiint-points", () => {
            if (containerRef.current) containerRef.current.style.cursor = "pointer";
          });
          map.on("mouseleave", "multiint-points", () => {
            if (containerRef.current) containerRef.current.style.cursor = "";
          });
          setReady(true);
        });
      } catch {
        if (!disposed) setError("No se pudo inicializar MapLibre.");
      }
    })();
    return () => {
      disposed = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !ready) return;
    const source = map.getSource("multiint-points") as
      | import("maplibre-gl").GeoJSONSource
      | undefined;
    source?.setData(toGeoJson(points, selectedPointId));
    if (points.length > 0) {
      const lons = points.map((point) => point.lon);
      const lats = points.map((point) => point.lat);
      const bounds: [[number, number], [number, number]] = [
        [Math.min(...lons), Math.min(...lats)],
        [Math.max(...lons), Math.max(...lats)],
      ];
      if (bounds[0][0] === bounds[1][0] && bounds[0][1] === bounds[1][1]) {
        map.easeTo({ center: [bounds[0][0], bounds[0][1]], zoom: 9, duration: 300 });
      } else {
        map.fitBounds(bounds, { padding: 32, maxZoom: 12, duration: 300 });
      }
    }
  }, [points, selectedPointId, ready]);

  return (
    <div className={`relative ${heightClassName} overflow-hidden rounded border border-base-700`}>
      <div ref={containerRef} className="h-full w-full" aria-label="Mapa de eventos multi-INT" />
      {!ready && !error && (
        <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-xs text-slate-500">
          Cargando mapa…
        </p>
      )}
      {error && (
        <p role="alert" className="absolute inset-0 flex items-center justify-center p-3 text-xs text-neon-crimson">
          {error}
        </p>
      )}
      {points.length === 0 && ready && (
        <p className="pointer-events-none absolute inset-x-0 bottom-2 text-center text-[10px] text-slate-500">
          Sin puntos georreferenciados en esta consulta.
        </p>
      )}
    </div>
  );
}
