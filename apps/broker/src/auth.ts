// Cognito sign-in with SRP (the password never leaves the browser), TOTP for accounts that use it, and tokens
// kept in memory only: a reload signs the user out, and nothing is left in localStorage (WP10).

import {
  AuthenticationDetails,
  CognitoUser,
  CognitoUserPool,
  type CognitoUserSession,
  type ICognitoStorage,
} from "amazon-cognito-identity-js";

const POOL_ID: string = import.meta.env.VITE_COGNITO_USER_POOL_ID || "";
const CLIENT_ID: string = import.meta.env.VITE_COGNITO_CLIENT_ID || "";

/** Without a user pool (local dev against `APP_ENV=local`), the API accepts every request as a local broker. */
export const LOCAL_MODE = !POOL_ID || !CLIENT_ID;

class MemoryStorage implements ICognitoStorage {
  private items = new Map<string, string>();
  setItem(key: string, value: string): void {
    this.items.set(key, value);
  }
  getItem(key: string): string | null {
    return this.items.get(key) ?? null;
  }
  removeItem(key: string): void {
    this.items.delete(key);
  }
  clear(): void {
    this.items.clear();
  }
}

const storage = new MemoryStorage();
const pool = LOCAL_MODE ? null : new CognitoUserPool({ UserPoolId: POOL_ID, ClientId: CLIENT_ID, Storage: storage });

export type SignInStep =
  | { kind: "done"; username: string }
  | { kind: "totp" }
  | { kind: "totp_setup"; secret: string; uri: string }
  | { kind: "new_password" };

let pending: CognitoUser | null = null;

function callbacks(
  user: CognitoUser,
  resolve: (step: SignInStep) => void,
  reject: (err: Error) => void,
) {
  return {
    onSuccess: () => {
      pending = null;
      resolve({ kind: "done", username: user.getUsername() });
    },
    onFailure: (err: Error) => reject(err),
    totpRequired: () => resolve({ kind: "totp" }),
    mfaSetup: () => {
      user.associateSoftwareToken({
        associateSecretCode: (secret: string) => {
          const label = encodeURIComponent(`SBC Broker Assistant:${user.getUsername()}`);
          resolve({ kind: "totp_setup", secret, uri: `otpauth://totp/${label}?secret=${secret}&issuer=SBC` });
        },
        onFailure: (err: Error) => reject(err),
      });
    },
    newPasswordRequired: () => resolve({ kind: "new_password" }),
  };
}

export function signIn(username: string, password: string): Promise<SignInStep> {
  if (!pool) return Promise.resolve({ kind: "done", username: "local" });
  const user = new CognitoUser({ Username: username.trim(), Pool: pool, Storage: storage });
  pending = user;
  return new Promise((resolve, reject) =>
    user.authenticateUser(
      new AuthenticationDetails({ Username: username.trim(), Password: password }),
      callbacks(user, resolve, reject),
    ),
  );
}

export function submitTotp(code: string): Promise<SignInStep> {
  const user = pending;
  if (!user) return Promise.reject(new Error("Start signing in again."));
  return new Promise((resolve, reject) =>
    user.sendMFACode(code.trim(), callbacks(user, resolve, reject), "SOFTWARE_TOKEN_MFA"),
  );
}

export function verifyTotpSetup(code: string): Promise<SignInStep> {
  const user = pending;
  if (!user) return Promise.reject(new Error("Start signing in again."));
  return new Promise((resolve, reject) =>
    user.verifySoftwareToken(code.trim(), "Authenticator app", {
      onSuccess: () => {
        pending = null;
        resolve({ kind: "done", username: user.getUsername() });
      },
      onFailure: (err: Error) => reject(err),
    }),
  );
}

export function completeNewPassword(password: string): Promise<SignInStep> {
  const user = pending;
  if (!user) return Promise.reject(new Error("Start signing in again."));
  return new Promise((resolve, reject) =>
    user.completeNewPasswordChallenge(password, {}, callbacks(user, resolve, reject)),
  );
}

/** The current access token, refreshed with the refresh token when it has expired; null when signed out. */
export function accessToken(): Promise<string | null> {
  const user = pool?.getCurrentUser();
  if (!user) return Promise.resolve(null);
  return new Promise((resolve) =>
    user.getSession((err: Error | null, session: CognitoUserSession | null) =>
      resolve(err || !session?.isValid() ? null : session.getAccessToken().getJwtToken()),
    ),
  );
}

export function signOut(): void {
  pool?.getCurrentUser()?.signOut();
  pending = null;
  storage.clear();
}

/** Turn Cognito's errors into something a person can act on, without saying which part was wrong. */
export function describe(err: unknown): string {
  const name = (err as { name?: string; code?: string })?.code ?? (err as { name?: string })?.name ?? "";
  switch (name) {
    case "NotAuthorizedException":
    case "UserNotFoundException":
      return "Wrong username or password.";
    case "CodeMismatchException":
    case "EnableSoftwareTokenMFAException":
      return "That code didn't work. Check your authenticator app and try again.";
    case "PasswordResetRequiredException":
      return "This account needs a password reset. Ask the administrator.";
    case "TooManyRequestsException":
    case "LimitExceededException":
      return "Too many attempts. Wait a minute and try again.";
    case "InvalidPasswordException":
      return (err as Error).message;
    default:
      return "Couldn't sign in. Please try again.";
  }
}
