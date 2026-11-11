// Cloudflare Turnstile, rendered explicitly. The script origin is allowed by the site's CSP
// (infra/cdk/sbc_infra/constructs/web.py). Without a site key (local dev) there is no widget, and the local API
// skips the check.

import { useEffect, useRef } from "react";

interface TurnstileApi {
  render(el: HTMLElement, options: Record<string, unknown>): string;
  remove(id: string): void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";
let loading: Promise<TurnstileApi> | null = null;

function load(): Promise<TurnstileApi> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT;
    s.async = true;
    s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("turnstile missing")));
    s.onerror = () => {
      loading = null;
      reject(new Error("couldn't load the bot check"));
    };
    document.head.appendChild(s);
  });
  return loading;
}

export const SITE_KEY: string = import.meta.env.VITE_TURNSTILE_SITE_KEY || "";

export function Turnstile({ onToken, onError }: { onToken(token: string): void; onError(message: string): void }) {
  const box = useRef<HTMLDivElement>(null);
  const handlers = useRef({ onToken, onError });
  handlers.current = { onToken, onError };

  useEffect(() => {
    let id: string | null = null;
    let live = true;
    load()
      .then((api) => {
        if (!live || !box.current) return;
        id = api.render(box.current, {
          sitekey: SITE_KEY,
          callback: (token: string) => handlers.current.onToken(token),
          "expired-callback": () => handlers.current.onToken(""),
          "error-callback": () => handlers.current.onError("The bot check failed. Please reload the page."),
        });
      })
      .catch(() => handlers.current.onError("Couldn't load the bot check. Please reload the page."));
    return () => {
      live = false;
      if (id && window.turnstile) window.turnstile.remove(id);
    };
  }, []);

  return <div ref={box} className="min-h-[65px]" />;
}
