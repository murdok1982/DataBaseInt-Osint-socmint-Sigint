/**
 * Metadatos de presentación del grafo (colores por `label`, nombres de relación)
 * y semántica visual de confianza.
 *
 * Derivado de los enums de `contracts/openapi.yaml` **v2.0.0** (EntityLabel,
 * GraphEdge.type) y de SPEC-01 §1/§2/§3 y SPEC-05 §2 (analista vs. IA).
 *
 * Paleta: `.antigravity/midjourney-ui-prompts.md` (§1 neon cyan / amber / crimson).
 */

import type { EntityLabel, GraphEdge, GraphEdgeType, GraphNode } from "@/services/api";

/** Orden de presentación de la UI (todos los `EntityLabel` del contrato v2.0). */
export const ENTITY_LABELS: readonly EntityLabel[] = [
  "Person",
  "Organization",
  "CyberAsset",
  "Location",
  "Event",
  "Report",
  "Analysis",
  "IntelRequirement",
  "CoherenceAlert",
];

/** Nodos OSINT operativos (SPEC-01 §1). */
export const OSINT_NODE_LABELS: readonly EntityLabel[] = [
  "Person",
  "Organization",
  "CyberAsset",
  "Location",
  "Event",
];

/** Nodos documentales/analíticos (SPEC-01 §2). */
export const ANALYTIC_NODE_LABELS: readonly EntityLabel[] = [
  "Report",
  "Analysis",
  "IntelRequirement",
  "CoherenceAlert",
];

export type LabelGroup = "OSINT" | "ANALÍTICO";

export interface LabelMeta {
  label: EntityLabel;
  display: string;
  color: string;
  group: LabelGroup;
}

export const LABEL_META: Record<EntityLabel, LabelMeta> = {
  Person: { label: "Person", display: "Persona", color: "#22d3ee", group: "OSINT" },
  Organization: {
    label: "Organization",
    display: "Organización",
    color: "#fbbf24",
    group: "OSINT",
  },
  CyberAsset: { label: "CyberAsset", display: "Ciberactivo", color: "#38bdf8", group: "OSINT" },
  Location: { label: "Location", display: "Ubicación", color: "#34d399", group: "OSINT" },
  Event: { label: "Event", display: "Evento", color: "#ef4444", group: "OSINT" },
  Report: { label: "Report", display: "Informe", color: "#94a3b8", group: "ANALÍTICO" },
  Analysis: { label: "Analysis", display: "Análisis", color: "#a78bfa", group: "ANALÍTICO" },
  IntelRequirement: {
    label: "IntelRequirement",
    display: "Requerimiento (RFI)",
    color: "#f472b6",
    group: "ANALÍTICO",
  },
  CoherenceAlert: {
    label: "CoherenceAlert",
    display: "Alerta de coherencia",
    color: "#fb7185",
    group: "ANALÍTICO",
  },
};

export function labelColor(label: EntityLabel): string {
  return LABEL_META[label].color;
}

export function labelDisplay(label: EntityLabel): string {
  return LABEL_META[label].display;
}

export function isOsintLabel(label: EntityLabel): boolean {
  return LABEL_META[label].group === "OSINT";
}

/** Los 16 tipos de relación del contrato v2.0.0 (SPEC-01 §3). */
export const EDGE_TYPES: readonly GraphEdgeType[] = [
  "CONTROLS",
  "FINANCED",
  "USES_ASSET",
  "LOCATED_AT",
  "ASSOCIATED_WITH",
  "PARTICIPATED_IN",
  "MENTIONED_IN",
  "INVESTIGATES",
  "HAS_REQUIREMENT",
  "CITES_ANALYSIS",
  "CORROBORATES",
  "CONTRADICTS",
  "EXTENDS",
  "ANSWERS_REQUIREMENT",
  "RAISED_FOR",
  "ABOUT",
];

export const EDGE_TYPE_DISPLAY: Record<GraphEdgeType, string> = {
  CONTROLS: "Controla",
  FINANCED: "Financió",
  USES_ASSET: "Usa activo",
  LOCATED_AT: "Ubicado en",
  ASSOCIATED_WITH: "Asociado con",
  PARTICIPATED_IN: "Participó en",
  MENTIONED_IN: "Mencionado en",
  INVESTIGATES: "Investiga",
  HAS_REQUIREMENT: "Tiene requerimiento",
  CITES_ANALYSIS: "Cita análisis",
  CORROBORATES: "Corrobora",
  CONTRADICTS: "Contradice",
  EXTENDS: "Extiende",
  ANSWERS_REQUIREMENT: "Responde requerimiento",
  RAISED_FOR: "Emitida para",
  ABOUT: "Sobre",
};

/** Relaciones OSINT (SPEC-01 §3.1) vs. relaciones analíticas (SPEC-01 §3.2). */
export const OSINT_EDGE_TYPES: readonly GraphEdgeType[] = [
  "CONTROLS",
  "FINANCED",
  "USES_ASSET",
  "LOCATED_AT",
  "ASSOCIATED_WITH",
  "PARTICIPATED_IN",
  "MENTIONED_IN",
];

/** Tipos de relación que la Bandeja/Workspace tratan como "evidencia analítica". */
export const ANALYSIS_EVIDENCE_EDGE_TYPES: readonly GraphEdgeType[] = [
  "CORROBORATES",
  "CONTRADICTS",
  "EXTENDS",
  "CITES_ANALYSIS",
  "HAS_REQUIREMENT",
  "ANSWERS_REQUIREMENT",
];

export function edgeDisplay(type: GraphEdgeType): string {
  return EDGE_TYPE_DISPLAY[type] ?? type;
}

export function isContradictionEdge(type: GraphEdgeType): boolean {
  return type === "CONTRADICTS";
}

/* ------------------------------------------------------------------ *
 * Semántica visual de confianza (SPEC-05 §2)                          *
 * ------------------------------------------------------------------ *
 * El contrato v2.0.0 no expone un campo `verified`/`source` por nodo: la
 * única marca de verificación humana es `locked_by_analyst`. Por tanto:
 *   · `locked_by_analyst === true`  → borde **sólido** (verificado por el analista)
 *   · `locked_by_analyst !== true`  → borde **punteado** (evidencia de IA sin verificar)
 *   · contradicción (CoherenceAlert CONTRADICTION) → **rojo**
 * Ver SPEC-GAP-3 en el README.                                        */
export type TrustState = "locked" | "ai_unverified";

export function nodeTrustState(node: Pick<GraphNode, "locked_by_analyst">): TrustState {
  return node.locked_by_analyst === true ? "locked" : "ai_unverified";
}

export function edgeTrustState(edge: Pick<GraphEdge, "locked_by_analyst">): TrustState {
  return edge.locked_by_analyst === true ? "locked" : "ai_unverified";
}

export function trustLabel(state: TrustState): string {
  return state === "locked" ? "Verificado (analista)" : "Evidencia IA sin verificar";
}

/* ------------------------------------------------------------------ *
 * Formato de fechas                                                   *
 * ------------------------------------------------------------------ */

/** Fecha ISO → texto compacto es-ES; devuelve "—" si falta o es inválida. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("es-ES", { dateStyle: "medium", timeStyle: "short" });
}

/** Fecha ISO → `yyyy-mm-dd` para `<input type="date">`. */
export function toDateInputValue(value: string | null | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toISOString().slice(0, 10);
}

/** Confianza 0..1 → porcentaje entero. */
export function confidencePct(value: number | null | undefined): string {
  if (typeof value !== "number") return "—";
  return `${Math.round(value * 100)}%`;
}
