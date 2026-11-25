// lib/session.ts: sign-out, or another account in the same tab, drops what the account before saw (audit).
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { resetUserData, watchUser } from "./session";

function fakeAuth() {
  let cb: ((e: string, s: { user: { id: string } } | null) => void) | null = null;
  const unsubscribe = vi.fn();
  return {
    auth: { onAuthStateChange: (f: typeof cb) => { cb = f; return { data: { subscription: { unsubscribe } } }; } },
    emit: (e: string, id: string | null) => cb!(e, id ? { user: { id } } : null),
    unsubscribe,
  };
}

const owned = () => {
  const qc = new QueryClient();
  qc.setQueryData(["overview", 7], { pot: 95000 });
  qc.setQueryData(["headlines", "owner"], [{ id: 1, headline: "owner-only Benzinga line", licence: "owner_only" }]);
  qc.setQueryData(["market-notices"], { sessions: ["2026-11-27"] });
  return qc;
};

describe("lib/session: nothing of one account reaches the next", () => {
  it("sign-out clears every cached RPC answer, the headlines included, and stops the realtime channels", () => {
    const qc = owned(), stop = vi.fn(), a = fakeAuth();
    watchUser(a.auth, () => resetUserData(qc, stop));
    a.emit("INITIAL_SESSION", "owner");
    expect(qc.getQueryData(["headlines", "owner"])).toBeDefined();
    a.emit("SIGNED_OUT", null);
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(qc.getQueryData(["headlines", "owner"])).toBeUndefined();
    expect(stop).toHaveBeenCalledTimes(1);
  });
  it("another account signing in on the same tab clears too, even without a sign-out in between", () => {
    const qc = owned(), stop = vi.fn(), a = fakeAuth();
    watchUser(a.auth, () => resetUserData(qc, stop));
    a.emit("INITIAL_SESSION", "owner");
    a.emit("TOKEN_REFRESHED", "owner");      // the same account: nothing dropped
    expect(qc.getQueryCache().getAll()).toHaveLength(3);
    a.emit("SIGNED_IN", "someone-else");
    expect(qc.getQueryCache().getAll()).toHaveLength(0);
    expect(stop).toHaveBeenCalledTimes(1);
  });
  it("a first sign-in from signed out drops nothing; the returned function unsubscribes", () => {
    const reset = vi.fn(), a = fakeAuth();
    const off = watchUser(a.auth, reset);
    a.emit("INITIAL_SESSION", null);
    a.emit("SIGNED_IN", "owner");
    expect(reset).not.toHaveBeenCalled();
    off();
    expect(a.unsubscribe).toHaveBeenCalled();
  });
});
