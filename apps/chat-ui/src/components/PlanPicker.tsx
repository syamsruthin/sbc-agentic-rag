import { useEffect, useState } from "react";

import { ApiError, type Api } from "../api";
import type { Bot, PlanSearchResult, PlanSummary } from "../types";

export interface PlanFilters {
  q: string;
  state: string;
  market: string;
  plan_type: string;
  metal: string;
}

export interface FilterChoice {
  key: Exclude<keyof PlanFilters, "q">;
  label: string;
  options: string[];
}

export interface PlanPickerProps {
  api: Api;
  bot: Bot;
  selected: PlanSummary[];
  onChange(selected: PlanSummary[]): void;
  max: number;
  /** The filters to offer (the public bot's state is fixed by its session, so it offers none). */
  filters?: FilterChoice[];
  initial?: Partial<PlanFilters>;
}

const EMPTY: PlanFilters = { q: "", state: "", market: "", plan_type: "", metal: "" };

/** Catalog data (state, market, metal tier…) is labeled as such: it isn't SBC text (PLAN.md 3.3, rule 3). */
function CatalogFacts({ plan }: { plan: PlanSummary }) {
  const bits = [plan.state, plan.market, plan.metal_tier, plan.plan_status].filter(Boolean);
  return (
    <span className="text-xs text-slate-500">
      {plan.contract_code} · {plan.plan_year}
      {bits.length > 0 && (
        <>
          {" · "}
          <span title="From the insurer's plan catalog, not from the SBC">{bits.join(" · ")} (catalog)</span>
        </>
      )}
    </span>
  );
}

export function PlanPicker({ api, bot, selected, onChange, max, filters = [], initial }: PlanPickerProps) {
  const [form, setForm] = useState<PlanFilters>({ ...EMPTY, ...initial });
  const [query, setQuery] = useState<PlanFilters>({ ...EMPTY, ...initial });
  const [result, setResult] = useState<PlanSearchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setQuery(form), 300);
    return () => clearTimeout(t);
  }, [form]);

  useEffect(() => {
    let live = true;
    setLoading(true);
    api
      .get<PlanSearchResult>(`/${bot}/plans/search`, { ...query, limit: 30 })
      .then((r) => live && (setResult(r), setError(null)))
      .catch((e: unknown) => live && setError(e instanceof ApiError ? e.detail : "Couldn't load plans."))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [api, bot, query]);

  const chosen = new Set(selected.map((p) => p.plan_key));
  function toggle(plan: PlanSummary) {
    if (chosen.has(plan.plan_key)) onChange(selected.filter((p) => p.plan_key !== plan.plan_key));
    else if (selected.length < max) onChange([...selected, plan]);
  }

  return (
    <div className="flex h-full min-h-0 flex-col gap-3">
      <input
        type="search"
        value={form.q}
        maxLength={100}
        onChange={(e) => setForm({ ...form, q: e.target.value })}
        placeholder="Search plan names or contract codes"
        aria-label="Search plans"
        className="rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-brand-500 focus:outline-none"
      />
      {filters.length > 0 && (
        <div className="grid grid-cols-2 gap-2">
          {filters.map((f) => (
            <select
              key={f.key}
              aria-label={f.label}
              value={form[f.key]}
              onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
              className="rounded-lg border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="">{f.label}: any</option>
              {f.options.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </select>
          ))}
        </div>
      )}
      <p className="text-xs text-slate-500">
        {selected.length}/{max} selected
        {result && ` · ${result.total} match${result.total === 1 ? "" : "es"}`}
        {loading && " · loading…"}
      </p>
      {error && <p className="rounded bg-amber-50 p-2 text-sm text-amber-900">{error}</p>}
      <ul className="min-h-0 flex-1 divide-y divide-slate-100 overflow-y-auto rounded-lg border border-slate-200 bg-white">
        {result?.plans.map((p) => {
          const on = chosen.has(p.plan_key);
          return (
            <li key={p.plan_key}>
              <label className={`flex cursor-pointer gap-3 px-3 py-2 hover:bg-slate-50 ${on ? "bg-brand-50" : ""}`}>
                <input
                  type="checkbox"
                  checked={on}
                  disabled={!on && selected.length >= max}
                  onChange={() => toggle(p)}
                  className="mt-1"
                />
                <span className="flex flex-col">
                  <span className="text-sm font-medium text-slate-800">{p.plan_name}</span>
                  <CatalogFacts plan={p} />
                </span>
              </label>
            </li>
          );
        })}
        {result && result.plans.length === 0 && <li className="p-3 text-sm text-slate-500">No plans match.</li>}
      </ul>
    </div>
  );
}

export function SelectedPlans({ plans, onRemove }: { plans: PlanSummary[]; onRemove(planKey: string): void }) {
  if (plans.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5 pb-2">
      {plans.map((p) => (
        <span
          key={p.plan_key}
          className="inline-flex max-w-full items-center gap-1 rounded-full bg-brand-50 px-2.5 py-1 text-xs text-brand-700"
        >
          <span className="truncate">{p.plan_name}</span>
          <button
            type="button"
            aria-label={`Remove ${p.plan_name}`}
            onClick={() => onRemove(p.plan_key)}
            className="font-bold text-brand-500 hover:text-brand-900"
          >
            ×
          </button>
        </span>
      ))}
    </div>
  );
}
