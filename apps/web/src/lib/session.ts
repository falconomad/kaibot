// What one account saw must never be shown to the next (audit, 25 Nov 2026): on sign-out, and whenever the signed-in
// account changes in the same tab, every cached RPC answer (the React Query cache: the overview, positions, headlines,
// notices ...) is dropped and the realtime channels are left. The pages under the sign-in gate unmount at the same
// moment (main.tsx Gate), so their own state (the headline card's place in its cycle) goes with them.
// Wired once in main.tsx; unit-tested in session.test.ts.
import type { QueryClient } from "@tanstack/react-query";

type AuthLike = {
  onAuthStateChange: (cb: (event: string, session: { user?: { id?: string } | null } | null) => void) =>
    { data: { subscription: { unsubscribe: () => void } } };
};

/** Calls `reset` on SIGNED_OUT and when the signed-in user's id changes from one account to another (or to none). A
 *  first sign-in from signed out has nothing of anyone's to drop. Returns the unsubscribe. */
export function watchUser(auth: AuthLike, reset: () => void): () => void {
  let last: string | null = null;
  const { data } = auth.onAuthStateChange((event, session) => {
    const id = session?.user?.id ?? null;
    if (event === "SIGNED_OUT" || (last !== null && id !== last)) reset();
    last = id;
  });
  return () => data.subscription.unsubscribe();
}

/** Drop every cached answer (queries and mutations) and stop the realtime subscriptions. */
export function resetUserData(qc: QueryClient, stopRealtime: () => void): void {
  void qc.cancelQueries();
  qc.clear();
  stopRealtime();
}
