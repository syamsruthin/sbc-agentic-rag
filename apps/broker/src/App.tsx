import { useCallback, useEffect, useMemo, useState } from "react";

import {
  BROKER_STATES,
  ChatPanel,
  Layout,
  MARKETS,
  METAL_TIERS,
  Notice,
  PLAN_TYPES,
  PlanPicker,
  SelectedPlans,
  createApi,
  useChat,
  type Api,
  type PlanSummary,
} from "@sbc/chat-ui";

import { LOCAL_MODE, accessToken, signOut } from "./auth";
import { SignIn } from "./SignIn";
import { localThreads, rememberThread, type ThreadRef } from "./threads";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "/api";
const MAX_PLANS = 6; // config/bots/broker.yaml compare_max_plans

const SUGGESTIONS = [
  "What's the deductible on BTCU8?",
  "Compare the ER copays of the plans I picked",
  "Which of these plans covers chiropractic care?",
  "Find Gold PPO plans in Colorado",
];

function Threads({ api, current, onOpen }: { api: Api; current: string | null; onOpen(t: ThreadRef): void }) {
  const [threads, setThreads] = useState<ThreadRef[] | null>(null);
  const [shared, setShared] = useState(false);
  useEffect(() => {
    let live = true;
    api
      .get<{ threads: ThreadRef[]; shared_account?: boolean }>("/broker/threads")
      .then((r) => {
        if (!live) return;
        setShared(Boolean(r.shared_account));
        setThreads(r.shared_account ? localThreads() : r.threads);
      })
      .catch(() => live && setThreads([]));
    return () => {
      live = false;
    };
  }, [api, current]);

  if (threads === null) return <p className="text-sm text-slate-500">Loading…</p>;
  return (
    <div className="space-y-2">
      {shared && (
        <p className="text-xs text-slate-500">Shared demo account: only this browser's conversations are listed.</p>
      )}
      {threads.length === 0 && <p className="text-sm text-slate-500">No earlier conversations.</p>}
      <ul className="space-y-1">
        {threads.map((t) => (
          <li key={t.thread_id}>
            <button
              type="button"
              onClick={() => onOpen(t)}
              className={`w-full truncate rounded px-2 py-1.5 text-left text-sm hover:bg-slate-100 ${t.thread_id === current ? "bg-brand-50 text-brand-700" : "text-slate-700"}`}
            >
              {t.title || "Untitled"}
              <span className="block text-xs text-slate-400">{new Date(t.updated_at * 1000).toLocaleString()}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Broker({ onSignedOut }: { onSignedOut(): void }) {
  const api = useMemo(
    () => createApi({ base: API_BASE, getToken: accessToken, onUnauthorized: onSignedOut }),
    [onSignedOut],
  );
  const chat = useChat(api, "broker");
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [tab, setTab] = useState<"plans" | "threads">("plans");
  const [drawer, setDrawer] = useState(false);
  const [seed] = useState(() => new URLSearchParams(window.location.search).get("q"));

  // Remember demo threads in this browser (the API doesn't list them for the shared account).
  const first = chat.entries.find((e) => e.role === "user");
  useEffect(() => {
    if (chat.threadId && first?.role === "user") rememberThread(chat.threadId, first.text);
  }, [chat.threadId, first]);

  const side = (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="mb-3 flex gap-1 rounded-lg bg-slate-100 p-1 text-sm">
        {(["plans", "threads"] as const).map((t) => (
          <button
            key={t}
            type="button"
            onClick={() => setTab(t)}
            className={`flex-1 rounded-md px-2 py-1 ${tab === t ? "bg-white font-medium shadow-sm" : "text-slate-600"}`}
          >
            {t === "plans" ? `Plans (${plans.length})` : "Conversations"}
          </button>
        ))}
      </div>
      {tab === "plans" ? (
        <PlanPicker
          api={api}
          bot="broker"
          selected={plans}
          onChange={setPlans}
          max={MAX_PLANS}
          filters={[
            { key: "state", label: "State", options: BROKER_STATES },
            { key: "market", label: "Market", options: MARKETS },
            { key: "plan_type", label: "Plan type", options: PLAN_TYPES },
            { key: "metal", label: "Metal tier", options: METAL_TIERS },
          ]}
        />
      ) : (
        <Threads
          api={api}
          current={chat.threadId}
          onOpen={(t) => {
            chat.resume(t.thread_id);
            setDrawer(false);
          }}
        />
      )}
    </div>
  );

  return (
    <div className="flex min-h-0 flex-1 gap-4">
      <aside className="hidden w-80 shrink-0 flex-col md:flex">{side}</aside>
      {drawer && (
        <div className="fixed inset-0 z-20 flex flex-col bg-white p-4 md:hidden">
          <div className="mb-3 flex justify-end">
            <button type="button" className="text-sm font-medium text-brand-600" onClick={() => setDrawer(false)}>
              Done
            </button>
          </div>
          {side}
        </div>
      )}
      <section className="flex min-h-0 flex-1 flex-col">
        <div className="mb-2 flex items-center justify-between gap-2">
          <button
            type="button"
            className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm md:hidden"
            onClick={() => setDrawer(true)}
          >
            Plans ({plans.length})
          </button>
          <button
            type="button"
            className="ml-auto text-sm text-slate-600 hover:text-slate-900"
            onClick={() => {
              chat.reset();
              setPlans([]);
            }}
          >
            New conversation
          </button>
        </div>
        <ChatPanel
          api={api}
          chat={chat}
          suggestions={seed ? [seed, ...SUGGESTIONS] : SUGGESTIONS}
          sendOptions={() => ({ uiSelected: plans.map((p) => p.plan_key) })}
          onOpenPlanPicker={() => {
            setTab("plans");
            setDrawer(true);
          }}
          placeholder="Ask about plans, compare them, or check coverage…"
          csvExport
          showDetails
          header={<SelectedPlans plans={plans} onRemove={(k) => setPlans(plans.filter((p) => p.plan_key !== k))} />}
        />
      </section>
    </div>
  );
}

export function App() {
  const [user, setUser] = useState<string | null>(LOCAL_MODE ? "local" : null);
  const out = useCallback(() => {
    signOut();
    setUser(LOCAL_MODE ? "local" : null);
  }, []);
  return (
    <Layout
      title="Broker Assistant"
      subtitle={LOCAL_MODE ? "Local mode: no sign-in (APP_ENV=local)" : user ? `Signed in as ${user}` : undefined}
      actions={
        user &&
        !LOCAL_MODE && (
          <button type="button" className="text-sm text-slate-600 hover:text-slate-900" onClick={out}>
            Sign out
          </button>
        )
      }
    >
      <div className="mb-3">
        <Notice>
          Answers quote each plan's Summary of Benefits and Coverage; plan details marked “catalog” come from the
          insurer's plan catalog, not the SBC. Don't enter client names or other personal information.
        </Notice>
      </div>
      {user ? <Broker onSignedOut={out} /> : <SignIn onSignedIn={setUser} />}
    </Layout>
  );
}
