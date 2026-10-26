/**
 * Contrato de UI del inspector de evidencia (SPEC-04 §3.5).
 *
 * La prueba fija dos invariantes: no se muestra "válido" antes de verificar y
 * una cadena inválida se presenta como bloqueo, no como advertencia.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/services/api";
import type { Evidence, EvidenceVerification } from "@/services/api";

import EvidenceInspector from "./EvidenceInspector";

const EVIDENCE = {
  evidence_id: "44444444-4444-4444-4444-444444444444",
  payload_sha256: "a".repeat(64),
  tlp: "TLP:AMBER",
  collector_agent: "HERMES",
  source_type: "SIGINT",
  source_uri: "sensor://test",
  captured_at: "2026-10-01T00:00:00Z",
  received_at: "2026-10-01T00:01:00Z",
  metadata: { sensor: "unit-test" },
} as unknown as Evidence;

const VALID = {
  evidence_id: EVIDENCE.evidence_id,
  valid: true,
  stored_sha256: "a".repeat(64),
  computed_sha256: "a".repeat(64),
  chain_valid: true,
  checked_at: "2026-10-01T00:02:00Z",
} as EvidenceVerification;

const BROKEN = {
  ...VALID,
  chain_valid: false,
} as EvidenceVerification;

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("EvidenceInspector", () => {
  it("no declara integridad hasta que el analista verifica", async () => {
    vi.spyOn(api, "getEvidence").mockResolvedValue(EVIDENCE);
    const verify = vi.spyOn(api, "verifyEvidence").mockResolvedValue(VALID);

    render(<EvidenceInspector evidenceId={EVIDENCE.evidence_id} canVerify />);

    expect((await screen.findAllByText(EVIDENCE.evidence_id)).length).toBeGreaterThan(0);
    expect(screen.queryByText("Evidencia íntegra y encadenada")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Verificar" }));

    await waitFor(() => expect(verify).toHaveBeenCalledWith(EVIDENCE.evidence_id));
    expect(await screen.findByText("Evidencia íntegra y encadenada")).toBeInTheDocument();
  });

  it("una cadena rota bloquea la aprobación con banner rojo", async () => {
    vi.spyOn(api, "getEvidence").mockResolvedValue(EVIDENCE);
    vi.spyOn(api, "verifyEvidence").mockResolvedValue(BROKEN);

    render(<EvidenceInspector evidenceId={EVIDENCE.evidence_id} canVerify />);
    fireEvent.click(await screen.findByRole("button", { name: "Verificar" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Cadena de custodia inválida");
    expect(alert).toHaveTextContent("no se puede aprobar");
  });
});

