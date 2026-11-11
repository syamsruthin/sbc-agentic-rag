import { useEffect, useState } from "react";

interface Demo {
  username: string;
  password: string;
}

const SHOPPER_EXAMPLES = [
  "What's the deductible on a Silver plan?",
  "Compare the out-of-pocket limits of two Bronze plans",
  "Does this plan cover acupuncture?",
  "How much is a specialist visit?",
  "Which plan is best for me?",
];

const BROKER_EXAMPLES = [
  "What's the deductible on BTCU8?",
  "Compare the ER copays of 0JF1 and BTCU8",
  "Is bariatric surgery excluded on 0JF1?",
  "What does a generic drug cost on this plan?",
  "Can you diagnose my rash?",
];

function CopyField({ label, value }: { label: string; value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-slate-50 px-3 py-1.5">
      <span className="min-w-0 text-sm">
        <span className="text-slate-500">{label}: </span>
        <code className="select-all break-all">{value}</code>
      </span>
      <button
        type="button"
        className="shrink-0 text-xs font-medium text-brand-600 hover:underline"
        onClick={() =>
          void navigator.clipboard.writeText(value).then(() => {
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
          })
        }
      >
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

/** The shared demo-broker login. It's read at runtime from /landing/demo.json, which
 * scripts/aws/rotate-demo-password.sh rewrites, so rotating the password needs no rebuild (decision D2). */
function DemoLogin() {
  const [demo, setDemo] = useState<Demo | null | "missing">(null);
  useEffect(() => {
    fetch("/landing/demo.json", { cache: "no-store" })
      .then((r) => (r.ok ? (r.json() as Promise<Demo>) : Promise.reject(new Error(String(r.status)))))
      .then((d) => setDemo(d.username && d.password ? d : "missing"))
      .catch(() => setDemo("missing"));
  }, []);
  if (demo === null) return <p className="text-sm text-slate-500">Loading the demo login…</p>;
  if (demo === "missing") return <p className="text-sm text-slate-500">The demo login isn't available right now.</p>;
  return (
    <div className="space-y-1.5">
      <CopyField label="Username" value={demo.username} />
      <CopyField label="Password" value={demo.password} />
      <p className="text-xs text-slate-500">
        Shared demo account: history clears daily, and it has its own usage limits. Don't enter personal or health
        information.
      </p>
    </div>
  );
}

function Examples({ app, items }: { app: string; items: string[] }) {
  return (
    <div className="mt-4">
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-slate-500">Try these</p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((q) => (
          <a
            key={q}
            href={`${app}?q=${encodeURIComponent(q)}`}
            className="rounded-full border border-slate-200 bg-white px-2.5 py-1 text-xs text-slate-700 hover:border-brand-200 hover:bg-brand-50"
          >
            {q}
          </a>
        ))}
      </div>
    </div>
  );
}

export function HomePage() {
  return (
    <div className="space-y-8">
      <section className="max-w-3xl">
        <h1 className="text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
          Health-plan answers you can check
        </h1>
        <p className="mt-3 text-lg text-slate-600">
          Two assistants answer questions about roughly 9,700 health plans from their Summary of Benefits and Coverage.
          Every dollar amount and percentage is copied from the plan's own SBC, checked by code before it's shown, and
          linked to the page it came from.
        </p>
      </section>

      <section className="grid gap-6 md:grid-cols-2">
        <article className="flex flex-col rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-xl font-semibold">Plan Shopper</h2>
          <p className="mt-1 text-sm text-slate-600">
            For individuals: pick your state, find this year's Individual-market plans, and ask what they cover in
            plain language. No sign-in.
          </p>
          <a
            href="/shopper/"
            className="mt-4 inline-block self-start rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            Try Plan Shopper
          </a>
          <Examples app="/shopper/" items={SHOPPER_EXAMPLES} />
        </article>

        <article className="flex flex-col rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
          <h2 className="text-xl font-semibold">Broker Assistant</h2>
          <p className="mt-1 text-sm text-slate-600">
            For brokers and HR teams: every market and state, side-by-side comparisons of up to six plans, and CSV
            export. Sign in with the demo account below.
          </p>
          <a
            href="/broker/"
            className="mt-4 inline-block self-start rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-700"
          >
            Try Broker Assistant
          </a>
          <div className="mt-4">
            <DemoLogin />
          </div>
          <Examples app="/broker/" items={BROKER_EXAMPLES} />
        </article>
      </section>

      <section className="grid gap-4 text-sm text-slate-600 sm:grid-cols-3">
        <p>
          <strong className="block text-slate-900">The SBC's own words</strong>
          Numbers are filled in by code from the parsed SBC, never typed by the model, and a verifier rejects any
          answer with a number it can't trace.
        </p>
        <p>
          <strong className="block text-slate-900">The right plan, or a question</strong>
          When a question could mean more than one plan, the assistant asks instead of guessing.
        </p>
        <p>
          <strong className="block text-slate-900">Measured</strong>
          A golden set of 410 questions, each checked against the PDFs by a person, gates every change.{" "}
          <a href="#/quality" className="text-brand-600 underline">
            See the numbers
          </a>
          .
        </p>
      </section>
    </div>
  );
}
