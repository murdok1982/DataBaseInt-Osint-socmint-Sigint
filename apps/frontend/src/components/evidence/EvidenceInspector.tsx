"use client";

/**
 * Inspector de evidencia (SPEC-04 §3.5).
 *
 * Muestra primero identidad, hash y cadena; los bytes no se descargan ni se
 * renderizan aquí. La verificación es explícita y sólo la ejecuta `ANALYST+`.
 * Si `chain_valid=false`, la UI muestra un banner rojo y las bandejas de
 * aprobación deben bloquear la propuesta que dependa de esta evidencia.
 */

import type { ReactElement } from "react";
import { useCallback, useEffect, useState } from "react";

import { ApiError, api } from "@/services/api";
import type { Evidence, EvidenceVerification } from "@/services/api";

export interface EvidenceInspectorProps {
  evidenceId: string;
  canVerify?: boolean;
  onClose?: () => void;
  onVerified?: (result: EvidenceVerification) => void;
}

function fmt(value: string | null | undefined): string {
  if (!value) return "—";
  const date = Date.parse(value);
  return Number.isNaN(date) ? value : new Date(date).toLocaleString();
}

function Hash({ value }: { value?: string | null }): ReactElement {
  if (!value) return <span className="text-slate-400">—</span>;
  return (
    <code className="break-all font-mono text-[10px] text-slate-300" title={value}>
      {value}
    </code>
  );
}

export default function EvidenceInspector({
  evidenceId,
  canVerify = false,
  onClose,
  onVerified,
}: EvidenceInspectorProps): ReactElement {
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [verification, setVerification] = useState<EvidenceVerification | null>(null);
  const [loading, setLoading] = useState(true);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    setEvidence(null);
    setVerification(null);
    (async () => {
      try {
        const result = await api.getEvidence(evidenceId);
        if (active) setEvidence(result);
      } catch (err) {
        if (active) {
          setError(err instanceof ApiError ? err.message : "No se pudo abrir la evidencia.");
        }
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => {
      active = false;
    };
  }, [evidenceId]);

  const verify = useCallback(async (): Promise<void> => {
    setVerifying(true);
    setError(null);
    try {
      const result = await api.verifyEvidence(evidenceId);
      setVerification(result);
      onVerified?.(result);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo verificar la evidencia.");
    } finally {
      setVerifying(false);
    }
  }, [evidenceId, onVerified]);

  const chainValid = verification?.chain_valid;
  const chainBroken = chainValid === false;

  return (
    <section
      aria-label="Inspector de evidencia"
      className="flex h-full min-h-0 flex-col rounded-lg border border-base-700 bg-base-900"
    >
      <header className="flex items-center justify-between gap-2 border-b border-base-700 px-3 py-2">
        <div>
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-200">
            Evidencia
          </h2>
          <p className="font-mono text-[10px] text-slate-400">{evidenceId}</p>
        </div>
        <div className="flex items-center gap-2">
          {canVerify && (
            <button
              type="button"
              onClick={() => void verify()}
              disabled={verifying || loading}
              aria-busy={verifying}
              className="rounded border border-neon-cyan/60 px-2 py-1 text-[11px] text-neon-cyan transition hover:bg-neon-cyan/10 disabled:opacity-40"
            >
              {verifying ? "Verificando…" : "Verificar"}
            </button>
          )}
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-base-600 px-2 py-1 text-[11px] text-slate-300 transition hover:border-neon-crimson hover:text-neon-crimson"
            >
              Cerrar
            </button>
          )}
        </div>
      </header>

      {chainBroken && (
        <p
          role="alert"
          className="border-b border-neon-crimson/50 bg-neon-crimson/15 px-3 py-2 text-xs font-semibold text-neon-crimson"
        >
          Cadena de custodia inválida: no se puede aprobar ninguna propuesta que use esta
          evidencia.
        </p>
      )}
      {error && (
        <p role="alert" className="border-b border-neon-crimson/40 px-3 py-2 text-xs text-neon-crimson">
          {error}
        </p>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-3 text-xs text-slate-300">
        {loading ? (
          <p className="text-slate-400">Cargando metadatos…</p>
        ) : evidence ? (
          <div className="space-y-4">
            <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-2">
              <dt className="text-slate-400">evidence_id</dt>
              <dd className="font-mono text-[10px] break-all">{evidence.evidence_id ?? evidenceId}</dd>
              <dt className="text-slate-400">SHA-256 almacenado</dt>
              <dd><Hash value={evidence.payload_sha256} /></dd>
              <dt className="text-slate-400">TLP</dt>
              <dd>{evidence.tlp ?? "—"}</dd>
              <dt className="text-slate-400">Colector</dt>
              <dd>{evidence.collector_agent ?? "—"}</dd>
              <dt className="text-slate-400">Fuente</dt>
              <dd className="break-all">{evidence.source_type ?? "—"} · {evidence.source_uri ?? "—"}</dd>
              <dt className="text-slate-400">Capturada</dt>
              <dd>{fmt(evidence.captured_at)}</dd>
              <dt className="text-slate-400">Recibida</dt>
              <dd>{fmt(evidence.received_at)}</dd>
            </dl>

            <div>
              <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-slate-400">
                Metadatos
              </h3>
              <pre className="max-h-48 overflow-auto rounded border border-base-700 bg-base-950 p-2 font-mono text-[10px] text-slate-400">
                {JSON.stringify(evidence.metadata ?? {}, null, 2)}
              </pre>
            </div>

            {verification && (
              <div
                aria-live="polite"
                className={`rounded border p-3 ${
                  chainValid
                    ? "border-emerald-500/40 bg-emerald-500/10"
                    : "border-neon-crimson/50 bg-neon-crimson/10"
                }`}
              >
                <p className="mb-2 font-semibold">
                  {chainValid ? "Evidencia íntegra y encadenada" : "Verificación fallida"}
                </p>
                <dl className="grid grid-cols-[130px_1fr] gap-x-3 gap-y-1">
                  <dt className="text-slate-400">valid</dt>
                  <dd>{String(verification.valid ?? false)}</dd>
                  <dt className="text-slate-400">chain_valid</dt>
                  <dd>{String(verification.chain_valid ?? false)}</dd>
                  <dt className="text-slate-400">SHA almacenado</dt>
                  <dd><Hash value={verification.stored_sha256} /></dd>
                  <dt className="text-slate-400">SHA recalculado</dt>
                  <dd><Hash value={verification.computed_sha256} /></dd>
                  <dt className="text-slate-400">Verificado</dt>
                  <dd>{fmt(verification.checked_at)}</dd>
                </dl>
              </div>
            )}
          </div>
        ) : null}
      </div>
    </section>
  );
}
