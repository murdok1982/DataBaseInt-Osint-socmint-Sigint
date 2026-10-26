/**
 * Contrato de UI de la bandeja de aprobación (SPEC-04 §3.6).
 *
 * Una propuesta sin cadena válida no se puede aprobar; cada decisión viaja con
 * `version` para que el backend pueda rechazar conflictos de concurrencia.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "@/services/api";
import type { Proposal } from "@/services/api";

import ApprovalInbox from "./ApprovalInbox";

const PROPOSAL = {
  proposal_id: "66666666-6666-6666-6666-666666666666",
  proposal_type: "RELATIONSHIP",
  status: "AGENT_STAGED",
  mission_id: "77777777-7777-7777-7777-777777777777",
  agent_type: "HERMES",
  target_entity_id: null,
  payload: { relationship_type: "COMMUNICATES_WITH" },
  evidence_ids: ["44444444-4444-4444-4444-444444444444"],
  confidence: 71,
  version: 3,
  created_at: "2026-10-01T00:00:00Z",
  decision_reason: null,
} as unknown as Proposal;

const VALID = {
  evidence_id: PROPOSAL.evidence_ids?.[0],
  valid: true,
  stored_sha256: "a".repeat(64),
  computed_sha256: "a".repeat(64),
  chain_valid: true,
  checked_at: "2026-10-01T00:02:00Z",
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ApprovalInbox", () => {
  it("aprueba una propuesta sólo tras verificar la cadena y con versión", async () => {
    vi.spyOn(api, "listProposals").mockResolvedValue({
      items: [PROPOSAL],
      truncated: false,
      next_cursor: null,
    });
    const verify = vi.spyOn(api, "verifyEvidence").mockResolvedValue(VALID as never);
    const approve = vi
      .spyOn(api, "approveProposal")
      .mockResolvedValue({ ...PROPOSAL, status: "ANALYST_VERIFIED", version: 4 });

    render(<ApprovalInbox canDecide />);
    fireEvent.click(await screen.findByText(/RELATIONSHIP · v3/));

    expect(await screen.findByText("cadena OK")).toBeInTheDocument();
    expect(verify).toHaveBeenCalledWith(PROPOSAL.evidence_ids?.[0]);

    fireEvent.change(screen.getByLabelText("Motivo (mín. 8 caracteres)"), {
      target: { value: "Dos fuentes independientes corroboran" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Aprobar" }));

    await waitFor(() =>
      expect(approve).toHaveBeenCalledWith(PROPOSAL.proposal_id, {
        reason: "Dos fuentes independientes corroboran",
        version: 3,
      }),
    );
  });

  it("bloquea Aprobar cuando la cadena de custodia es inválida", async () => {
    vi.spyOn(api, "listProposals").mockResolvedValue({
      items: [PROPOSAL],
      truncated: false,
      next_cursor: null,
    });
    vi.spyOn(api, "verifyEvidence").mockResolvedValue({ ...VALID, chain_valid: false } as never);

    render(<ApprovalInbox canDecide />);
    fireEvent.click(await screen.findByText(/RELATIONSHIP · v3/));
    expect(await screen.findByText("cadena inválida")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("Motivo (mín. 8 caracteres)"), {
      target: { value: "Intento de aprobación sin cadena" },
    });
    expect(screen.getByRole("button", { name: "Aprobar" })).toBeDisabled();
  });
});

