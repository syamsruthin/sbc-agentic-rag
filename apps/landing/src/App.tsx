import { useEffect, useState } from "react";

import { HomePage } from "./pages/Home";
import { HowItWorksPage } from "./pages/HowItWorks";
import { QualityPage } from "./pages/Quality";

const PAGES = {
  "": { title: "Home", Page: HomePage },
  "how-it-works": { title: "How it works", Page: HowItWorksPage },
  quality: { title: "Quality and cost", Page: QualityPage },
} as const;
type PageKey = keyof typeof PAGES;

function current(): PageKey {
  const key = window.location.hash.replace(/^#\/?/, "");
  return key in PAGES ? (key as PageKey) : "";
}

export function App() {
  const [page, setPage] = useState<PageKey>(current);
  useEffect(() => {
    const onHash = () => {
      setPage(current());
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const { Page } = PAGES[page];

  return (
    <div className="min-h-[100dvh] bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <nav className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-3">
          <a href="#/" className="font-semibold text-slate-900">
            SBC Agentic RAG
          </a>
          <div className="flex gap-4 text-sm">
            {(Object.keys(PAGES) as PageKey[]).map((k) => (
              <a
                key={k}
                href={`#/${k}`}
                aria-current={k === page ? "page" : undefined}
                className={k === page ? "font-medium text-brand-600" : "text-slate-600 hover:text-slate-900"}
              >
                {PAGES[k].title}
              </a>
            ))}
          </div>
        </nav>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Page />
      </main>
      <footer className="mx-auto max-w-5xl px-4 pb-10 text-xs text-slate-500">
        A portfolio project. Answers quote each plan's Summary of Benefits and Coverage (SBC) and are not advice;
        the plan documents are the final word. Not affiliated with or endorsed by any insurer.{" "}
        <a className="underline" href="https://github.com/syamsruthin/sbc-agentic-rag" target="_blank" rel="noopener noreferrer">
          Source on GitHub
        </a>
      </footer>
    </div>
  );
}
