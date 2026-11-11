// Earlier conversations. A broker's own come from GET /api/broker/threads. The shared demo account lists none
// there (every visitor shares it), so this browser remembers the IDs of its own demo threads instead.

const KEY = "sbc.broker.demo-threads";
const MAX = 20;

export interface ThreadRef {
  thread_id: string;
  title: string;
  updated_at: number;
}

export function localThreads(): ThreadRef[] {
  try {
    const raw = window.localStorage.getItem(KEY);
    const list = raw ? (JSON.parse(raw) as ThreadRef[]) : [];
    return Array.isArray(list) ? list : [];
  } catch {
    return [];
  }
}

export function rememberThread(thread_id: string, title: string): void {
  try {
    const rest = localThreads().filter((t) => t.thread_id !== thread_id);
    const entry = { thread_id, title: title.slice(0, 80), updated_at: Math.floor(Date.now() / 1000) };
    window.localStorage.setItem(KEY, JSON.stringify([entry, ...rest].slice(0, MAX)));
  } catch {
    // storage unavailable (private mode): the list just isn't kept
  }
}
