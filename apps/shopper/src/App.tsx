import { useMemo, useState } from "react";

import {
  ApiError,
  ChatPanel,
  Layout,
  METAL_TIERS,
  Notice,
  PLAN_TYPES,
  PlanPicker,
  SHOPPER_STATES,
  SelectedPlans,
  createApi,
  useChat,
  type PlanSummary,
} from "@sbc/chat-ui";

import { SITE_KEY, Turnstile } from "./turnstile";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const MAX_PLANS = 3; // config/bots/public.yaml compare_max_plans

interface Session {
  token: string;
  state: string;
  expires_at: number;
}

const SUGGESTIONS = [
  "Which plans have the lowest deductible?",
  "What does a primary care visit cost?",
  "Compare the out-of-pocket limits of the plans I picked",
  "Is urgent care covered before the deductible?",
];

/** The question a "Try these" chip on the landing page passed in (?q=…), placed in the box, never auto-sent. */
function initialQuestion(): string | null {
  return new URLSearchParams(window.location.search).get("q");
}

function Start({ onStarted }: { onStarted(session: Session): void }) {
  const [state, setState] = useState("");
  const [token, setToken] = useState(SITE_KEY ? "" : "local-dev");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const api = useMemo(() => createApi({ base: API_BASE }), []);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const s = await api.post<Session>("/public/session", { turnstile_token: token, state });
      onStarted(s);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : "Couldn't start a session. Please try again.");
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto mt-6 w-full max-w-md rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <h2 className="text-lg font-semibold">Where do you live?</h2>
      <p className="mt-1 text-sm text-slate-600">
        Plans are sold by state. You'll see this year's Individual-market plans from the states this demo covers.
      </p>
      <select
        aria-label="Your state"
        value={state}
        onChange={(e) => setState(e.target.value)}
        className="mt-4 w-full rounded-lg border border-slate-300 px-3 py-2"
      >
        <option value="">Choose a state</option>
        {Object.entries(SHOPPER_STATES).map(([code, name]) => (
          <option key={code} value={code}>
            {name}
          </option>
        ))}
      </select>
      {SITE_KEY && (
        <div className="mt-4">
          <Turnstile onToken={setToken} onError={setError} />
        </div>
      )}
      {error && <p className="mt-3 rounded bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      <button
        type="button"
        disabled={!state || !token || busy}
        onClick={() => void start()}
        className="mt-4 w-full rounded-lg bg-brand-600 px-4 py-2.5 font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {busy ? "Starting…" : "Start"}
      </button>
      <p className="mt-4 text-xs text-slate-500">
        Please don't enter personal or health information. Conversations are deleted after 30 days.
      </p>
    </div>
  );
}

function Shopper({ session, onExpired }: { session: Session; onExpired(): void }) {
  const api = useMemo(
    () => createApi({ base: API_BASE, getToken: async () => session.token, onUnauthorized: onExpired }),
    [session.token, onExpired],
  );
  const chat = useChat(api, "public");
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [seed] = useState(initialQuestion);

  const picker = (
    <PlanPicker
      api={api}
      bot="public"
      selected={plans}
      onChange={setPlans}
      max={MAX_PLANS}
      // The API takes the state from the session token; it's also sent for local runs, which have no token.
      initial={{ state: session.state }}
      filters={[
        { key: "metal", label: "Metal tier", options: METAL_TIERS },
        { key: "plan_type", label: "Plan type", options: PLAN_TYPES },
      ]}
    />
  );

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <aside className="hidden w-80 shrink-0 flex-col md:flex">
        <p className="mb-2 text-sm font-medium text-slate-700">Pick up to {MAX_PLANS} plans to ask about</p>
        {picker}
      </aside>
      {pickerOpen && (
        <div className="fixed inset-0 z-20 flex flex-col bg-white p-4 md:hidden">
          <div className="mb-3 flex items-center justify-between">
            <p className="font-medium">Pick up to {MAX_PLANS} plans</p>
            <button type="button" className="text-sm font-medium text-brand-600" onClick={() => setPickerOpen(false)}>
              Done
            </button>
          </div>
          {picker}
        </div>
      )}
      <section className="flex min-h-0 flex-1 flex-col">
        <div className="mb-2 flex items-center justify-between gap-2 md:hidden">
          <button
            type="button"
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm"
            onClick={() => setPickerOpen(true)}
          >
            Plans ({plans.length})
          </button>
        </div>
        <ChatPanel
          api={api}
          chat={chat}
          suggestions={seed ? [seed, ...SUGGESTIONS] : SUGGESTIONS}
          sendOptions={() => ({ uiSelected: plans.map((p) => p.plan_key), userState: session.state })}
          onOpenPlanPicker={() => setPickerOpen(true)}
          placeholder="Ask about a plan's costs or coverage…"
          showDetails
          header={
            <SelectedPlans plans={plans} onRemove={(k) => setPlans(plans.filter((p) => p.plan_key !== k))} />
          }
        />
      </section>
    </div>
  );
}

export function App() {
  const [session, setSession] = useState<Session | null>(null);
  const expired = useMemo(() => () => setSession(null), []);
  return (
    <Layout
      title="Plan Shopper"
      subtitle={session ? `Individual plans in ${SHOPPER_STATES[session.state] ?? session.state}` : undefined}
      actions={
        session && (
          <button type="button" className="text-sm text-slate-600 hover:text-slate-900" onClick={expired}>
            Change state
          </button>
        )
      }
    >
      <div className="mb-3">
        <Notice>
          Answers quote each plan's Summary of Benefits and Coverage. They aren't advice; the plan's documents are the
          final word. Don't enter personal or health information.
        </Notice>
      </div>
      {session ? <Shopper session={session} onExpired={expired} /> : <Start onStarted={setSession} />}
    </Layout>
  );
}
