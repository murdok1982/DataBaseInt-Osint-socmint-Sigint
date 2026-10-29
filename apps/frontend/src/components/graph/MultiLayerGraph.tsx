"use client";

/**
 * Subgrafo multi-capa v3.1 (SPEC-03 §4).
 *
 * Vista de apoyo al grafo principal: no usa Cytoscape porque el contrato
 * devuelve nodos/aristas heterogéneos con presupuesto y `truncated`. Un layout
 * circular determinista permite leer el resultado sin introducir física ni
 * dependencias nuevas; el grafo operativo sigue en `GraphCanvas`.
 */

import type { ReactElement } from "react";
import { useMemo } from "react";

export interface MultiIntNode {
  entity_id: string;
  canonical_name: string;
  discipline?: string;
  confidence?: number;
  tlp?: string;
  depth?: number;
}

export interface MultiIntEdge {
  relationship_id: string;
  source_entity_id: string;
  target_entity_id: string;
  relationship_type: string;
  confidence?: number;
}

export interface MultiLayerGraphProps {
  nodes: MultiIntNode[];
  edges: MultiIntEdge[];
  selectedNodeId?: string | null;
  onSelectNode?: (nodeId: string) => void;
  truncated?: boolean;
}

const WIDTH = 720;
const HEIGHT = 420;
const CENTER_X = WIDTH / 2;
const CENTER_Y = HEIGHT / 2;
const RADIUS = Math.min(WIDTH, HEIGHT) * 0.36;

export default function MultiLayerGraph({
  nodes,
  edges,
  selectedNodeId = null,
  onSelectNode,
  truncated = false,
}: MultiLayerGraphProps): ReactElement {
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    const count = nodes.length;
    nodes.forEach((node, index) => {
      if (count === 1) {
        map.set(node.entity_id, { x: CENTER_X, y: CENTER_Y });
        return;
      }
      const angle = (2 * Math.PI * index) / count - Math.PI / 2;
      map.set(node.entity_id, {
        x: CENTER_X + RADIUS * Math.cos(angle),
        y: CENTER_Y + RADIUS * Math.sin(angle),
      });
    });
    return map;
  }, [nodes]);

  if (nodes.length === 0) {
    return (
      <div className="flex h-72 items-center justify-center rounded border border-base-700 bg-base-950/40 text-xs text-slate-400">
        Sin nodos para los filtros seleccionados.
      </div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded border border-base-700 bg-base-950/60">
      {truncated && (
        <p className="absolute right-2 top-2 z-10 rounded border border-neon-amber/40 bg-base-900/90 px-2 py-0.5 text-[10px] text-neon-amber">
          Resultado truncado por presupuesto
        </p>
      )}
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        role="img"
        aria-label={`Subgrafo multi-INT con ${nodes.length} nodos y ${edges.length} relaciones`}
        className="h-80 w-full"
      >
        <g>
          {edges.map((edge) => {
            const from = positions.get(edge.source_entity_id);
            const to = positions.get(edge.target_entity_id);
            if (!from || !to) return null;
            const selected =
              edge.source_entity_id === selectedNodeId || edge.target_entity_id === selectedNodeId;
            return (
              <g key={edge.relationship_id}>
                <line
                  x1={from.x}
                  y1={from.y}
                  x2={to.x}
                  y2={to.y}
                  stroke={selected ? "#22d3ee" : "#475569"}
                  strokeWidth={selected ? 2 : 1}
                  strokeOpacity={selected ? 0.9 : 0.55}
                />
                <text
                  x={(from.x + to.x) / 2}
                  y={(from.y + to.y) / 2 - 3}
                  textAnchor="middle"
                  fontSize={7}
                  fill="#64748b"
                >
                  {edge.relationship_type}
                </text>
              </g>
            );
          })}
        </g>
        <g>
          {nodes.map((node) => {
            const pos = positions.get(node.entity_id);
            if (!pos) return null;
            const selected = node.entity_id === selectedNodeId;
            const color =
              node.discipline === "SIGINT"
                ? "#fbbf24"
                : node.discipline === "SOCMINT"
                  ? "#22d3ee"
                  : node.discipline === "OSINT"
                    ? "#34d399"
                    : "#a78bfa";
            return (
              <g
                key={node.entity_id}
                role="button"
                tabIndex={0}
                aria-label={`${node.canonical_name} (${node.discipline ?? "MIXED"})`}
                aria-pressed={selected}
                onClick={() => onSelectNode?.(node.entity_id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onSelectNode?.(node.entity_id);
                  }
                }}
                className="cursor-pointer"
              >
                <circle
                  cx={pos.x}
                  cy={pos.y}
                  r={selected ? 14 : 10}
                  fill={color}
                  fillOpacity={selected ? 0.95 : 0.7}
                  stroke={selected ? "#f8fafc" : "#020617"}
                  strokeWidth={selected ? 2.5 : 1}
                />
                <text
                  x={pos.x}
                  y={pos.y + 24}
                  textAnchor="middle"
                  fontSize={9}
                  fill="#cbd5e1"
                >
                  {node.canonical_name.length > 20
                    ? `${node.canonical_name.slice(0, 19)}…`
                    : node.canonical_name}
                </text>
                <text
                  x={pos.x}
                  y={pos.y + 34}
                  textAnchor="middle"
                  fontSize={7}
                  fill="#64748b"
                >
                  {node.discipline ?? "—"} · {node.confidence ?? 0}
                </text>
              </g>
            );
          })}
        </g>
      </svg>
      <div className="flex flex-wrap gap-3 border-t border-base-700 px-3 py-1.5 text-[10px] text-slate-400">
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-neon-amber" />SIGINT</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-neon-cyan" />SOCMINT</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-400" />OSINT</span>
        <span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-violet-400" />Otra/HUMINT</span>
      </div>
    </div>
  );
}
