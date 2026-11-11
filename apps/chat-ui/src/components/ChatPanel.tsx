import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";

import type { Api } from "../api";
import type { Chat, SendOptions } from "../useChat";
import type { AssistantEntry } from "../useChat";
import { AnswerView } from "./AnswerView";
import { Feedback } from "./Feedback";
import { ReportLink } from "./ReportLink";

export interface ChatPanelProps {
  api: Api;
  chat: Chat;
  /** Extra fields for each turn (the selected plans, the visitor's state). */
  sendOptions?: () => SendOptions;
  placeholder?: string;
  /** Example questions shown before the first turn. */
  suggestions?: string[];
  csvExport?: boolean;
  showDetails?: boolean;
  /** A clarifying question asked the user to pick a plan. */
  onOpenPlanPicker?: () => void;
  /** Shown above the input (selected plans, notices). */
  header?: ReactNode;
}

function AssistantMessage({
  entry,
  props,
}: {
  entry: AssistantEntry;
  props: ChatPanelProps;
}) {
  const { api, chat, csvExport, showDetails, onOpenPlanPicker } = props;
  const last = chat.entries[chat.entries.length - 1]?.id === entry.id;
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      {entry.pending && entry.status && (
        <p className="flex items-center gap-2 text-sm text-slate-500" role="status">
          <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-brand-500" />
          {entry.status}
        </p>
      )}
      {entry.answer && <AnswerView answer={entry.answer} csvExport={csvExport} showDetails={showDetails} />}
      {entry.clarify && (
        <div>
          <p className="text-[15px] text-slate-800">{entry.clarify.question}</p>
          <div className="mt-2 flex flex-wrap gap-2">
            {entry.clarify.options.map((o) => (
              <button
                key={`${o.label}-${o.plan_key ?? ""}`}
                type="button"
                disabled={!last || chat.busy}
                className="rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-sm text-brand-700 hover:bg-brand-100 disabled:opacity-50"
                onClick={() => void chat.choose(o)}
              >
                {o.label}
              </button>
            ))}
            {entry.clarify.open_plan_picker && onOpenPlanPicker && (
              <button
                type="button"
                disabled={!last || chat.busy}
                className="rounded-full border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                onClick={onOpenPlanPicker}
              >
                Pick a plan…
              </button>
            )}
          </div>
        </div>
      )}
      {entry.limit && (
        <p className="rounded bg-amber-50 p-3 text-sm text-amber-900">
          {entry.limit.message}
          {entry.limit.retry_after_s > 0 && ` (try again in about ${Math.ceil(entry.limit.retry_after_s / 60)} min)`}
        </p>
      )}
      {entry.error && entry.error.code !== "stopped" && (
        <p className="rounded bg-red-50 p-3 text-sm text-red-800">{entry.error.message}</p>
      )}
      {entry.error?.code === "stopped" && <p className="text-sm text-slate-500">Stopped.</p>}
      {!entry.pending && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          {entry.answer?.kind === "answer" && chat.threadId && entry.requestId ? (
            <Feedback api={api} bot={chat.bot} threadId={chat.threadId} requestId={entry.requestId} />
          ) : (
            <span />
          )}
          <ReportLink requestId={entry.requestId} />
        </div>
      )}
    </div>
  );
}

export function ChatPanel(props: ChatPanelProps) {
  const { chat, sendOptions, placeholder, suggestions, header } = props;
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [chat.entries]);

  function submit(message: string) {
    if (!message.trim() || chat.busy) return;
    setText("");
    void chat.send(message, sendOptions?.());
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit(text);
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      submit(text);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-1 pb-4">
        {chat.entries.length === 0 && suggestions && suggestions.length > 0 && (
          <div>
            <p className="mb-2 text-sm text-slate-500">Try one of these:</p>
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="rounded-full border border-slate-200 bg-white px-3 py-1.5 text-left text-sm text-slate-700 hover:border-brand-200 hover:bg-brand-50"
                  onClick={() => submit(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}
        {chat.threadId && chat.entries.length === 0 && (
          <p className="text-sm text-slate-500">
            Continuing an earlier conversation. Its messages aren't shown here, but the assistant remembers them.
          </p>
        )}
        {chat.entries.map((entry) =>
          entry.role === "user" ? (
            <div key={entry.id} className="flex justify-end">
              <p className="max-w-[85%] whitespace-pre-wrap rounded-lg bg-brand-600 px-4 py-2 text-[15px] text-white">
                {entry.text}
              </p>
            </div>
          ) : (
            <AssistantMessage key={entry.id} entry={entry} props={props} />
          ),
        )}
        <div ref={end} />
      </div>
      {header}
      <form onSubmit={onSubmit} className="flex items-end gap-2 border-t border-slate-200 pt-3">
        <label className="sr-only" htmlFor="chat-input">
          Your question
        </label>
        <textarea
          id="chat-input"
          rows={2}
          maxLength={2000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={placeholder ?? "Ask about a plan's benefits…"}
          className="min-h-[2.75rem] flex-1 resize-none rounded-lg border border-slate-300 px-3 py-2 text-[15px] focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-100"
        />
        {chat.busy ? (
          <button
            type="button"
            onClick={chat.stop}
            className="rounded-lg border border-slate-300 px-4 py-2.5 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            Stop
          </button>
        ) : (
          <button
            type="submit"
            disabled={!text.trim()}
            className="rounded-lg bg-brand-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-brand-700 disabled:opacity-50"
          >
            Ask
          </button>
        )}
      </form>
    </div>
  );
}
