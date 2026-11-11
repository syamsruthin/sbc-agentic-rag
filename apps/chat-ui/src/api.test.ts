import { describe, expect, it, vi } from "vitest";

import { ApiError, createApi, sha256Hex } from "./api";

function sse(body: string): Response {
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

describe("sha256Hex", () => {
  it("matches the known digest", async () => {
    expect(await sha256Hex("abc")).toBe("ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  });
});

describe("createApi", () => {
  it("sends the token in X-Sbc-Authorization and the body hash on POST", async () => {
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => sse("event: done\ndata: {}\n\n"));
    const api = createApi({ base: "/api", getToken: async () => "tok", fetchImpl });
    const events = [];
    for await (const e of api.stream("/broker/chat", { message: "hi" })) events.push(e);

    expect(events).toEqual([{ type: "done" }]);
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/broker/chat");
    const headers = new Headers(init!.headers);
    expect(headers.get("X-Sbc-Authorization")).toBe("Bearer tok");
    expect(headers.get("Authorization")).toBeNull();
    expect(headers.get("x-amz-content-sha256")).toBe(await sha256Hex('{"message":"hi"}'));
    expect(init!.body).toBe('{"message":"hi"}');
  });

  it("drops empty query parameters on GET and sends no token when there is none", async () => {
    const fetchImpl = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => Response.json({ plans: [], total: 0 }));
    await createApi({ fetchImpl }).get("/public/plans/search", { q: "", state: "CA", limit: 20, market: undefined });
    const [url, init] = fetchImpl.mock.calls[0]!;
    expect(url).toBe("/api/public/plans/search?state=CA&limit=20");
    expect(new Headers(init!.headers).has("X-Sbc-Authorization")).toBe(false);
  });

  it("raises ApiError with the API's detail and request ID, and reports a 401", async () => {
    const onUnauthorized = vi.fn();
    const fetchImpl = vi.fn(async () =>
      Response.json({ detail: "sign in first" }, { status: 401, headers: { "x-request-id": "req-12345678" } }),
    );
    const api = createApi({ fetchImpl, onUnauthorized });
    const err = await api.get("/broker/threads").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).detail).toBe("sign in first");
    expect((err as ApiError).requestId).toBe("req-12345678");
    expect(onUnauthorized).toHaveBeenCalledOnce();
  });
});
