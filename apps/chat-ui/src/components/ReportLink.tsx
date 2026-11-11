const DEFAULT_REPORT_URL = "https://github.com/syamsruthin/sbc-agentic-rag/issues/new";

/** "Report a problem", carrying the request ID so the turn can be found in the logs (WP12). Only the ID is
 * sent: never the question or the answer. */
export function ReportLink({ requestId }: { requestId: string | null }) {
  if (!requestId) return null;
  const base = import.meta.env.VITE_REPORT_URL || DEFAULT_REPORT_URL;
  const url = new URL(base);
  url.searchParams.set("title", `Problem with an answer (request ${requestId})`);
  url.searchParams.set(
    "body",
    `Request ID: ${requestId}\n\nWhat went wrong? (Please don't include personal or health information.)\n`,
  );
  return (
    <span className="text-xs text-slate-400">
      <a href={url.toString()} target="_blank" rel="noopener noreferrer" className="hover:underline">
        Report a problem
      </a>{" "}
      · ID <code className="select-all">{requestId}</code>
    </span>
  );
}
