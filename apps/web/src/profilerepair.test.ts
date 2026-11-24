// #635: an invited member whose profile was not made when they confirmed their email saw "Not your dashboard". At
// sign-in the Gate now asks kb_me, and for an account that is neither owner, member nor helper asks migration 0046's
// kb_profile_repair once, then kb_me again. Before 0046 (null) or on an error the first answer stands.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, meRepaired, type Me } from "./lib/api";

// the dashboard's Supabase client, replaced: only rpc is used here (no network, no env)
const rpc = vi.hoisted(() => vi.fn<(fn: string, args?: unknown) => Promise<{ data: unknown; error: unknown }>>());
vi.mock("./lib/supa", () => ({ createClient: () => ({ rpc }) }));

const me = (o: Partial<Me> = {}): Me => ({ owner: false, member: false, helper: false, name: null, can_invite: false, ...o });

describe("meRepaired", () => {
  it("a member, the owner or a helper: no repair is asked", async () => {
    for (const first of [me({ member: true, name: "Mia" }), me({ owner: true }), me({ helper: true })]) {
      const repair = vi.fn(async () => true);
      expect(await meRepaired(async () => first, repair)).toEqual(first);
      expect(repair).not.toHaveBeenCalled();
    }
  });

  it("no profile and owed one: repaired, then kb_me is asked again and the member's page follows", async () => {
    const answers = [me(), me({ member: true, name: "Bea", can_invite: true })];
    const ask = vi.fn(async () => answers.shift()!);
    const repair = vi.fn(async () => true);
    expect(await meRepaired(ask, repair)).toEqual(me({ member: true, name: "Bea", can_invite: true }));
    expect(repair).toHaveBeenCalledTimes(1);
    expect(ask).toHaveBeenCalledTimes(2);
  });

  it("not owed one (false), 0046 not applied (null) or an error: the first answer, asked once", async () => {
    for (const repair of [async () => false, async () => null, async () => { throw new Error("boom"); }]) {
      const ask = vi.fn(async () => me());
      expect(await meRepaired(ask, repair)).toEqual(me());
      expect(ask).toHaveBeenCalledTimes(1);
    }
  });

  it("before 0045 (kb_me null): null, and no repair", async () => {
    const repair = vi.fn(async () => true);
    expect(await meRepaired(async () => null, repair)).toBeNull();
    expect(repair).not.toHaveBeenCalled();
  });
});

describe("api.meAtSignIn and api.inviteRevoke", () => {
  beforeEach(() => { rpc.mockReset(); });
  const missing = { code: "PGRST202", message: "Could not find the function public.kb_profile_repair" };

  it("calls kb_me, kb_profile_repair, kb_me; a missing kb_profile_repair is not an error", async () => {
    const seen: string[] = [];
    rpc.mockImplementation(async (fn: string) => {
      seen.push(fn);
      if (fn === "kb_profile_repair") return { data: null, error: missing };
      return { data: me(), error: null };
    });
    expect(await api.meAtSignIn()).toEqual(me());
    expect(seen).toEqual(["kb_me", "kb_profile_repair"]);
  });

  it("a repaired profile: kb_me twice, the member comes back", async () => {
    const answers = [me(), me({ member: true, name: "Bea" })];
    rpc.mockImplementation(async (fn: string) => (fn === "kb_profile_repair"
      ? { data: true, error: null } : { data: answers.shift(), error: null }));
    expect((await api.meAtSignIn())?.member).toBe(true);
    expect(rpc.mock.calls.map((c) => c[0])).toEqual(["kb_me", "kb_profile_repair", "kb_me"]);
  });

  it("kb_invite_revoke gets the id; before 0046 the answer is null", async () => {
    const calls: unknown[] = [];
    rpc.mockImplementation(async (fn: string, args?: unknown) => {
      calls.push([fn, args]);
      return { data: null, error: { code: "PGRST202", message: "Could not find the function public.kb_invite_revoke" } };
    });
    expect(await api.inviteRevoke(7)).toBeNull();
    expect(calls).toEqual([["kb_invite_revoke", { p_id: 7 }]]);
  });
});
