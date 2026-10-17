"use client";

/**
 * SearchConsole — buscador global + consola GraphRAG (transversal).
 *
 * Autorizado por: SPEC-05 §1/§7 y SPEC-03 §7 (pregunta en lenguaje natural,
 * respuesta `{ answer, subgraph, citations }`, siempre con citas). El `subgraph`
 * se reenvía al grafo (render de la respuesta) y las `citations` distinguen:
 *   · cita de **informe OSINT** (`report_id` + `page_number` + `exact_quote`)
 *   · cita de **análisis humano** (`analysis_id` + `exact_quote`)
 *
 * Traza a: `searchRag` (POST /search/rag). Sin evidencia → `200` con
 * `citations: []` (SPEC-03 §8).
 */

import { useState } from "react";

import { api, ApiError } from "@/services/api";
import type { RagResponse, Subgraph } from "@/services/api";

export interface SearchConsoleProps {
  /** Renderiza la respuesta en el grafo: subgrafo + ids resaltados. */
  onSubgraph?: (subgraph: Subgraph, highlightIds: string[]) => void;
  /** Abre la evidencia de un informe OSINT en el visor. */
  onOpenReport?: (reportId: string, pageNumber: number, quote: string) => void;
  /** Abre un análisis humano citado. */
  onOpenAnalysis?: (analysisId: string) => void;
}

interface Exchange {
  query: string;
  response: RagResponse;
}

const EXAMPLES: readonly string[] = [
  "¿Qué organizaciones controla el sujeto X?",
  "Relaciona los ciberactivos con sus titulares",
  "¿Quién financió las operaciones del grupo?",
];

function highlightIdsFrom(subgraph: Subgraph | undefined): string[] {
  if (!subgraph) return [];
  const nodeIds = (subgraph.nodes ?? []).map((node) => node.id);
  const edgeIds = (subgraph.edges ?? []).map((edge) => edge.id);
  return [...nodeIds, ...edgeIds];
}

export default function SearchConsole({
  onSubgraph,
  onOpenReport,
  onOpenAnalysis,
}: SearchConsoleProps): JSX.Element {
  const [input, setInput] = useState("");
  const [history, setHistory] = useState<Exchange[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (query: string): Promise<void> => {
    const trimmed = query.trim();
    if (!trimmed) return;
    setLoading(true);
    setError(null);
    try {
      const response = await api.searchRag({ query: trimmed });
      setHistory((previous) => [...previous, { query: trimmed, response }]);
      setInput("");
      onSubgraph?.(response.subgraph ?? {}, highlightIdsFrom(response.subgraph));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "La consulta GraphRAG falló.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <section
      aria-label="Buscador global y consola GraphRAG"
      className="flex flex-col gap-3 rounded-lg border border-base-700 bg-base-900/70 p-4"
    >
      <h2 className="text-xs font-semibold uppercase tracking-widest text-slate-400">
        Buscador / GraphRAG
      </h2>

      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          void submit(input);
        }}
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder="Pregunta en lenguaje natural…"
          maxLength={2000}
          aria-label="Consulta GraphRAG"
          className="min-w-0 flex-1 rounded border border-base-600 bg-base-950 px-3 py-2 text-sm text-slate-100 focus:border-neon-violet focus:outline-none"
        />
        <button
          type="submit"
          disabled={loading || input.trim().length === 0}
          className="rounded border border-neon-violet/50 px-3 py-2 text-sm text-neon-violet transition hover:bg-neon-violet/10 disabled:opacity-40"
        >
          {loading ? "Consultando…" : "Buscar"}
        </button>
      </form>

      {history.length === 0 && (
        <div className="flex flex-wrap gap-1.5">
          {EXAMPLES.map((example) => (
            <button
              key={example}
              type="button"
              onClick={() => void submit(example)}
              className="rounded border border-base-600 px-2 py-0.5 text-[11px] text-slate-400 transition hover:border-neon-cyan hover:text-neon-cyan"
            >
              {example}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p role="alert" className="rounded border border-neon-crimson/40 bg-neon-crimson/10 p-2 text-[11px] text-neon-crimson">
          {error}
        </p>
      )}

      {history.length > 0 && (
        <ol className="flex max-h-72 flex-col gap-3 overflow-y-auto">
          {history.map((exchange, index) => (
            <li key={`${exchange.query}-${index}`} className="flex flex-col gap-2">
              <p className="text-sm font-medium text-slate-200">› {exchange.query}</p>
              <div className="rounded border border-base-700 bg-base-950/60 p-3">
                <p className="whitespace-pre-wrap text-sm text-slate-300">
                  {exchange.response.answer}
                </p>

                {(exchange.response.subgraph?.nodes?.length ?? 0) > 0 && (
                  <button
                    type="button"
                    onClick={() =>
                      onSubgraph?.(
                        exchange.response.subgraph ?? {},
                        highlightIdsFrom(exchange.response.subgraph),
                      )
                    }
                    className="mt-2 rounded border border-neon-cyan/50 px-2 py-0.5 text-[11px] text-neon-cyan transition hover:bg-neon-cyan/10"
                  >
                    Ver en el grafo (
                    {exchange.response.subgraph?.nodes?.length ?? 0} nodos /{" "}
                    {exchange.response.subgraph?.edges?.length ?? 0} aristas)
                  </button>
                )}

                {exchange.response.citations.length > 0 ? (
                  <div className="mt-2">
                    <p className="mb-1 text-[11px] uppercase tracking-widest text-slate-500">
                      Citas de evidencia ({exchange.response.citations.length})
                    </p>
                    <ul className="flex flex-col gap-1.5">
                      {exchange.response.citations.map((citation, citationIndex) => {
                        const isReport = typeof citation.report_id === "string";
                        const isAnalysis = typeof citation.analysis_id === "string";
                        return (
                          <li
                            key={`${citation.report_id ?? citation.analysis_id ?? "c"}-${citationIndex}`}
                            className={`rounded border p-2 text-[11px] ${
                              isAnalysis
                                ? "border-neon-violet/40"
                                : "border-base-700"
                            }`}
                          >
                            <span
                              className={`mb-1 inline-block rounded border px-1.5 py-0.5 text-[9px] font-semibold uppercase ${
                                isAnalysis
                                  ? "border-neon-violet/50 text-neon-violet"
                                  : "border-neon-crimson/50 text-neon-crimson"
                              }`}
                            >
                              {isAnalysis ? "Análisis humano" : "Informe OSINT"}
                            </span>
                            <blockquote className="border-l-2 border-base-600 pl-2 italic text-slate-400">
                              “{citation.exact_quote}”
                            </blockquote>
                            <div className="mt-1 flex items-center justify-between text-slate-500">
                              <span className="font-mono">
                                {isReport
                                  ? `${citation.report_id?.slice(0, 8)}… · pág. ${citation.page_number ?? "—"}`
                                  : `${citation.analysis_id?.slice(0, 8)}…`}
                              </span>
                              {isReport && onOpenReport && (
                                <button
                                  type="button"
                                  onClick={() =>
                                    citation.report_id &&
                                    onOpenReport(
                                      citation.report_id,
                                      citation.page_number ?? 1,
                                      citation.exact_quote,
                                    )
                                  }
                                  className="rounded border border-base-600 px-1.5 py-0.5 text-slate-300 transition hover:border-neon-cyan hover:text-neon-cyan"
                                >
                                  Ver informe
                                </button>
                              )}
                              {isAnalysis && onOpenAnalysis && (
                                <button
                                  type="button"
                                  onClick={() =>
                                    citation.analysis_id && onOpenAnalysis(citation.analysis_id)
                                  }
                                  className="rounded border border-base-600 px-1.5 py-0.5 text-slate-300 transition hover:border-neon-violet hover:text-neon-violet"
                                >
                                  Ver análisis
                                </button>
                              )}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : (
                  <p className="mt-2 text-[11px] text-slate-500">
                    Sin evidencia citada (respuesta de no-evidencia, SPEC-03 §8).
                  </p>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
