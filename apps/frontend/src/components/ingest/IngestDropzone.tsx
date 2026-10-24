"use client";

/**
 * IngestDropzone — drag & drop de informes con progreso en vivo (transversal).
 * Autorizado por: SPEC-05 §6 (zona drag & drop → `POST /api/v1/ingest`,
 * progreso por WebSocket, `PENDING_HUMAN_REVIEW` visible).
 * Traza a: `createIngestJob`, `getIngestJob`, `ingestProgressWs`.
 *
 * Visibilidad por rol: sólo ADMIN/ANALYST (`can("ingest:create")`, SPEC-04 §1).
 * El componente se OCULTA por completo para VIEWER.
 */

import type { ReactElement } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import { useAuth } from "@/context/AuthContext";
import { ApiError, api } from "@/services/api";
import type { IngestJob } from "@/services/api";

const STATUS_LABEL: Record<NonNullable<IngestJob["status"]>, string> = {
  QUEUED: "En cola",
  PROCESSING: "Procesando",
  DONE: "Completado",
  FAILED: "Fallido",
};

const STATUS_COLOR: Record<NonNullable<IngestJob["status"]>, string> = {
  QUEUED: "text-slate-300",
  PROCESSING: "text-neon-cyan",
  DONE: "text-neon-emerald",
  FAILED: "text-neon-crimson",
};

export interface IngestDropzoneProps {
  /** Notifica al dashboard que un job terminó (para refrescar el grafo). */
  onCompleted?: (job: IngestJob) => void;
}

export default function IngestDropzone({ onCompleted }: IngestDropzoneProps): ReactElement | null {
  const { can } = useAuth();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [job, setJob] = useState<IngestJob | null>(null);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const socketRef = useRef<WebSocket | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const closeSocket = useCallback((): void => {
    if (socketRef.current) {
      socketRef.current.close();
      socketRef.current = null;
    }
  }, []);

  useEffect(() => closeSocket, [closeSocket]);

  const applyJob = useCallback(
    (next: IngestJob): void => {
      setJob((previous) => ({ ...previous, ...next }));
      if (next.status === "DONE" || next.status === "FAILED") {
        closeSocket();
        onCompleted?.(next);
      }
    },
    [closeSocket, onCompleted],
  );

  const subscribe = useCallback(
    (jobId: string): void => {
      closeSocket();
      try {
        const socket = api.ingestProgressWs(jobId);
        socketRef.current = socket;
        socket.onmessage = (event: MessageEvent) => {
          try {
            const payload = JSON.parse(event.data) as IngestJob;
            applyJob(payload);
          } catch {
            // Mensaje no-JSON: se ignora (ver SPEC-GAP-1 sobre el formato del WS).
          }
        };
        socket.onerror = () => {
          setError("Se perdió la conexión de progreso. El job sigue en el servidor.");
        };
      } catch {
        setError("No se pudo abrir el WebSocket de progreso.");
      }
    },
    [applyJob, closeSocket],
  );

  const handleFiles = (files: FileList | null): void => {
    const candidate = files?.[0] ?? null;
    if (candidate) {
      setFile(candidate);
      setTitle((current) => current || candidate.name.replace(/\.[^.]+$/, ""));
      setError(null);
    }
  };

  const handleSubmit = async (): Promise<void> => {
    if (!file) {
      setError("Adjunta un informe (PDF) antes de subir.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await api.createIngestJob(file, title.trim() || file.name);
      setJob(created);
      if (created.job_id) subscribe(created.job_id);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "No se pudo crear el job de ingesta.");
    } finally {
      setBusy(false);
    }
  };

  // SPEC-04 §1 — acción no permitida para VIEWER: se oculta.
  if (!can("ingest:create")) return null;

  const status = job?.status;
  const progressPct = Math.round((job?.progress ?? 0) * 100);

  return (
    <section
      aria-label="Ingesta de informes OSINT"
      className="flex flex-col gap-3 rounded-lg border border-base-700 bg-base-900/70 p-4"
    >
      <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
        Ingesta de informes
      </h2>

      <div
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          handleFiles(event.dataTransfer.files);
        }}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
        }}
        role="button"
        tabIndex={0}
        aria-label="Zona drag and drop para subir un informe"
        className={`flex cursor-pointer flex-col items-center justify-center gap-1 rounded-md border border-dashed p-6 text-center transition ${
          dragging
            ? "border-neon-cyan bg-neon-cyan/10"
            : "border-base-600 bg-base-950/60 hover:border-neon-cyan/60"
        }`}
      >
        <p className="text-sm text-slate-200">
          {file ? file.name : "Arrastra un informe aquí"}
        </p>
        <p className="text-[11px] text-slate-500">
          {file
            ? `${(file.size / 1024).toFixed(0)} KB · pulsa para cambiar`
            : "o haz clic para seleccionar (PDF)"}
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          className="hidden"
          onChange={(event) => handleFiles(event.target.files)}
        />
      </div>

      <label className="flex flex-col gap-1 text-[11px] text-slate-400">
        Título del informe
        <input
          value={title}
          onChange={(event) => setTitle(event.target.value)}
          placeholder="p. ej. Dossier OSINT 2026-04"
          className="rounded border border-base-600 bg-base-950 px-2 py-1.5 text-sm text-slate-100 focus:border-neon-cyan focus:outline-none"
        />
      </label>

      <button
        type="button"
        onClick={() => void handleSubmit()}
        disabled={busy || !file}
        className="rounded border border-neon-violet/50 px-2 py-1.5 text-sm text-neon-violet transition hover:bg-neon-violet/10 disabled:opacity-40"
      >
        {busy ? "Subiendo…" : "Subir e ingerir"}
      </button>

      {error && (
        <p role="alert" className="rounded border border-neon-crimson/40 bg-neon-crimson/10 p-2 text-[11px] text-neon-crimson">
          {error}
        </p>
      )}

      {job && (
        <div className="flex flex-col gap-1 rounded border border-base-700 bg-base-950/60 p-2">
          <div className="flex items-center justify-between text-[11px]">
            <span className="font-mono text-slate-500">{job.job_id ?? "—"}</span>
            {status && (
              <span className={STATUS_COLOR[status]}>{STATUS_LABEL[status]}</span>
            )}
          </div>
          <div className="h-1.5 w-full rounded-full bg-base-700" role="presentation">
            <div
              className="h-1.5 rounded-full bg-neon-cyan transition-all"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <p className="text-right font-mono text-[10px] text-slate-500">{progressPct}%</p>

          {(job.pending_review ?? 0) > 0 && (
            <p
              role="status"
              className="rounded border border-neon-amber/40 bg-neon-amber/10 p-2 text-[11px] text-neon-amber"
            >
              PENDING_HUMAN_REVIEW · {job.pending_review} tripleta(s) de baja confianza
              requieren revisión humana.
            </p>
          )}
        </div>
      )}
    </section>
  );
}
