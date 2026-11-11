// One conversation with one bot: sends turns, follows the SSE stream and keeps the transcript.

import { useCallback, useRef, useState } from "react";

import { ApiError, type Api } from "./api";
import type { AnswerBody, Bot, ClarifyBody, ClarifyOption, ErrorBody, LimitBody } from "./types";

export interface UserEntry {
  id: string;
  role: "user";
  text: string;
}

export interface AssistantEntry {
  id: string;
  role: "assistant";
  requestId: string | null;
  status: string | null;
  pending: boolean;
  answer?: AnswerBody;
  clarify?: ClarifyBody;
  limit?: LimitBody;
  error?: ErrorBody;
}

export type Entry = UserEntry | AssistantEntry;

export interface SendOptions {
  uiSelected?: string[];
  userState?: string | null;
}

export interface Chat {
  bot: Bot;
  entries: Entry[];
  threadId: string | null;
  busy: boolean;
  send(message: string, options?: SendOptions): Promise<void>;
  choose(option: ClarifyOption): Promise<void>;
  /** Continue an earlier thread (the API keeps its history server-side, but has no route to read it back). */
  resume(threadId: string): void;
  reset(): void;
  stop(): void;
}

let counter = 0;
const nextId = (): string => `e${Date.now().toString(36)}${(counter++).toString(36)}`;

export function useChat(api: Api, bot: Bot): Chat {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const thread = useRef<string | null>(null);

  const patch = useCallback((id: string, change: Partial<AssistantEntry>) => {
    setEntries((all) => all.map((e) => (e.id === id && e.role === "assistant" ? { ...e, ...change } : e)));
  }, []);

  const run = useCallback(
    async (path: string, body: Record<string, unknown>, userText: string) => {
      abort.current?.abort();
      const controller = new AbortController();
      abort.current = controller;
      const reply: AssistantEntry = {
        id: nextId(),
        role: "assistant",
        requestId: null,
        status: "Sending…",
        pending: true,
      };
      setEntries((all) => [...all, { id: nextId(), role: "user", text: userText }, reply]);
      setBusy(true);
      let finished = false;
      try {
        for await (const ev of api.stream(path, body, controller.signal)) {
          switch (ev.type) {
            case "thread":
              thread.current = ev.thread_id;
              setThreadId(ev.thread_id);
              patch(reply.id, { requestId: ev.request_id });
              break;
            case "status":
              patch(reply.id, { status: ev.text });
              break;
            case "answer":
              finished = true;
              patch(reply.id, { answer: ev.body, status: null });
              break;
            case "clarify":
              finished = true;
              patch(reply.id, { clarify: ev.body, status: null });
              break;
            case "limit":
              finished = true;
              patch(reply.id, { limit: ev.body, status: null });
              break;
            case "error":
              finished = true;
              patch(reply.id, { error: ev.body, status: null });
              break;
            case "done":
              break;
          }
        }
        if (!finished) {
          patch(reply.id, {
            error: { code: "incomplete", message: "The answer was cut off. Please try again." },
            status: null,
          });
        }
      } catch (err) {
        if (controller.signal.aborted) {
          patch(reply.id, { error: { code: "stopped", message: "Stopped." }, status: null });
        } else {
          const message =
            err instanceof ApiError ? err.detail : "Couldn't reach the assistant. Check your connection and try again.";
          patch(reply.id, {
            error: { code: err instanceof ApiError ? String(err.status) : "network", message },
            requestId: err instanceof ApiError ? err.requestId : null,
            status: null,
          });
        }
      } finally {
        patch(reply.id, { pending: false });
        // A newer turn may have replaced this one; only the latest turn clears the busy flag.
        if (abort.current === controller) {
          abort.current = null;
          setBusy(false);
        }
      }
    },
    [api, patch],
  );

  const send = useCallback(
    async (message: string, options: SendOptions = {}) => {
      const text = message.trim();
      if (!text) return;
      const body: Record<string, unknown> = { message: text };
      if (thread.current) body.thread_id = thread.current;
      if (options.uiSelected?.length) body.ui_selected = options.uiSelected;
      if (options.userState) body.user_state = options.userState;
      await run(`/${bot}/chat`, body, text);
    },
    [bot, run],
  );

  const choose = useCallback(
    async (option: ClarifyOption) => {
      if (!thread.current) return;
      const body = option.plan_key
        ? { thread_id: thread.current, plan_key: option.plan_key }
        : { thread_id: thread.current, text: option.label };
      await run(`/${bot}/chat/choose`, body, option.label);
    },
    [bot, run],
  );

  const reset = useCallback(() => {
    abort.current?.abort();
    thread.current = null;
    setThreadId(null);
    setEntries([]);
  }, []);

  const resume = useCallback((id: string) => {
    abort.current?.abort();
    thread.current = id;
    setThreadId(id);
    setEntries([]);
  }, []);

  const stop = useCallback(() => abort.current?.abort(), []);

  return { bot, entries, threadId, busy, send, choose, resume, reset, stop };
}
