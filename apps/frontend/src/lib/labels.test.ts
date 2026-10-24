/**
 * Metadatos de presentación del grafo: cada etiqueta del contrato debe tener
 * color y nombre, y los grupos OSINT/analítico deben cubrir todas las etiquetas
 * sin solaparse (SPEC-01 §1/§2).
 */
import { describe, expect, it } from "vitest";
import {
  ANALYTIC_NODE_LABELS,
  ENTITY_LABELS,
  LABEL_META,
  OSINT_NODE_LABELS,
  isOsintLabel,
  labelColor,
  labelDisplay,
} from "./labels";

describe("etiquetas del grafo", () => {
  it("cubre las 9 etiquetas del contrato sin duplicados", () => {
    expect(ENTITY_LABELS).toHaveLength(9);
    expect(new Set(ENTITY_LABELS).size).toBe(ENTITY_LABELS.length);
  });

  it("los grupos OSINT y analítico particionan ENTITY_LABELS", () => {
    const grouped = [...OSINT_NODE_LABELS, ...ANALYTIC_NODE_LABELS].sort();
    expect(grouped).toEqual([...ENTITY_LABELS].sort());
    for (const label of OSINT_NODE_LABELS) expect(isOsintLabel(label)).toBe(true);
    for (const label of ANALYTIC_NODE_LABELS) expect(isOsintLabel(label)).toBe(false);
  });

  it("cada etiqueta tiene color hexadecimal y nombre visible", () => {
    for (const label of ENTITY_LABELS) {
      expect(labelColor(label)).toMatch(/^#[0-9a-f]{6}$/i);
      expect(labelDisplay(label).length).toBeGreaterThan(0);
      expect(LABEL_META[label].label).toBe(label);
    }
  });

  it("usa colores distintos para distinguir visualmente los nodos", () => {
    const colors = ENTITY_LABELS.map(labelColor);
    expect(new Set(colors).size).toBe(colors.length);
  });
});
