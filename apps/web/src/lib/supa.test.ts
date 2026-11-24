// @vitest-environment jsdom
// lib/supa.ts builds the dashboard's Supabase client from the three parts it uses, so the file-storage and
// edge-functions clients are not shipped and the realtime code is fetched on demand. These tests hold it to what
// `createClient` of @supabase/supabase-js (a dev dependency, for this file only) does with the options the dashboard
// used to pass: the same place for the session, the same requests, the same realtime endpoint and token.
// No network: fetch is a fake that records what it was asked.
import { createClient as reference } from "@supabase/supabase-js";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createClient } from "./supa";

const URL_ = "https://abcdefgh.supabase.co", KEY = "sb_publishable_test";
const STORE = "sb-abcdefgh-auth-token";
const TOKEN = "header.payload.signature";

type Seen = { url: string; method: string; headers: Record<string, string>; body: unknown };
let seen: Seen[] = [];

beforeEach(() => {
  seen = [];
  localStorage.clear();
  vi.spyOn(console, "warn").mockImplementation(() => {});   // two clients on one store, on purpose
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers: Record<string, string> = {};
    new Headers(init?.headers).forEach((v, k) => { if (k !== "x-client-info") headers[k] = v; });
    seen.push({ url: String(input), method: init?.method ?? "GET", headers, body: init?.body ? JSON.parse(String(init.body)) : null });
    return new Response("[]", { status: 200, headers: { "content-type": "application/json" } });
  }));
});
afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks(); });

const signedIn = () => localStorage.setItem(STORE, JSON.stringify({
  access_token: TOKEN, refresh_token: "r", token_type: "bearer", expires_in: 3600,
  expires_at: Math.floor(Date.now() / 1000) + 3600, user: { id: "u1", aud: "authenticated", email: "owner@example.com" },
}));
const theirs = () => reference(URL_, KEY, { auth: { persistSession: true, detectSessionInUrl: true, flowType: "pkce" } });
/** What a call sent, with our client and with supabase-js. */
async function both(call: (c: { rpc: (fn: string, args?: Record<string, unknown>) => PromiseLike<unknown> }) => PromiseLike<unknown>) {
  await call(createClient(URL_, KEY));
  const ours = seen; seen = [];
  await call(theirs());
  return { ours, theirs: seen };
}

describe("lib/supa: the same client as supabase-js, without the parts no page uses", () => {
  it("keeps the session where supabase-js keeps it, so nobody is signed out by the change", () => {
    const key = (c: unknown) => (c as { auth: { storageKey: string } }).auth.storageKey;
    expect(key(createClient(URL_, KEY))).toBe(STORE);
    expect(key(createClient(URL_, KEY))).toBe(key(theirs()));
    expect(key(createClient(`${URL_}/`, KEY))).toBe(STORE);
  });

  it("signed out: an RPC goes to the same address with the same headers and body (the key as the bearer)", async () => {
    const r = await both(async (c) => { await c.rpc("kb_claim_owner"); await c.rpc("kb_overview", { p_run: 7 }); });
    expect(r.ours).toHaveLength(2);
    expect(r.ours).toEqual(r.theirs);
    expect(r.ours[1]).toMatchObject({ url: `${URL_}/rest/v1/rpc/kb_overview`, method: "POST", body: { p_run: 7 } });
    expect(r.ours[1].headers).toMatchObject({ apikey: KEY, authorization: `Bearer ${KEY}` });
  });

  it("signed in: an RPC carries the session's token, as with supabase-js", async () => {
    signedIn();
    const r = await both((c) => c.rpc("kb_runs"));
    expect(r.ours).toEqual(r.theirs);
    expect(r.ours[0].headers).toMatchObject({ apikey: KEY, authorization: `Bearer ${TOKEN}` });
  });

  it("reads the stored session back, and asks for the sign-in email the same way", async () => {
    signedIn();
    const c = createClient(URL_, KEY);
    expect((await c.auth.getSession()).data.session?.access_token).toBe(TOKEN);
    localStorage.clear();
    const otp = (auth: { signInWithOtp: (a: { email: string }) => Promise<unknown> }) => auth.signInWithOtp({ email: "owner@example.com" });
    await otp(createClient(URL_, KEY).auth);
    const ours = seen; seen = [];
    await otp(theirs().auth);
    const shape = (s: Seen[]) => s.map((x) => ({ url: x.url, method: x.method, headers: x.headers, keys: Object.keys(x.body as object).sort() }));
    expect(ours).toHaveLength(1);
    expect(ours[0].url).toBe(`${URL_}/auth/v1/otp`);
    expect(shape(ours)).toEqual(shape(seen));           // the PKCE challenge is random; everything else is equal
    expect((ours[0].body as { code_challenge_method?: string }).code_challenge_method).toBe("s256");
  });

  it("realtime is made on the first ask, once, for the same endpoint, and gets the session's token", async () => {
    signedIn();
    const c = createClient(URL_, KEY);
    const rt = await c.realtime();
    expect(await c.realtime()).toBe(rt);
    const ref = theirs().realtime;
    await rt.setAuth(); await ref.setAuth();
    const view = (x: unknown) => { const r = x as { endPoint: string; apiKey: string; accessTokenValue: string; params: unknown };
      return { endPoint: r.endPoint, apiKey: r.apiKey, token: r.accessTokenValue, params: r.params }; };
    expect(view(rt)).toEqual(view(ref));
    expect(view(rt)).toMatchObject({ endPoint: "wss://abcdefgh.supabase.co/realtime/v1/websocket", apiKey: KEY, token: TOKEN });
  });

  it("signed out, the realtime client falls back to the key, as supabase-js does", async () => {
    const rt = await createClient(URL_, KEY).realtime();
    await rt.setAuth();
    expect((rt as unknown as { accessTokenValue: string }).accessTokenValue).toBe(KEY);
  });

  it("builds the sign-in client with the settings supabase-js gave it (PKCE, kept session, refresh, link in the address)", () => {
    const view = (c: unknown) => { const a = (c as { auth: Record<string, unknown> }).auth;
      return { url: a.url, storageKey: a.storageKey, flowType: a.flowType, persistSession: a.persistSession,
        autoRefreshToken: a.autoRefreshToken, detectSessionInUrl: a.detectSessionInUrl,
        hasCustomAuthorizationHeader: a.hasCustomAuthorizationHeader,
        apikey: (a.headers as Record<string, string>).apikey, authorization: (a.headers as Record<string, string>).Authorization }; };
    expect(view(createClient(URL_, KEY))).toEqual(view(theirs()));
    expect(view(createClient(URL_, KEY))).toMatchObject({ url: `${URL_}/auth/v1`, flowType: "pkce", persistSession: true,
      autoRefreshToken: true, detectSessionInUrl: true, apikey: KEY });
  });

  it("the 6-digit code is checked with the same request", async () => {
    const verify = (auth: { verifyOtp: (a: { email: string; token: string; type: "email" }) => Promise<unknown> }) =>
      auth.verifyOtp({ email: "owner@example.com", token: "123456", type: "email" });
    await verify(createClient(URL_, KEY).auth);
    const ours = seen; seen = [];
    await verify(theirs().auth);
    expect(ours).toHaveLength(1);
    expect(ours[0]).toMatchObject({ url: `${URL_}/auth/v1/verify`, method: "POST", body: { email: "owner@example.com", token: "123456", type: "email" } });
    expect(ours).toEqual(seen);
  });

  it("a refreshed token reaches the next RPC and the realtime client; after sign-out both fall back to the key", async () => {
    const FRESH = "fresh.payload.signature";
    const run = async (make: () => { auth: { getSession: () => Promise<unknown>; signOut: () => Promise<unknown> };
      rpc: (fn: string) => PromiseLike<unknown> }, realtimeOf: (c: unknown) => Promise<unknown> | unknown) => {
      localStorage.clear(); seen = [];
      localStorage.setItem(STORE, JSON.stringify({ access_token: TOKEN, refresh_token: "r", token_type: "bearer", expires_in: 3600,
        expires_at: Math.floor(Date.now() / 1000) - 10, user: { id: "u1", aud: "authenticated", email: "owner@example.com" } }));
      vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const headers: Record<string, string> = {};
        new Headers(init?.headers).forEach((v, k) => { if (k !== "x-client-info") headers[k] = v; });
        seen.push({ url: String(input), method: init?.method ?? "GET", headers, body: init?.body ? JSON.parse(String(init.body)) : null });
        const body = String(input).includes("grant_type=refresh_token")
          ? { access_token: FRESH, refresh_token: "r2", token_type: "bearer", expires_in: 3600,
              user: { id: "u1", aud: "authenticated", email: "owner@example.com" } }
          : [];
        return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
      }));
      const c = make();
      const rt = await realtimeOf(c) as { accessTokenValue: string | null };
      await c.rpc("kb_runs");                       // the stored token has expired: the client refreshes it first
      await new Promise((r) => setTimeout(r, 0));
      const afterRefresh = { realtime: rt.accessTokenValue, sent: seen.map((s) => ({ url: s.url, headers: s.headers })) };
      seen = [];
      await c.auth.signOut();
      await c.rpc("kb_runs");
      await new Promise((r) => setTimeout(r, 0));
      const rpc = seen.find((s) => s.url.endsWith("/rpc/kb_runs"));
      return { afterRefresh, afterSignOut: { realtime: rt.accessTokenValue, rpc: rpc?.headers, stored: localStorage.getItem(STORE) } };
    };
    const ours = await run(() => createClient(URL_, KEY), (c) => (c as ReturnType<typeof createClient>).realtime());
    const ref = await run(() => theirs(), (c) => (c as ReturnType<typeof theirs>).realtime);
    expect(ours).toEqual(ref);
    expect(ours.afterRefresh.realtime).toBe(FRESH);
    expect(ours.afterRefresh.sent.at(-1)).toMatchObject({ url: `${URL_}/rest/v1/rpc/kb_runs`, headers: { apikey: KEY, authorization: `Bearer ${FRESH}` } });
    expect(ours.afterSignOut).toMatchObject({ realtime: KEY, rpc: { apikey: KEY, authorization: `Bearer ${KEY}` }, stored: null });
  });

  it("realtime asked for only after a refresh still starts with the fresh token", async () => {
    const FRESH = "fresh.payload.signature";
    localStorage.setItem(STORE, JSON.stringify({ access_token: TOKEN, refresh_token: "r", token_type: "bearer", expires_in: 3600,
      expires_at: Math.floor(Date.now() / 1000) - 10, user: { id: "u1", aud: "authenticated", email: "owner@example.com" } }));
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ access_token: FRESH, refresh_token: "r2", token_type: "bearer",
      expires_in: 3600, user: { id: "u1", aud: "authenticated", email: "owner@example.com" } }), { status: 200, headers: { "content-type": "application/json" } })));
    const c = createClient(URL_, KEY);
    await c.auth.getSession();
    const rt = await c.realtime();
    await new Promise((r) => setTimeout(r, 0));
    expect((rt as unknown as { accessTokenValue: string }).accessTokenValue).toBe(FRESH);
  });

  it("refuses a missing address or key with the messages supabase-js gives", () => {
    expect(() => createClient("", KEY)).toThrow("supabaseUrl is required.");
    expect(() => createClient("abcdefgh.supabase.co", KEY)).toThrow("Invalid supabaseUrl");
    expect(() => createClient(URL_, "")).toThrow("supabaseKey is required.");
  });
});
