/**
 * Helpers de presentación (relaciones, confianza y formato): semántica visual
 * de SPEC-05 §2 y SPEC-01 §3 (16 relaciones del contrato v2.0.0).
 */
import { describe, expect, it } from "vitest";
import {
  ANALYSIS_EVIDENCE_EDGE_TYPES,
  EDGE_TYPES,
  EDGE_TYPE_DISPLAY,
  OSINT_EDGE_TYPES,
  confidencePct,
  edgeDisplay,
  edgeTrustState,
  formatDate,
  isContradictionEdge,
  nodeTrustState,
  toDateInputValue,
  trustLabel,
} from "./labels";

describe("relaciones del grafo", () => {
  it("son las 16 del contrato, sin duplicados, y todas tienen nombre visible", () => {
    expect(EDGE_TYPES).toHaveLength(16);
    expect(new Set(EDGE_TYPES).size).toBe(16);
    for (const type of EDGE_TYPES) expect(EDGE_TYPE_DISPLAY[type].length).toBeGreaterThan(0);
  });

  it("las relaciones OSINT son un subconjunto de las analíticas excluidas", () => {
    for (const type of OSINT_EDGE_TYPES) {
      expect(ANALYSIS_EVIDENCE_EDGE_TYPES).not.toContain(type);
    }
  });

  it("edgeDisplay devuelve el nombre en español", () => {
    expect(edgeDisplay("CONTRADICTS")).toBe("Contradice");
  });

  it("solo CONTRADICTS se trata como contradicción", () => {
    expect(isContradictionEdge("CONTRADICTS")).toBe(true);
    expect(isContradictionEdge("CORROBORATES")).toBe(false);
  });
});

describe("semántica de confianza (SPEC-05 §2)", () => {
  it("un nodo verificado por el analista tiene borde sólido", () => {
    expect(nodeTrustState({ locked_by_analyst: true })).toBe("locked");
    expect(edgeTrustState({ locked_by_analyst: true })).toBe("locked");
  });

  it("todo lo que no está bloqueado por el analista es evidencia de IA sin verificar", () => {
    expect(nodeTrustState({ locked_by_analyst: false })).toBe("ai_unverified");
    expect(edgeTrustState({ locked_by_analyst: false })).toBe("ai_unverified");
    expect(nodeTrustState({} as { locked_by_analyst: boolean })).toBe("ai_unverified");
  });

  it("trustLabel describe ambos estados", () => {
    expect(trustLabel("locked")).toBe("Verificado (analista)");
    expect(trustLabel("ai_unverified")).toBe("Evidencia IA sin verificar");
  });
});

describe("formato", () => {
  it("formatDate devuelve guion largo si la fecha falta o no es válida", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("no-es-fecha")).toBe("—");
    expect(formatDate("2026-10-24T10:00:00Z")).not.toBe("—");
  });

  it("toDateInputValue produce yyyy-mm-dd para <input type=date>", () => {
    expect(toDateInputValue("2026-10-24T10:00:00.000Z")).toBe("2026-10-24");
    expect(toDateInputValue(undefined)).toBe("");
    expect(toDateInputValue("basura")).toBe("");
  });

  it("confidencePct redondea a porcentaje entero", () => {
    expect(confidencePct(0.876)).toBe("88%");
    expect(confidencePct(0)).toBe("0%");
    expect(confidencePct(null)).toBe("—");
  });
});
