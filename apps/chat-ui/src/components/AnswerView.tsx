import { useRef, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { downloadCsv, tableRows, toCsv } from "../csv";
import type { AnswerBody } from "../types";

function CsvTable({ children, filename }: { children?: ReactNode; filename: string }) {
  const ref = useRef<HTMLTableElement>(null);
  return (
    <div className="my-3">
      <div className="overflow-x-auto rounded border border-slate-200">
        <table ref={ref} className="min-w-full text-left text-sm">
          {children}
        </table>
      </div>
      <button
        type="button"
        className="mt-1 text-xs font-medium text-brand-600 hover:underline"
        onClick={() => ref.current && downloadCsv(filename, toCsv(tableRows(ref.current)))}
      >
        Export as CSV
      </button>
    </div>
  );
}

function markdownComponents(csv: boolean, filename: string): Components {
  return {
    // Raw HTML in the markdown is never rendered (react-markdown's default); links open in a new tab.
    a: ({ href, children }) => (
      <a href={href} target="_blank" rel="noopener noreferrer" className="text-brand-600 underline">
        {children}
      </a>
    ),
    table: ({ children }) =>
      csv ? (
        <CsvTable filename={filename}>{children}</CsvTable>
      ) : (
        <div className="my-3 overflow-x-auto rounded border border-slate-200">
          <table className="min-w-full text-left text-sm">{children}</table>
        </div>
      ),
    th: ({ children }) => <th className="border-b border-slate-200 bg-slate-50 px-3 py-2 font-semibold">{children}</th>,
    td: ({ children }) => <td className="border-b border-slate-100 px-3 py-2 align-top">{children}</td>,
    ul: ({ children }) => <ul className="my-2 list-disc pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="my-2 list-decimal pl-5">{children}</ol>,
    p: ({ children }) => <p className="my-2">{children}</p>,
    h1: ({ children }) => <h3 className="mt-3 font-semibold">{children}</h3>,
    h2: ({ children }) => <h3 className="mt-3 font-semibold">{children}</h3>,
    h3: ({ children }) => <h4 className="mt-3 font-semibold">{children}</h4>,
  };
}

export interface AnswerViewProps {
  answer: AnswerBody;
  /** Offer "Export as CSV" under each table (Broker Assistant). */
  csvExport?: boolean;
  /** Show where the answer came from: path, model calls, cost (WP12's optional details footer). */
  showDetails?: boolean;
}

export function AnswerView({ answer, csvExport = false, showDetails = false }: AnswerViewProps) {
  const filename = `sbc-comparison-${new Date().toISOString().slice(0, 10)}.csv`;
  const d = answer.details;
  return (
    <div className="text-[15px] leading-relaxed text-slate-800">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents(csvExport, filename)}>
        {answer.markdown}
      </ReactMarkdown>
      {answer.citations.length > 0 && (
        <div className="mt-3 border-t border-slate-100 pt-2">
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Sources</p>
          <ol className="mt-1 space-y-1 text-sm">
            {answer.citations.map((c) => (
              <li key={c.number} className="flex gap-2">
                <span className="text-slate-500">[{c.number}]</span>
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noopener noreferrer" className="text-brand-600 underline">
                    {c.label}
                  </a>
                ) : (
                  <span>{c.label}</span>
                )}
              </li>
            ))}
          </ol>
        </div>
      )}
      {showDetails && (d || answer.cached) && (
        <details className="mt-2 text-xs text-slate-500">
          <summary className="cursor-pointer select-none">Details</summary>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            {answer.cached && (
              <>
                <dt>Source</dt>
                <dd>Answer cache (no model call)</dd>
              </>
            )}
            {d && (
              <>
                <dt>Path</dt>
                <dd>{d.path ?? "n/a"}</dd>
                <dt>Model calls</dt>
                <dd>{d.llm_calls}</dd>
                <dt>Cost</dt>
                <dd>${d.cost_usd.toFixed(4)}</dd>
                {d.tools.length > 0 && (
                  <>
                    <dt>Tools</dt>
                    <dd>{d.tools.join(", ")}</dd>
                  </>
                )}
              </>
            )}
          </dl>
        </details>
      )}
    </div>
  );
}
