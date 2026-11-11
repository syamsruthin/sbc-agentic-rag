import { useState } from "react";

import type { Api } from "../api";
import { FEEDBACK_REASONS, type Bot, type FeedbackReason } from "../types";

export interface FeedbackProps {
  api: Api;
  bot: Bot;
  threadId: string;
  requestId: string;
}

/** Thumbs up or down with reason codes only: no free text, so no personal data can be typed into it. */
export function Feedback({ api, bot, threadId, requestId }: FeedbackProps) {
  const [state, setState] = useState<"idle" | "choosing" | "sent" | "failed">("idle");
  const [reasons, setReasons] = useState<FeedbackReason[]>([]);

  async function submit(rating: "up" | "down", chosen: FeedbackReason[]) {
    try {
      await api.post(`/${bot}/feedback`, { thread_id: threadId, request_id: requestId, rating, reasons: chosen });
      setState("sent");
    } catch {
      setState("failed");
    }
  }

  if (state === "sent") return <p className="text-xs text-slate-500">Thanks for the feedback.</p>;
  if (state === "failed") return <p className="text-xs text-slate-500">Couldn't send feedback.</p>;
  if (state === "choosing") {
    return (
      <div className="flex flex-wrap items-center gap-1.5 text-xs">
        <span className="text-slate-500">What was wrong?</span>
        {FEEDBACK_REASONS.map((r) => {
          const on = reasons.includes(r.code);
          return (
            <button
              key={r.code}
              type="button"
              aria-pressed={on}
              className={`rounded-full border px-2 py-0.5 ${on ? "border-brand-500 bg-brand-50 text-brand-700" : "border-slate-200 text-slate-600"}`}
              onClick={() => setReasons(on ? reasons.filter((x) => x !== r.code) : [...reasons, r.code])}
            >
              {r.label}
            </button>
          );
        })}
        <button
          type="button"
          className="rounded bg-slate-800 px-2 py-0.5 font-medium text-white"
          onClick={() => void submit("down", reasons)}
        >
          Send
        </button>
      </div>
    );
  }
  return (
    <div className="flex items-center gap-1 text-xs text-slate-500">
      <span>Helpful?</span>
      <button type="button" aria-label="Helpful" className="rounded px-1.5 hover:bg-slate-100" onClick={() => void submit("up", [])}>
        👍
      </button>
      <button type="button" aria-label="Not helpful" className="rounded px-1.5 hover:bg-slate-100" onClick={() => setState("choosing")}>
        👎
      </button>
    </div>
  );
}
