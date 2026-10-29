"use client";

/**
 * ReportViewer — visor de PDF de un informe OSINT con subrayado de evidencia.
 *
 * Autorizado por: SPEC-05 §3 (visor de PDF que subraya `exact_quote` usando
 * `page_number` y `/reports/{id}/chunks`) y SPEC-02 §2 (`char_start`/`char_end`
 * son offsets **relativos a la página**).
 * Traza a: `getReport`, `getReportFile`, `getReportChunks`.
 *
 * Sólo se usa para evidencia de **informe OSINT** (traza doble: `source_report_id`
 * + `page_number` + `exact_quote`, Regla 4). La evidencia de **análisis humano**
 * la representa `AnalysisCitationCard` (no hay PDF).
 */

import type { ReactElement } from "react";
import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError, api } from "@/services/api";
import type { Chunk, Report } from "@/services/api";

export interface ReportViewerProps {
  reportId: string;
  /** Página del informe a la que pertenece la evidencia (`page_number`). */
  pageNumber?: number;
  /** `exact_quote`: fragmento exacto a subrayar. */
  quote?: string | null;
}

interface LocatedEvidence {
  chunk: Chunk;
  before: string;
  match: string;
  after: string;
  /** true si el match provino de normalizar espacios (búsqueda aproximada). */
  approximate: boolean;
}

const REPORT_SOURCE_LABEL: Record<NonNullable<Report["source_type"]>, string> = {
  OSINT_PDF: "Informe OSINT (PDF)",
  NEWS: "Noticia",
  TELEGRAM: "Telegram",
  RSS: "RSS",
  MANUAL: "Manual",
  PUBLIC_REGISTRY: "Registro público",
};

function locateWithinChunk(chunk: Chunk, needle: string): Omit<LocatedEvidence, "chunk"> | null {
  const text = chunk.text ?? "";
  if (!needle) return null;

  const exactIndex = text.indexOf(needle);
  if (exactIndex >= 0) {
    return {
      before: text.slice(0, exactIndex),
      match: text.slice(exactIndex, exactIndex + needle.length),
      after: text.slice(exactIndex + needle.length),
      approximate: false,
    };
  }

  // Respaldo tolerante a espacios/saltos de línea distintos entre cita y chunk.
  const normalizedText = text.replace(/\s+/g, " ");
  const normalizedNeedle = needle.replace(/\s+/g, " ").trim();
  const normalizedIndex = normalizedText.indexOf(normalizedNeedle);
  if (normalizedIndex >= 0) {
    return {
      before: normalizedText.slice(0, normalizedIndex),
      match: normalizedText.slice(normalizedIndex, normalizedIndex + normalizedNeedle.length),
      after: normalizedText.slice(normalizedIndex + normalizedNeedle.length),
      approximate: true,
    };
  }

  return null;
}

export default function ReportViewer({
  reportId,
  pageNumber,
  quote,
}: ReportViewerProps): ReactElement {
  const [report, setReport] = useState<Report | null>(null);
  const [chunks, setChunks] = useState<Chunk[]>([]);
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState<string | null>(null);
  const objectUrlRef = useRef<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setStatus("loading");
    setError(null);

    (async () => {
      try {
        const [meta, chunkList, blob] = await Promise.all([
          api.getReport(reportId),
          api.getReportChunks(reportId),
          api.getReportFile(reportId),
        ]);
        if (cancelled) return;

        const url = URL.createObjectURL(blob);
        if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
        objectUrlRef.current = url;

        setReport(meta);
        setChunks(chunkList.items ?? []);
        setFileUrl(url);
        setStatus("ready");
      } catch (err) {
        if (cancelled) return;
        setError(err instanceof ApiError ? err.message : "No se pudo cargar el informe original.");
        setStatus("error");
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [reportId]);

  useEffect(
    () => () => {
      if (objectUrlRef.current) URL.revokeObjectURL(objectUrlRef.current);
    },
    [],
  );

  const needle = (quote ?? "").trim();

  const evidence = useMemo<LocatedEvidence | null>(() => {
    if (!needle) return null;

    const onPage =
      typeof pageNumber === "number"
        ? chunks.filter((chunk) => chunk.page_number === pageNumber)
        : [];
    const pool = onPage.length > 0 ? onPage : chunks;

    for (const chunk of pool) {
      const located = locateWithinChunk(chunk, needle);
      if (located) return { chunk, ...located };
    }
    return null;
  }, [needle, chunks, pageNumber]);

  const targetPage = evidence?.chunk.page_number ?? pageNumber ?? 1;

  return (
    <div className="flex h-full flex-col gap-2">
      <header className="flex items-start justify-between gap-2 border-b border-base-700 pb-2">
        <div className="min-w-0">
          <p className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-neon-crimson">
            <span className="rounded border border-neon-crimson/50 px-1.5 py-0.5">
              Cita de informe OSINT
            </span>
            {report?.ingested_by_role === "AI_INFORMANT" && (
              <span className="rounded border border-neon-amber/50 px-1.5 py-0.5 text-neon-amber">
                Informante IA
              </span>
            )}
          </p>
          <p className="mt-1 truncate text-sm font-semibold text-slate-100">
            {report?.title ?? "Informe original"}
          </p>
          <p className="truncate font-mono text-[11px] text-slate-400">
            {reportId} · pág. {targetPage}
            {report?.source_type ? ` · ${REPORT_SOURCE_LABEL[report.source_type]}` : ""}
            {report?.reliability_rating ? ` · fiabilidad ${report.reliability_rating}` : ""}
          </p>
        </div>
        {fileUrl && (
          <a
            href={fileUrl}
            target="_blank"
            rel="noreferrer"
            className="shrink-0 rounded border border-base-600 px-2 py-1 text-[11px] text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan"
          >
            Abrir PDF
          </a>
        )}
      </header>

      {status === "loading" && (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
          Cargando informe y evidencia…
        </div>
      )}

      {status === "error" && (
        <div
          role="alert"
          className="flex flex-1 items-center justify-center rounded border border-neon-crimson/40 bg-neon-crimson/10 p-4 text-center text-sm text-neon-crimson"
        >
          {error}
        </div>
      )}

      {status === "ready" && (
        <>
          <div className="min-h-0 flex-1 overflow-hidden rounded-md border border-base-700 bg-base-900">
            {fileUrl ? (
              <iframe
                title={report?.title ?? "Visor de informe"}
                src={`${fileUrl}#page=${targetPage}&view=FitH`}
                className="h-full w-full"
              />
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-slate-400">
                El informe no tiene archivo asociado.
              </div>
            )}
          </div>

          <section
            aria-label="Evidencia textual subrayada"
            className="max-h-44 shrink-0 overflow-auto rounded-md border border-base-700 bg-base-950 p-3"
          >
            {needle ? (
              evidence ? (
                <>
                  <p className="mb-2 text-[11px] uppercase tracking-widest text-slate-400">
                    Evidencia · pág. {evidence.chunk.page_number ?? "—"}
                    {typeof evidence.chunk.char_start === "number" &&
                      ` · [${evidence.chunk.char_start}:${evidence.chunk.char_end ?? "?"}]`}
                    {evidence.approximate && " · coincidencia aproximada"}
                  </p>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-300">
                    {evidence.before}
                    <mark className="rounded bg-neon-cyan/30 px-0.5 text-neon-cyan underline decoration-neon-cyan decoration-2">
                      {evidence.match}
                    </mark>
                    {evidence.after}
                  </p>
                </>
              ) : (
                <p className="text-sm text-slate-400">
                  Fragmento de evidencia no localizable en los chunks del informe.
                </p>
              )
            ) : (
              <p className="text-sm text-slate-400">
                Selecciona una relación para ver su evidencia subrayada.
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
