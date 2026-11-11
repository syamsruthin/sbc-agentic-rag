// Shapes of the API's JSON and server-sent events (docs/aws/MIGRATION_PLAN.md section 6.2).

export type Bot = "broker" | "public";

export interface Citation {
  number: number;
  label: string;
  url: string;
}

/** Where an answer came from, for the optional "details" footer (WP12). Never user text. */
export interface AnswerDetails {
  path: string | null;
  llm_calls: number;
  cost_usd: number;
  tools: string[];
}

export interface AnswerBody {
  /** `answer` (verified SBC facts), or `message` (a refusal, a limit or a notice with no plan facts). */
  kind: string;
  markdown: string;
  plan_keys: string[];
  citations: Citation[];
  cached?: boolean;
  details?: AnswerDetails;
}

export interface ClarifyOption {
  label: string;
  plan_key: string | null;
}

export interface ClarifyBody {
  question: string;
  open_plan_picker: boolean;
  options: ClarifyOption[];
}

/** A refused turn: HTTP 200 with a `limit` event, not a 429. */
export interface LimitBody {
  scope: string;
  retry_after_s: number;
  message: string;
}

export interface ErrorBody {
  code: string;
  message: string;
}

export type ChatEvent =
  | { type: "thread"; thread_id: string; request_id: string }
  | { type: "status"; stage: string; text: string }
  | { type: "answer"; body: AnswerBody }
  | { type: "clarify"; body: ClarifyBody }
  | { type: "limit"; body: LimitBody }
  | { type: "error"; body: ErrorBody }
  | { type: "done" };

/** A plan as the plan picker lists it. State, market, network, exchange, status and metal tier come from the
 * portal catalog, not from the SBC, so the UI labels them as catalog data (PLAN.md 3.3, rule 3). */
export interface PlanSummary {
  plan_key: string;
  plan_year: number;
  sbc_id: string;
  contract_code: string;
  plan_name: string;
  issuer: string;
  plan_type: string;
  coverage_period: string;
  coverage_for: string;
  state: string | null;
  market: string | null;
  network: string | null;
  exchange: string | null;
  plan_status: string | null;
  metal_tier: string | null;
  metal_tier_source: string | null;
  source_url: string;
  eoc_url: string;
}

export interface PlanSearchResult {
  plans: PlanSummary[];
  total: number;
}

export const FEEDBACK_REASONS = [
  { code: "wrong_number", label: "Wrong number" },
  { code: "wrong_plan", label: "Wrong plan" },
  { code: "missing_info", label: "Missing information" },
  { code: "unclear", label: "Unclear" },
  { code: "not_helpful", label: "Not helpful" },
  { code: "other", label: "Something else" },
] as const;

export type FeedbackReason = (typeof FEEDBACK_REASONS)[number]["code"];
