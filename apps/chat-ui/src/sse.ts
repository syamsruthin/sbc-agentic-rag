// Server-sent events over fetch. EventSource can only GET, and every chat turn is a POST, so the response body
// is read here and parsed by the rules of the SSE spec (event/data fields, comments, blank-line dispatch).

import type { ChatEvent } from "./types";

export interface RawEvent {
  event: string;
  data: string;
}

const LINE_END = /\r\n|\n|\r/;

/** Parse an SSE byte stream into events. Comment lines (the API's `: keepalive`) are skipped. */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncGenerator<RawEvent> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let event = "message";
  let data: string[] = [];

  function* drain(final: boolean): Generator<RawEvent> {
    for (;;) {
      const match = LINE_END.exec(buffer);
      if (!match) break;
      // A lone "\r" at the end may be the first half of "\r\n": wait for more bytes.
      if (!final && match[0] === "\r" && match.index === buffer.length - 1) break;
      const line = buffer.slice(0, match.index);
      buffer = buffer.slice(match.index + match[0].length);
      if (line === "") {
        if (data.length > 0) yield { event, data: data.join("\n") };
        event = "message";
        data = [];
        continue;
      }
      if (line.startsWith(":")) continue;
      const colon = line.indexOf(":");
      const field = colon < 0 ? line : line.slice(0, colon);
      let value = colon < 0 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) value = value.slice(1);
      if (field === "event") event = value;
      else if (field === "data") data.push(value);
    }
  }

  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      yield* drain(false);
    }
    buffer += decoder.decode();
    if (buffer !== "" && !buffer.endsWith("\n")) buffer += "\n";
    yield* drain(true);
    if (data.length > 0) yield { event, data: data.join("\n") };
  } finally {
    // Stops the download when the caller stops reading early (a new question, or leaving the page).
    reader.cancel().catch(() => undefined);
  }
}

/** Turn raw events into typed chat events; unknown event names are ignored, so the API can add new ones. */
export function toChatEvent(raw: RawEvent): ChatEvent | null {
  let data: unknown;
  try {
    data = raw.data === "" ? {} : JSON.parse(raw.data);
  } catch {
    return null;
  }
  const d = data as Record<string, unknown>;
  switch (raw.event) {
    case "thread":
      return { type: "thread", thread_id: String(d.thread_id ?? ""), request_id: String(d.request_id ?? "") };
    case "status":
      return { type: "status", stage: String(d.stage ?? ""), text: String(d.text ?? "") };
    case "answer":
      return { type: "answer", body: data as never };
    case "clarify":
      return { type: "clarify", body: data as never };
    case "limit":
      return { type: "limit", body: data as never };
    case "error":
      return { type: "error", body: data as never };
    case "done":
      return { type: "done" };
    default:
      return null;
  }
}
