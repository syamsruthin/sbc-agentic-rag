// Every number here is a recorded result (TODO.md, docs/aws/DEPLOYMENT_ANALYSIS.md). Update them from the eval
// reports; never estimate them.

const RETRIEVAL = [
  { setup: "Hybrid search + rerank (deployed)", r5: "0.920", r10: "0.962", mrr: "0.730", cp: "0.724", cr: "0.934" },
  { setup: "Hybrid search, no rerank", r5: "0.900", r10: "0.945", mrr: "0.783", cp: "0.776", cr: "0.916" },
  { setup: "Keyword (BM25) only", r5: "0.847", r10: "0.952", mrr: "0.712", cp: "0.701", cr: "0.915" },
];

function Stat({ value, label }: { value: string; label: string }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="text-2xl font-bold text-slate-900">{value}</p>
      <p className="text-sm text-slate-600">{label}</p>
    </div>
  );
}

export function QualityPage() {
  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-bold text-slate-900">Quality and cost</h1>
        <p className="mt-2 max-w-3xl text-slate-600">
          The golden set has 410 questions across lookups, comparisons, coverage checks, cost scenarios, multi-turn
          conversations and questions that should be refused. A person checked every expected answer against the SBC
          PDFs.
        </p>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Plan safety</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Stat value="100%" label="Search results from the right plan (plan-filter correctness)" />
          <Stat value="0" label="Confident answers about the wrong plan" />
          <Stat value="98.0%" label="Questions mapped to the right SBC row" />
        </div>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Retrieval (golden set 0.2.0, plan year 2027)</h2>
        <div className="mt-3 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full text-left text-sm">
            <thead className="bg-slate-50">
              <tr>
                {["Setup", "Recall@5", "Recall@10", "MRR@10", "Context precision", "Context recall"].map((h) => (
                  <th key={h} className="px-3 py-2 font-semibold text-slate-700">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {RETRIEVAL.map((r) => (
                <tr key={r.setup} className="border-t border-slate-100">
                  <td className="px-3 py-2">{r.setup}</td>
                  <td className="px-3 py-2">{r.r5}</td>
                  <td className="px-3 py-2">{r.r10}</td>
                  <td className="px-3 py-2">{r.mrr}</td>
                  <td className="px-3 py-2">{r.cp}</td>
                  <td className="px-3 py-2">{r.cr}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-2 text-sm text-slate-600">
          Answer quality is gated on exact numeric match, zero untraceable numbers, the right plan and the right
          behavior. The full answer-quality run on the deployed models is still to come; its results will go here.
        </p>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">Cost</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <Stat value="≈ $0.003" label="Model cost of a simple lookup (fast path, measured)" />
          <Stat value="$0.02–0.04" label="Model cost of a question that needs tools (agent loop, measured)" />
          <Stat value="≈ $0.40" label="AWS cost a month when idle (estimate)" />
        </div>
        <p className="mt-2 text-sm text-slate-600">
          Repeated first questions are answered from a cache with no model call. Open an answer's “Details” to see its
          path, model calls and cost.
        </p>
      </section>
    </div>
  );
}
