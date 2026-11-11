import { useState, type FormEvent } from "react";

import {
  completeNewPassword,
  describe,
  signIn,
  submitTotp,
  verifyTotpSetup,
  type SignInStep,
} from "./auth";

export function SignIn({ onSignedIn }: { onSignedIn(username: string): void }) {
  const [step, setStep] = useState<SignInStep | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<SignInStep>) {
    setBusy(true);
    setError(null);
    try {
      const next = await action();
      if (next.kind === "done") onSignedIn(next.username);
      else {
        setStep(next);
        setCode("");
      }
    } catch (e) {
      setError(describe(e));
    } finally {
      setBusy(false);
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!step) void run(() => signIn(username, password));
    else if (step.kind === "totp") void run(() => submitTotp(code));
    else if (step.kind === "totp_setup") void run(() => verifyTotpSetup(code));
    else if (step.kind === "new_password") void run(() => completeNewPassword(password));
  }

  const input = "mt-1 w-full rounded-lg border border-slate-300 px-3 py-2 focus:border-brand-500 focus:outline-none";
  return (
    <form
      onSubmit={onSubmit}
      className="mx-auto mt-6 w-full max-w-sm space-y-4 rounded-xl border border-slate-200 bg-white p-6 shadow-sm"
    >
      <h2 className="text-lg font-semibold">Sign in</h2>
      {!step && (
        <>
          <label className="block text-sm">
            Username
            <input className={input} autoComplete="username" value={username} onChange={(e) => setUsername(e.target.value)} required />
          </label>
          <label className="block text-sm">
            Password
            <input
              className={input}
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              required
            />
          </label>
          <p className="text-xs text-slate-500">
            Trying the demo? The username and password are on the{" "}
            <a href="/" className="text-brand-600 underline">
              home page
            </a>
            .
          </p>
        </>
      )}
      {step?.kind === "new_password" && (
        <label className="block text-sm">
          Choose a new password
          <input
            className={input}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
      )}
      {step?.kind === "totp_setup" && (
        <div className="space-y-2 text-sm">
          <p>Add this account to your authenticator app with the key below, then enter the 6-digit code it shows.</p>
          <code className="block select-all break-all rounded bg-slate-100 p-2 text-xs">{step.secret}</code>
          <a href={step.uri} className="text-xs text-brand-600 underline">
            Open in an authenticator app
          </a>
        </div>
      )}
      {(step?.kind === "totp" || step?.kind === "totp_setup") && (
        <label className="block text-sm">
          Authenticator code
          <input
            className={input}
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />
        </label>
      )}
      {error && <p className="rounded bg-red-50 p-2 text-sm text-red-800">{error}</p>}
      <button
        type="submit"
        disabled={busy}
        className="w-full rounded-lg bg-brand-600 px-4 py-2.5 font-medium text-white hover:bg-brand-700 disabled:opacity-50"
      >
        {busy ? "Signing in…" : step ? "Continue" : "Sign in"}
      </button>
    </form>
  );
}
