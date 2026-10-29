"use client";

/**
 * Bandeja de aprobación (SPEC-04 §3.6).
 *
 * Cada propuesta `AGENT_STAGED` se revisa individualmente: no existe
 * "aprobar todo". Aprobar sólo la pasa a `ANALYST_VERIFIED`; la aplicación al
 * grafo la ejecuta ATENEA. Se bloquea la decisión si falta evidencia o si
 * alguna cadena de custodia no verifica, y se exige `version` para detectar
 * conflictos de concurrencia.
 */

import type { ReactElement } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "@/services/api";
import type { EvidenceVerification, Proposal } from "@/services/api";

export interface ApprovalInboxProps {
  canDecide?: boolean;
  onOpenEvidence?: (evidenceId: string) => void;
  onChanged?: () => void;
}

type VerificationState = EvidenceVerification | "loading" | "error";

function short(value: string | null | undefined, size = 10): string {
  if (!value) return "—";
  return value.length > size ? `${value.slice(0, size)}…` : value;
}

function fmt(value: string | null | undefined): string {
  if (!value) return "—";
  const date = Date.parse(value);
  return Number.isNaN(date) ? value : new Date(date).toLocaleString();
}

export default function ApprovalInbox({
  canDecide = false,
  onOpenEvidence,
  onChanged,
}: ApprovalInboxProps): ReactElement {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [verifications, setVerifications] = useState<Record<string, VerificationState>>({});
  const [reason, setReason] = useState("");
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selected = useMemo(
    () => proposals.find((item) => item.proposal_id === selectedId) ?? null,
    [proposals, selectedId],
  );

  const load = useCallback(async (): Promise<void> => {
    setLoading(true);
    setError(null);
    try {
      const result = await api.listProposals({ status: "AGENT_STAGED", limit: 100 });
      const items = result.items ?? [];
      setProposals(items);
      setSelectedId((current) =>
        current && items.some((item) => item.proposal_id === current)
          ? current
          : (items[0]?.proposal_id ?? null),
      );
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo cargar la bandeja.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Verificación de todas las evidencias de la propuesta seleccionada.
  useEffect(() => {
    if (!selected || !canDecide) return;
    const ids = selected.evidence_ids ?? [];
    let active = true;
    setVerifications((current) => {
      const next = { ...current };
      for (const id of ids) next[id] = "loading";
      return next;
    });
    (async () => {
      await Promise.all(
        ids.map(async (id) => {
          try {
            const result = await api.verifyEvidence(id);
            if (active) setVerifications((current) => ({ ...current, [id]: result }));
          } catch {
            if (active) setVerifications((current) => ({ ...current, [id]: "error" }));
          }
        }),
      );
    })();
    return () => {
      active = false;
    };
  }, [selected, canDecide]);

  const evidenceIds = selected?.evidence_ids ?? [];
  const evidenceOk =
    evidenceIds.length > 0 &&
    evidenceIds.every((id) => {
      const value = verifications[id];
      return value !== "loading" && value !== "error" && value?.chain_valid === true;
    });
  const canSubmit = canDecide && evidenceOk && reason.trim().length >= 8 && !busy;

  const decide = useCallback(
    async (decision: "approve" | "reject"): Promise<void> => {
      if (!selected?.proposal_id || !selected.version) return;
      setBusy(true);
      setError(null);
      try {
        const call = decision === "approve" ? api.approveProposal : api.rejectProposal;
        await call(selected.proposal_id, {
          reason: reason.trim(),
          version: selected.version,
        });
        setReason("");
        await load();
        onChanged?.();
      } catch (err) {
        if (err instanceof ApiError && err.status === 409) {
          setError(
            "La propuesta cambió mientras la revisabas (conflicto de versión). Recarga antes de decidir.",
          );
          await load();
        } else {
          setError(err instanceof ApiError ? err.message : "No se pudo registrar la decisión.");
        }
      } finally {
        setBusy(false);
      }
    },
    [selected, reason, load, onChanged],
  );

  return (
    <section
      aria-label="Bandeja de aprobación"
      className="flex h-full min-h-0 gap-3 rounded-lg border border-base-700 bg-base-900 p-3"
    >
      <div className="flex w-72 shrink-0 flex-col border-r border-base-700 pr-3">
        <div className="mb-2 flex items-center justify-between">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
            Propuestas staged
          </h2>
          <button
            type="button"
            onClick={() => void load()}
            className="rounded border border-base-600 px-2 py-0.5 text-[10px] text-slate-300 hover:border-neon-cyan hover:text-neon-cyan"
          >
            Recargar
          </button>
        </div>
        {loading && <p className="text-xs text-slate-400">Cargando…</p>}
        {!loading && proposals.length === 0 && (
          <p className="text-xs text-slate-400">No hay propuestas pendientes.</p>
        )}
        <ul className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {proposals.map((proposal) => {
            const id = proposal.proposal_id ?? "";
            const active = id === selectedId;
            return (
              <li key={id}>
                <button
                  type="button"
                  onClick={() => {
                    setSelectedId(id);
                    setReason("");
                  }}
                  aria-current={active ? "true" : undefined}
                  className={`w-full rounded border px-2 py-1.5 text-left text-[11px] transition ${
                    active
                      ? "border-neon-cyan/60 bg-neon-cyan/10 text-slate-100"
                      : "border-base-700 text-slate-400 hover:border-base-600 hover:text-slate-200"
                  }`}
                >
                  <span className="block font-semibold">
                    {proposal.proposal_type ?? "PROPOSAL"} · v{proposal.version ?? "?"}
                  </span>
                  <span className="block font-mono text-[10px] text-slate-400">
                    {short(proposal.proposal_id, 16)}
                  </span>
                  <span className="block">{proposal.agent_type ?? "—"}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="min-w-0 flex-1 overflow-y-auto">
        {error && (
          <p role="alert" className="mb-2 rounded border border-neon-crimson/40 bg-neon-crimson/10 px-2 py-1 text-xs text-neon-crimson">
            {error}
          </p>
        )}
        {!selected ? (
          <p className="text-xs text-slate-400">Selecciona una propuesta para revisarla.</p>
        ) : (
          <div className="space-y-4 text-xs text-slate-300">
            <header className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <h3 className="text-sm font-semibold text-slate-100">
                  {selected.proposal_type ?? "Propuesta"} de {selected.agent_type ?? "agente"}
                </h3>
                <p className="font-mono text-[10px] text-slate-400">
                  {selected.proposal_id} · mission {short(selected.mission_id, 18)}
                </p>
              </div>
              <span className="rounded border border-neon-amber/40 px-2 py-0.5 text-[10px] text-neon-amber">
                {selected.status ?? "AGENT_STAGED"} · v{selected.version ?? "?"}
              </span>
            </header>

            <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-2">
              <dt className="text-slate-400">Creada</dt>
              <dd>{fmt(selected.created_at)}</dd>
              <dt className="text-slate-400">Confianza</dt>
              <dd>{selected.confidence ?? "—"}</dd>
              <dt className="text-slate-400">Entidad destino</dt>
              <dd className="font-mono text-[10px]">{selected.target_entity_id ?? "—"}</dd>
              <dt className="text-slate-400">Evidencias</dt>
              <dd>{evidenceIds.length}</dd>
            </dl>

            <section aria-label="Evidencias de la propuesta">
              <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Evidencia y cadena de custodia
              </h4>
              {evidenceIds.length === 0 && (
                <p className="rounded border border-neon-crimson/40 bg-neon-crimson/10 px-2 py-1 text-neon-crimson">
                  Sin evidencia: la propuesta no se puede aprobar.
                </p>
              )}
              <ul className="space-y-1">
                {evidenceIds.map((id) => {
                  const status = verifications[id];
                  const ok = status !== "loading" && status !== "error" && status?.chain_valid === true;
                  return (
                    <li
                      key={id}
                      className="flex items-center justify-between gap-2 rounded border border-base-700 px-2 py-1"
                    >
                      <span className="font-mono text-[10px]">{id}</span>
                      <span className="flex items-center gap-2">
                        <span
                          className={
                            status === "loading"
                              ? "text-slate-400"
                              : ok
                                ? "text-emerald-400"
                                : "text-neon-crimson"
                          }
                        >
                          {status === "loading" ? "verificando…" : ok ? "cadena OK" : "cadena inválida"}
                        </span>
                        <button
                          type="button"
                          onClick={() => onOpenEvidence?.(id)}
                          className="rounded border border-base-600 px-1.5 py-0.5 text-[10px] hover:border-neon-cyan hover:text-neon-cyan"
                        >
                          Inspeccionar
                        </button>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>

            <section aria-label="Payload propuesto">
              <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Payload
              </h4>
              <pre className="max-h-64 overflow-auto rounded border border-base-700 bg-base-950 p-2 font-mono text-[10px] text-slate-400">
                {JSON.stringify(selected.payload ?? {}, null, 2)}
              </pre>
            </section>

            <section aria-label="Decisión">
              <label htmlFor="decision-reason" className="mb-1 block text-[11px] uppercase tracking-wider text-slate-400">
                Motivo (mín. 8 caracteres)
              </label>
              <textarea
                id="decision-reason"
                value={reason}
                onChange={(event) => setReason(event.target.value)}
                rows={3}
                disabled={!canDecide || busy}
                className="w-full rounded border border-base-700 bg-base-950 px-2 py-1.5 text-xs text-slate-200 focus:border-neon-cyan focus:outline-none disabled:opacity-50"
                placeholder="Evidencia corroborada por dos fuentes independientes…"
              />
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => void decide("approve")}
                  disabled={!canSubmit}
                  title={!evidenceOk ? "Verifica primero todas las evidencias" : undefined}
                  className="rounded border border-emerald-500/60 px-3 py-1 text-xs font-semibold text-emerald-400 transition hover:bg-emerald-500/10 disabled:opacity-40"
                >
                  Aprobar
                </button>
                <button
                  type="button"
                  onClick={() => void decide("reject")}
                  disabled={!canDecide || reason.trim().length < 8 || busy}
                  className="rounded border border-neon-crimson/60 px-3 py-1 text-xs font-semibold text-neon-crimson transition hover:bg-neon-crimson/10 disabled:opacity-40"
                >
                  Rechazar
                </button>
                <button
                  type="button"
                  disabled
                  title="SPEC-GAP-UI-004: crea una tasking_order en una spec posterior"
                  className="rounded border border-base-700 px-3 py-1 text-xs text-slate-400"
                >
                  Pedir más evidencia
                </button>
                {!canDecide && (
                  <span className="text-[10px] text-slate-400">
                    Tu rol sólo puede leer la bandeja.
                  </span>
                )}
              </div>
            </section>
          </div>
        )}
      </div>
    </section>
  );
}
