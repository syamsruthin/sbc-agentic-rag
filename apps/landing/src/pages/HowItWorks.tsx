import type { ReactNode } from "react";

function Step({ title, children }: { title: string; children: ReactNode }) {
  return (
    <li className="relative rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <p className="font-semibold text-slate-900">{title}</p>
      <p className="mt-1 text-sm text-slate-600">{children}</p>
    </li>
  );
}

function Box({ title, note, tone = "slate" }: { title: string; note?: string; tone?: "slate" | "brand" | "amber" }) {
  const tones = {
    slate: "border-slate-300 bg-white",
    brand: "border-brand-200 bg-brand-50",
    amber: "border-amber-200 bg-amber-50",
  };
  return (
    <div className={`rounded-lg border px-3 py-2 text-center text-sm ${tones[tone]}`}>
      <p className="font-medium text-slate-900">{title}</p>
      {note && <p className="text-xs text-slate-500">{note}</p>}
    </div>
  );
}

const Arrow = () => (
  <div aria-hidden className="flex items-center justify-center text-slate-400">
    <span className="hidden md:inline">→</span>
    <span className="md:hidden">↓</span>
  </div>
);

export function HowItWorksPage() {
  return (
    <div className="space-y-10">
      <section>
        <h1 className="text-2xl font-bold text-slate-900">How it works</h1>
        <p className="mt-2 max-w-3xl text-slate-600">
          Each SBC PDF is converted, checked cell by cell against the PDF, and parsed into a facts database and a
          search index, built offline into one SQLite file. At question time, an agent built with LangGraph decides
          which plan you mean, fetches facts and passages for that plan only, and drafts an answer with placeholders
          that code fills in.
        </p>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">One question, step by step</h2>
        <ol className="mt-3 grid gap-3 md:grid-cols-3">
          <Step title="1. Ingress">
            Identifiers people type (emails, phone numbers, SSNs, member IDs, dates) are redacted before anything is stored or sent to a model.
          </Step>
          <Step title="2. Classify">
            A small model sorts the question: a plan lookup, a comparison, a coverage check, a cost scenario, or out of
            scope.
          </Step>
          <Step title="3. Resolve the plan">
            Code, not the model, matches the plans you picked or named. If more than one could fit, the assistant asks.
          </Step>
          <Step title="4. Fast path or agent loop">
            Simple lookups read the facts directly. Harder questions run tools (find, compare, search the SBC text),
            always filtered to the resolved plans.
          </Step>
          <Step title="5. Verify">
            The model writes placeholders such as {"{{F1}}"} instead of numbers. A verifier rejects any number that
            isn't from the facts, retries once, then falls back to a template answer.
          </Step>
          <Step title="6. Render">
            Code fills each placeholder with the SBC's exact wording, adds required limitations and a citation to the
            PDF page, and appends the disclaimer.
          </Step>
        </ol>
      </section>

      <section>
        <h2 className="text-lg font-semibold text-slate-900">On AWS</h2>
        <div className="mt-3 grid items-center gap-2 md:grid-cols-[1fr_auto_1fr_auto_1fr]">
          <Box title="Your browser" note="React apps" />
          <Arrow />
          <Box title="CloudFront" note="one domain for apps and API" tone="brand" />
          <Arrow />
          <div className="grid gap-2">
            <Box title="S3" note="landing, broker and shopper apps" />
            <Box title="Lambda (arm64)" note="FastAPI + LangGraph, streamed with SSE" tone="brand" />
          </div>
        </div>
        <div className="mt-3 grid gap-2 sm:grid-cols-2 md:grid-cols-4">
          <Box title="Facts bundle" note="SQLite in the container image" />
          <Box title="DynamoDB" note="sessions, limits, checkpoints, audit" />
          <Box title="Cognito" note="broker sign-in" />
          <Box title="OpenRouter" note="models with zero data retention" tone="amber" />
        </div>
        <p className="mt-3 max-w-3xl text-sm text-slate-600">
          There are no servers to keep running, no VPC and no database cluster: the site costs cents a month when idle.
          The offline data build runs on demand on ECS Fargate Spot. Usage limits per user, per session and per IP,
          a global daily cap and hard spending limits on the model keys bound what the public demo can cost.
        </p>
      </section>
    </div>
  );
}
