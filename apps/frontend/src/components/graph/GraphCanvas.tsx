"use client";

/**
 * GraphCanvas — visualizador de red con Cytoscape (Vista 1).
 *
 * Autorizado por: SPEC-05 §2 — motor **Cytoscape**; estilo por `label`;
 * diferenciación visual analista vs. IA:
 *   · `locked_by_analyst: true`      → borde **sólido** (verificado por analista)
 *   · evidencia de IA sin verificar  → borde **punteado**
 *   · contradicción                  → **rojo**
 * Selección → Dossier (Vista 2); doble clic → expansión de vecindad.
 * Traza a: `listGraphNodes`, `listGraphEdges`, `getGraphNeighborhood`.
 *
 * Cytoscape se importa de forma dinámica dentro del efecto para no romper el
 * render en servidor (SSR) de Next.js App Router.
 */

import { useEffect, useRef, useState } from "react";
import type { Core, ElementDefinition } from "cytoscape";

import { ENTITY_LABELS, LABEL_META, edgeTrustState, nodeTrustState } from "@/lib/labels";
import type { GraphEdge, GraphNode } from "@/services/api";

export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selectedNodeId?: string | null;
  /** Ids de nodos/aristas a resaltar (p. ej. la ruta crítica de una respuesta GraphRAG). */
  highlightElementIds?: readonly string[];
  /** Ids de nodos en contradicción (CoherenceAlert CONTRADICTION) → borde rojo. */
  contradictionNodeIds?: readonly string[];
  onSelectNode?: (node: GraphNode | null) => void;
  /** Doble clic: expandir vecindad vía `getGraphNeighborhood`. */
  onExpandNode?: (node: GraphNode) => void;
  loading?: boolean;
}

function buildElements(nodes: GraphNode[], edges: GraphEdge[]): ElementDefinition[] {
  const nodeElements: ElementDefinition[] = nodes.map((node) => ({
    group: "nodes",
    data: {
      id: node.id,
      label: node.label,
      name: node.canonical_name ?? node.id,
      risk: node.risk_score ?? 0,
      trust: nodeTrustState(node),
      locked: node.locked_by_analyst === true ? 1 : 0,
    },
  }));

  const known = new Set(nodes.map((node) => node.id));
  const edgeElements: ElementDefinition[] = edges
    .filter((edge) => known.has(edge.from) && known.has(edge.to))
    .map((edge) => ({
      group: "edges",
      data: {
        id: edge.id,
        source: edge.from,
        target: edge.to,
        type: edge.type,
        confidence: edge.confidence,
        trust: edgeTrustState(edge),
      },
    }));

  return [...nodeElements, ...edgeElements];
}

const LEGEND: readonly { label: string; dash: boolean; color: string }[] = [
  { label: "Analista (verificado)", dash: false, color: "#e2e8f0" },
  { label: "Evidencia IA sin verificar", dash: true, color: "#64748b" },
  { label: "Contradicción", dash: false, color: "#ef4444" },
];

export default function GraphCanvas({
  nodes,
  edges,
  selectedNodeId = null,
  highlightElementIds = [],
  contradictionNodeIds = [],
  onSelectNode,
  onExpandNode,
  loading = false,
}: GraphCanvasProps): JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<Core | null>(null);
  const [ready, setReady] = useState(false);

  // Refs "latest" para que los handlers de Cytoscape (registrados una sola vez)
  // no capturen valores obsoletos.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const onSelectRef = useRef(onSelectNode);
  onSelectRef.current = onSelectNode;
  const onExpandRef = useRef(onExpandNode);
  onExpandRef.current = onExpandNode;

  // 1) Instancia única.
  useEffect(() => {
    let disposed = false;

    (async () => {
      const cytoscape = (await import("cytoscape")).default;
      if (disposed || !containerRef.current) return;

      const cy = cytoscape({
        container: containerRef.current,
        elements: [],
        wheelSensitivity: 0.2,
        minZoom: 0.1,
        maxZoom: 4,
        style: [
          {
            selector: "node",
            style: {
              "background-color": "#475569",
              label: "data(name)",
              color: "#cbd5e1",
              "font-size": 10,
              "text-valign": "bottom",
              "text-margin-y": 4,
              width: "mapData(risk, 0, 1, 18, 46)",
              height: "mapData(risk, 0, 1, 18, 46)",
              "border-width": 1.5,
              "border-style": "dashed",
              "border-color": "#0a0f1a",
            },
          },
          // Estilo por `label` (SPEC-05 §2).
          ...ENTITY_LABELS.map((label) => ({
            selector: `node[label = "${label}"]`,
            style: {
              "background-color": LABEL_META[label].color,
              "border-color": LABEL_META[label].color,
            },
          })),
          // Evidencia de IA sin verificar → borde punteado.
          {
            selector: 'node[trust = "ai_unverified"]',
            style: { "border-style": "dashed", "border-width": 1.5, "border-opacity": 0.8 },
          },
          // `locked_by_analyst: true` → borde sólido.
          {
            selector: 'node[trust = "locked"]',
            style: { "border-style": "solid", "border-width": 3 },
          },
          {
            selector: "edge",
            style: {
              width: "mapData(confidence, 0, 1, 0.6, 3)",
              "line-color": "#334155",
              "target-arrow-color": "#334155",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
              "line-style": "dashed",
              opacity: 0.65,
            },
          },
          {
            selector: 'edge[trust = "locked"]',
            style: { "line-style": "solid", opacity: 0.9 },
          },
          // Contradicción → rojo (arista CONTRADICTS).
          {
            selector: 'edge[type = "CONTRADICTS"]',
            style: {
              "line-color": "#ef4444",
              "target-arrow-color": "#ef4444",
              "line-style": "solid",
              opacity: 1,
            },
          },
          // Fuerza bruta para el color de nodo en contradicción (clase).
          {
            selector: "node.contradiction",
            style: { "border-color": "#ef4444", "border-style": "solid", "border-width": 3 },
          },
          {
            selector: "node:selected",
            style: { "border-width": 4, "border-color": "#22d3ee" },
          },
          {
            selector: ".highlight",
            style: {
              "border-width": 3,
              "border-color": "#22d3ee",
              "line-color": "#22d3ee",
              "target-arrow-color": "#22d3ee",
              opacity: 1,
            },
          },
        ],
      });

      cy.on("tap", "node", (event) => {
        const id = event.target.id();
        const node = nodesRef.current.find((candidate) => candidate.id === id) ?? null;
        onSelectRef.current?.(node);
      });

      cy.on("dbltap", "node", (event) => {
        const id = event.target.id();
        const node = nodesRef.current.find((candidate) => candidate.id === id);
        if (node) onExpandRef.current?.(node);
      });

      cy.on("tap", (event) => {
        if (event.target === cy) onSelectRef.current?.(null);
      });

      cyRef.current = cy;
      setReady(true);
    })();

    return () => {
      disposed = true;
      cyRef.current?.destroy();
      cyRef.current = null;
      setReady(false);
    };
  }, []);

  // 2) Sincronización de elementos (diff incremental).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !ready) return;

    const elements = buildElements(nodes, edges);
    const incoming = new Set(elements.map((element) => String(element.data.id)));

    let added = 0;
    cy.batch(() => {
      cy.elements().forEach((element) => {
        if (!incoming.has(element.id())) element.remove();
      });
      const existing = new Set<string>();
      cy.elements().forEach((element) => {
        existing.add(element.id());
      });
      const toAdd = elements.filter((element) => !existing.has(String(element.data.id)));
      added = toAdd.length;
      if (added > 0) cy.add(toAdd);
    });

    if (added > 0) {
      cy.layout({ name: "cose", animate: false, fit: true, padding: 40 }).run();
    }
  }, [nodes, edges, ready]);

  // 3) Selección.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !ready) return;
    cy.batch(() => {
      cy.elements().unselect();
      if (selectedNodeId) {
        const target = cy.getElementById(selectedNodeId);
        if (target.nonempty()) target.select();
      }
    });
  }, [selectedNodeId, ready]);

  // 4) Resaltado de rutas críticas.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !ready) return;
    cy.batch(() => {
      cy.elements().removeClass("highlight");
      for (const id of highlightElementIds) {
        const element = cy.getElementById(id);
        if (element.nonempty()) element.addClass("highlight");
      }
    });
  }, [highlightElementIds, ready]);

  // 5) Marcas de contradicción (nodos).
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !ready) return;
    cy.batch(() => {
      cy.nodes().removeClass("contradiction");
      for (const id of contradictionNodeIds) {
        const element = cy.getElementById(id);
        if (element.nonempty()) element.addClass("contradiction");
      }
    });
  }, [contradictionNodeIds, ready]);

  return (
    <div className="relative h-full w-full">
      <div
        ref={containerRef}
        tabIndex={0}
        role="application"
        aria-label="Grafo interactivo de inteligencia. Tab para enfocar, doble clic en un nodo para expandir su vecindad."
        className="h-full w-full rounded-lg border border-base-700 bg-base-950 outline-none focus-visible:ring-2 focus-visible:ring-neon-cyan"
      />

      {/* Leyenda de confianza (SPEC-05 §2) */}
      <div className="pointer-events-none absolute bottom-3 left-3 flex flex-col gap-1 rounded border border-base-700 bg-base-900/85 p-2 text-[10px] text-slate-300">
        {LEGEND.map((item) => (
          <span key={item.label} className="flex items-center gap-2">
            <span
              aria-hidden="true"
              className="inline-block h-3 w-3 rounded-full"
              style={{
                border: `2px ${item.dash ? "dashed" : "solid"} ${item.color}`,
              }}
            />
            {item.label}
          </span>
        ))}
      </div>

      {loading && (
        <div className="absolute inset-0 flex items-center justify-center bg-base-950/60 text-sm text-slate-300">
          Cargando grafo…
        </div>
      )}

      {!loading && nodes.length === 0 && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center gap-1 text-center">
          <p className="text-sm text-slate-300">Sin entidades para mostrar</p>
          <p className="text-xs text-slate-500">
            Ajusta los filtros o lanza una consulta GraphRAG.
          </p>
        </div>
      )}
    </div>
  );
}
