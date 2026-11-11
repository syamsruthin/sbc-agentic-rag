import { describe, expect, it } from "vitest";

import { parseSse, toChatEvent, type RawEvent } from "./sse";

function streamOf(...chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const c of chunks) controller.enqueue(enc.encode(c));
      controller.close();
    },
  });
}

async function collect(stream: ReadableStream<Uint8Array>): Promise<RawEvent[]> {
  const out: RawEvent[] = [];
  for await (const e of parseSse(stream)) out.push(e);
  return out;
}

describe("parseSse", () => {
  it("parses the API's turn stream and skips keepalive comments", async () => {
    const events = await collect(
      streamOf(
        'event: thread\ndata: {"thread_id":"abc12345","request_id":"r-1234567"}\n\n',
        ": keepalive\n\n",
        'event: status\ndata: {"stage":"classify","text":"Understanding your question…"}\n\n',
        'event: answer\ndata: {"kind":"answer","markdown":"x","plan_keys":[],"citations":[]}\n\n',
        "event: done\ndata: {}\n\n",
      ),
    );
    expect(events.map((e) => e.event)).toEqual(["thread", "status", "answer", "done"]);
  });

  it("handles events split across chunks, CRLF line ends and multi-line data", async () => {
    const events = await collect(streamOf("event: sta", "tus\r", "\ndata: a\r\ndata: b\r\n", "\r\n"));
    expect(events).toEqual([{ event: "status", data: "a\nb" }]);
  });

  it("decodes UTF-8 split in the middle of a character", async () => {
    const bytes = new TextEncoder().encode('event: status\ndata: {"text":"…"}\n\n');
    const cut = bytes.indexOf(0xe2) + 1;
    const stream = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, cut));
        c.enqueue(bytes.slice(cut));
        c.close();
      },
    });
    const [e] = await collect(stream);
    expect(JSON.parse(e!.data).text).toBe("…");
  });

  it("delivers a last event that has no trailing blank line", async () => {
    expect(await collect(streamOf("event: done\ndata: {}"))).toEqual([{ event: "done", data: "{}" }]);
  });
});

describe("toChatEvent", () => {
  it("types known events and ignores unknown ones and bad JSON", () => {
    expect(toChatEvent({ event: "limit", data: '{"scope":"user","retry_after_s":30,"message":"m"}' })).toEqual({
      type: "limit",
      body: { scope: "user", retry_after_s: 30, message: "m" },
    });
    expect(toChatEvent({ event: "future", data: "{}" })).toBeNull();
    expect(toChatEvent({ event: "answer", data: "{oops" })).toBeNull();
  });
});
