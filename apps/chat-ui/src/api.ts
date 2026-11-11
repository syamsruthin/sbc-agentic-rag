// The API client. Two details come from the AWS hosting (docs/aws/MIGRATION_PLAN.md WP9, WP10):
//
// - The bearer token goes in `X-Sbc-Authorization`, never in `Authorization`: CloudFront signs each request to
//   the Lambda Function URL with SigV4 in `Authorization`, replacing whatever the browser sent.
// - Every POST carries `x-amz-content-sha256` (the hex SHA-256 of its body), which that signing needs.

import { parseSse, toChatEvent } from "./sse";
import type { ChatEvent } from "./types";

export const TOKEN_HEADER = "X-Sbc-Authorization";
export const BODY_HASH_HEADER = "x-amz-content-sha256";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly detail: string,
    readonly requestId: string | null,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

export async function sha256Hex(text: string): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

export interface ApiOptions {
  /** `/api` behind CloudFront, and in local dev through the Vite proxy. */
  base?: string;
  /** The bearer token for this request (a Cognito access token or a public session token), or null. */
  getToken?: () => Promise<string | null>;
  /** Called on a 401, e.g. to send the user back to sign-in. */
  onUnauthorized?: () => void;
  fetchImpl?: typeof fetch;
}

export interface Api {
  get<T>(path: string, params?: Record<string, string | number | undefined | null>): Promise<T>;
  post<T>(path: string, body: unknown): Promise<T>;
  stream(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<ChatEvent>;
}

async function errorFrom(response: Response): Promise<ApiError> {
  let detail = response.statusText || `HTTP ${response.status}`;
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") detail = body.detail;
    else if (Array.isArray(body.detail)) detail = "That request wasn't valid.";
  } catch {
    // not JSON: keep the status text
  }
  return new ApiError(response.status, detail, response.headers.get("x-request-id"));
}

export function createApi(options: ApiOptions = {}): Api {
  const base = (options.base ?? "/api").replace(/\/$/, "");
  const doFetch = options.fetchImpl ?? ((input, init) => fetch(input, init));

  async function headers(extra: Record<string, string>): Promise<Headers> {
    const h = new Headers(extra);
    const token = options.getToken ? await options.getToken() : null;
    if (token) h.set(TOKEN_HEADER, `Bearer ${token}`);
    return h;
  }

  async function send(path: string, init: RequestInit): Promise<Response> {
    const response = await doFetch(`${base}${path}`, init);
    if (!response.ok) {
      if (response.status === 401) options.onUnauthorized?.();
      throw await errorFrom(response);
    }
    return response;
  }

  async function postInit(body: unknown, accept: string, signal?: AbortSignal): Promise<RequestInit> {
    const text = JSON.stringify(body);
    return {
      method: "POST",
      body: text,
      signal,
      headers: await headers({
        "Content-Type": "application/json",
        Accept: accept,
        [BODY_HASH_HEADER]: await sha256Hex(text),
      }),
    };
  }

  return {
    async get<T>(path: string, params?: Record<string, string | number | undefined | null>): Promise<T> {
      const query = new URLSearchParams();
      for (const [k, v] of Object.entries(params ?? {})) {
        if (v !== undefined && v !== null && v !== "") query.set(k, String(v));
      }
      const qs = query.toString();
      const response = await send(qs ? `${path}?${qs}` : path, {
        headers: await headers({ Accept: "application/json" }),
      });
      return (await response.json()) as T;
    },

    async post<T>(path: string, body: unknown): Promise<T> {
      const response = await send(path, await postInit(body, "application/json"));
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    },

    async *stream(path: string, body: unknown, signal?: AbortSignal): AsyncGenerator<ChatEvent> {
      const response = await send(path, await postInit(body, "text/event-stream", signal));
      if (!response.body) throw new ApiError(500, "The response had no body.", null);
      for await (const raw of parseSse(response.body)) {
        const event = toChatEvent(raw);
        if (event) yield event;
      }
    },
  };
}

/** Wake the Lambda while the page loads, so the first question doesn't pay the whole cold start. */
export function warmUp(base = "/api"): void {
  fetch(`${base.replace(/\/$/, "")}/healthz`, { cache: "no-store" }).catch(() => undefined);
}
