// Client-side limit on sign-in and registration attempts (stakeholder, 24 Nov 2026). The sign-in page now says
// whether an email has an account, which the stakeholder accepted for a dashboard of under 5 users; this limit makes
// trying many addresses from one browser slow. It is a courtesy, not a lock: Supabase Auth's own per-email and per-IP
// limits stay the real ones (Auth -> Rate Limits), and a script can skip this page.

export const ATTEMPT_LIMIT = 5;
export const ATTEMPT_WINDOW_MS = 15 * 60_000;
const KEY = "kb:auth-attempts";

type Store = Pick<Storage, "getItem" | "setItem">;

function read(store: Store, now: number): number[] {
  try {
    const raw = JSON.parse(store.getItem(KEY) ?? "[]") as unknown;
    return Array.isArray(raw) ? raw.filter((t): t is number => typeof t === "number" && t > now - ATTEMPT_WINDOW_MS && t <= now) : [];
  } catch { return []; }
}

/** Record one attempt if the limit allows it. `ok: false` carries the wait until the oldest attempt in the window ages
 *  out. A store that throws (private mode, no storage) never blocks: the limit is then off, as above. */
export function takeAttempt(now = Date.now(), store: Store | undefined = globalThis.localStorage): { ok: true } | { ok: false; waitMs: number } {
  if (!store) return { ok: true };
  const recent = read(store, now);
  if (recent.length >= ATTEMPT_LIMIT) return { ok: false, waitMs: Math.max(1000, recent[0] + ATTEMPT_WINDOW_MS - now) };
  try { store.setItem(KEY, JSON.stringify([...recent, now])); } catch { /* the limit is a courtesy */ }
  return { ok: true };
}

export function waitWords(ms: number): string {
  const min = Math.ceil(ms / 60_000);
  return min <= 1 ? "a minute" : `${min} minutes`;
}

export const TOO_MANY = (ms: number) => `Too many tries from this browser. Try again in ${waitWords(ms)}.`;

/** Supabase Auth's answer when shouldCreateUser is false and the email has no account (code otp_disabled, 422). */
export function isNoAccount(error: { code?: string; message?: string } | null | undefined): boolean {
  if (!error) return false;
  return error.code === "otp_disabled" || error.code === "user_not_found" || /signups? not allowed|user not found/i.test(error.message ?? "");
}

/** The answer when the database's trigger (migration 0045) refused the new account. */
export function isSignupRefused(error: { code?: string; message?: string } | null | undefined): boolean {
  if (!error) return false;
  return error.code === "unexpected_failure" || /database error saving new user|invite only|account limit/i.test(error.message ?? "");
}

export const NO_ACCOUNT = "No account for this email. Ask an existing user for an invite.";
