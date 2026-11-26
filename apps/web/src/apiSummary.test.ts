// The /api/summary serverless function (api/summary.ts) with fetch stubbed: no network, no keys. Covers the cache hit,
// the budget refusal, validation of the model's draft, preview deployments without a key, and owner-only access.
import { describe, expect, it, vi } from "vitest";
import { budgetFor, checkDraft, compose, gather, handle, inputEstimate, licenceOf, newsFrom, parseDraft, worstCase, SOURCE_LICENCE, MAX_RESERVED, MAX_TOKENS, MODEL, PRICE, type Facts } from "../api/summary";

const NOW = new Date("2026-11-18T20:00:00Z"); // 15:00 ET
const ENV = { VITE_SUPABASE_URL: "https://db.example.co", VITE_SUPABASE_PUBLISHABLE_KEY: "sb_publishable_test", ANTHROPIC_API_KEY: "sk-test-not-real" };
const DB_FACTS = { symbol: "AAPL", today: "2026-11-18",
  profile: { name: "Apple Inc", industry: "Technology", exchange: "NASDAQ", as_of: "2026-11-18" },
  news: [{ headline: "Apple raises dividend to $0.27 a share", source: "benzinga", published_at: "2026-11-17T21:05:00Z" },
    { headline: "Apple to open three stores in India", source: "benzinga", published_at: "2026-11-15T12:00:00Z" }] };
const GOOD = { about: "Apple Inc is a US technology company listed on NASDAQ.", news: [{ id: "n1", gist: "Apple raised its dividend to $0.27 a share." }] };

type Call = { url: string; body: Record<string, unknown> | null; headers: Record<string, string> };
/** A stub for fetch: Supabase RPCs by function name, the Anthropic Messages API, anything else 404. */
function stub(o: { card?: unknown; reserve?: unknown; draft?: unknown; anthropicStatus?: number; settle?: (b: Record<string, unknown>) => unknown;
  rpcError?: { status: number; code: string }; throwModel?: boolean; usage?: { input_tokens: number; output_tokens: number } } = {}) {
  const calls: Call[] = [];
  const f = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input), body = init?.body ? JSON.parse(String(init.body)) : null;
    calls.push({ url, body, headers: (init?.headers ?? {}) as Record<string, string> });
    const res = (v: unknown, status = 200) => new Response(JSON.stringify(v), { status, headers: { "content-type": "application/json" } });
    if (url.includes("/rest/v1/rpc/")) {
      const fn = url.split("/rpc/")[1];
      if (o.rpcError) return res({ code: o.rpcError.code, message: "x" }, o.rpcError.status);
      if (fn === "kb_stock_summary") return res(o.card ?? null);
      if (fn === "kb_summary_facts") return res(DB_FACTS);
      if (fn === "kb_summary_reserve") return res(o.reserve ?? { ok: true, call_id: 41 });
      if (fn === "kb_summary_settle") return res(o.settle ? o.settle(body) : { symbol: "AAPL", day: "2026-11-18", today: true, text: body.p_text, body: body.p_body, sources: body.p_sources, ai: body.p_ai });
    }
    if (url.startsWith("https://api.anthropic.com/")) {
      if (o.throwModel) throw new DOMException("timed out", "TimeoutError");
      return res({ content: [{ type: "tool_use", name: "stock_summary", input: o.draft ?? GOOD }], usage: o.usage ?? { input_tokens: 900, output_tokens: 120 } }, o.anthropicStatus ?? 200);
    }
    return res({}, 404);
  }) as typeof fetch;
  return { f, calls, to: (s: string) => calls.filter((c) => c.url.includes(s)) };
}
const get = (q = "symbol=AAPL", auth = "Bearer header.payload.sig", method = "GET") =>
  new Request(`https://kaibot-zeta.vercel.app/api/summary?${q}`, { method, headers: auth ? { authorization: auth } : {} });
const run = async (s: ReturnType<typeof stub>, req = get(), env: Record<string, string | undefined> = ENV) => {
  const r = await handle(req, env, s.f, NOW);
  return { status: r.status, body: await r.json() as Record<string, any> };
};

describe("/api/summary: requests", () => {
  it("refuses other methods, a bad symbol and a caller without a session", async () => {
    expect((await run(stub(), get("symbol=AAPL", "Bearer a", "POST"))).status).toBe(405);
    expect((await run(stub(), get("symbol=not%20a%20ticker"))).status).toBe(400);
    expect((await run(stub(), get("symbol=AAPL", ""))).status).toBe(401);
  });
  it("passes the caller's own session and the publishable key to Supabase, never a key of its own", async () => {
    const s = stub({ card: { symbol: "AAPL", day: "2026-11-18", today: true, text: "x", body: {}, sources: [], ai: true } });
    await run(s);
    const h = s.calls[0].headers;
    expect(h.Authorization).toBe("Bearer header.payload.sig");
    expect(h.apikey).toBe("sb_publishable_test");
  });
  it("a caller who is not the owner gets 403", async () => {
    expect((await run(stub({ rpcError: { status: 400, code: "42501" } }))).status).toBe(403);
  });
  it("before 0023 is applied it answers 'unavailable', not an error", async () => {
    const r = await run(stub({ rpcError: { status: 404, code: "PGRST202" } }));
    expect(r.status).toBe(200);
    expect(r.body.status).toBe("unavailable");
  });
});

describe("/api/summary: cache, preview, budget", () => {
  it("serves today's card from the database without calling the model", async () => {
    const card = { symbol: "AAPL", day: "2026-11-18", today: true, text: "Apple Inc: Technology.", body: {}, sources: [], ai: true };
    const s = stub({ card });
    const r = await run(s);
    expect(r.body).toEqual({ status: "cached", summary: card });
    expect(s.to("anthropic")).toEqual([]);
    expect(s.to("kb_summary_reserve")).toEqual([]);
  });
  it("on a preview deployment (no ANTHROPIC_API_KEY) it says summaries are unavailable and shows an older card", async () => {
    const older = { symbol: "AAPL", day: "2026-11-17", today: false, text: "old", body: {}, sources: [], ai: true };
    const s = stub({ card: older });
    const r = await run(s, get(), { ...ENV, ANTHROPIC_API_KEY: undefined });
    expect(r.body.status).toBe("unavailable");
    expect(r.body.reason).toContain("summaries unavailable");
    expect(r.body.summary).toEqual(older);
    expect(s.to("anthropic")).toEqual([]);
  });
  it("a budget refusal makes no model call and shows the plain facts, unstored", async () => {
    const s = stub({ reserve: { ok: false, reason: "day_cap" } });
    const r = await run(s);
    expect(r.body.status).toBe("plain");
    expect(r.body.reason).toBe("day_cap");
    expect(r.body.summary.ai).toBe(false);
    expect(r.body.summary.text).toContain("Apple Inc: Technology, listed on NASDAQ.");
    expect(s.to("anthropic")).toEqual([]);
    expect(s.to("kb_summary_settle")).toEqual([]);
  });
  it("reserves the call's worst case, which is small and inside the SQL's per-call limit", async () => {
    const s = stub();
    await run(s);
    const p = s.to("kb_summary_reserve")[0].body!;
    expect(p.p_model).toBe(MODEL);
    expect(p.p_reserved as number).toBeGreaterThan(0);
    expect(p.p_reserved as number).toBeLessThan(0.002);
  });
});

describe("/api/summary: the model", () => {
  it("writes a fresh card: the cheap model, a forced tool, the real cost, stored through kb_summary_settle", async () => {
    const s = stub();
    const r = await run(s);
    expect(r.body.status).toBe("fresh");
    const m = s.to("anthropic")[0];
    expect(m.body!.model).toBe("claude-haiku-5-5");
    expect(m.body!.tool_choice).toEqual({ type: "tool", name: "stock_summary" });
    expect(m.headers["x-api-key"]).toBe("sk-test-not-real");
    expect(String((m.body!.messages as { content: string }[])[0].content)).toContain("Apple raises dividend");
    const st = s.to("kb_summary_settle")[0].body!;
    expect(st.p_call).toBe(41);
    expect(st.p_ai).toBe(true);
    expect(st.p_actual).toBeCloseTo((900 * 0.10 + 120 * 0.50) / 1e6, 9);
    expect(String(st.p_text)).toContain("Apple Inc is a US technology company listed on NASDAQ.");
    expect(String(st.p_text)).toContain("News, Nov 17, 2026: Apple raised its dividend to $0.27 a share.");
    expect(JSON.stringify(r.body)).not.toContain("sk-test-not-real");
  });
  it("a draft that adds a fact is refused: the plain facts are stored, its cost still counted", async () => {
    const s = stub({ draft: { about: "Apple makes the iPhone and is listed on NASDAQ.", news: [] } });
    const r = await run(s);
    const st = s.to("kb_summary_settle")[0].body!;
    expect(st.p_ai).toBe(false);
    expect(String(st.p_error)).toContain("iPhone");
    expect(st.p_actual as number).toBeGreaterThan(0);
    expect(String(st.p_text)).not.toContain("iPhone");
    expect(r.body.summary.ai).toBe(false);
  });
  it("a draft of the wrong shape or length is refused too", async () => {
    for (const draft of [{ about: "x".repeat(261), news: [] }, { about: "ok", news: [], price_target: 300 }, { about: "ok", news: [{ id: "n9", gist: "x" }] }]) {
      const s = stub({ draft });
      await run(s);
      expect(s.to("kb_summary_settle")[0].body!.p_ai).toBe(false);
    }
  });
  it("a failed or timed-out call settles its worst case, stores nothing and shows the plain facts", async () => {
    for (const o of [{ anthropicStatus: 529 }, { throwModel: true }]) {
      const s = stub(o);
      const r = await run(s);
      const st = s.to("kb_summary_settle")[0].body!;
      expect(st.p_text).toBeNull();
      expect(String(st.p_error)).toMatch(/anthropic/);
      expect(r.body.status).toBe("plain");
    }
  });
});

describe("draft validation", () => {
  const facts = async (): Promise<Facts> => gather("AAPL", DB_FACTS, {}, stub().f, NOW);
  it("a faithful draft passes", async () => {
    const d = parseDraft({ about: "Apple Inc is a US technology company, listed on NASDAQ.", news: [{ id: "n1", gist: "The dividend rises to $0.27 a share." }, { id: "n2", gist: "Apple plans three new stores in India." }] })!;
    expect(checkDraft(d, await facts())).toEqual([]);
  });
  it.each([
    ["Apple makes the iPhone.", [], "iPhone"],
    ["Apple, listed since 1976.", [], "1976"],
    ["See https://evil.example", [], "link"],
    ["Apple is a technology company.", [{ id: "n2", gist: "Apple opens 5 stores." }], "5"],
    ["Apple is a technology company.", [{ id: "n3", gist: "x" }], "n3"],
    ["Apple is a technology company.", [{ id: "n1", gist: "a" }, { id: "n1", gist: "b" }], "twice"],
  ])("refuses %s", async (about, news, why) => {
    const problems = checkDraft(parseDraft({ about, news })!, await facts());
    expect(problems.some((p) => p.includes(why))).toBe(true);
  });
});

describe("facts", () => {
  it("uses the database's facts when no API key is set, and says what it could not read", async () => {
    const f = await gather("AAPL", DB_FACTS, {}, stub().f, NOW);
    expect(f.profile?.name).toBe("Apple Inc");
    expect(f.news.map((n) => n.id)).toEqual(["n1", "n2"]);
    expect(f.earningsStatus).toBe("unread");
    const c = compose(f, null);
    expect(c.text).toContain("Earnings date: could not be read today.");
    expect(c.sources.map((x) => x.name)).toEqual(["Finnhub profile", "the news desk"]);
  });
  it("reads Finnhub and Alpaca when their keys are set, with the keys in headers, not URLs", async () => {
    const calls: string[] = [], hdrs: Record<string, string>[] = [];
    const f = (async (u: RequestInfo | URL, init?: RequestInit) => {
      const url = String(u); calls.push(url); hdrs.push((init?.headers ?? {}) as Record<string, string>);
      const r = (v: unknown) => new Response(JSON.stringify(v), { status: 200 });
      if (url.includes("profile2")) return r({ name: "Apple Inc", finnhubIndustry: "Technology", exchange: "NASDAQ NMS - GLOBAL MARKET", country: "US", ipo: "1980-12-12" });
      if (url.includes("calendar/earnings")) return r({ earningsCalendar: [{ symbol: "AAPL", date: "2027-01-29", hour: "amc" }, { symbol: "AAPL", date: "2026-10-30", hour: "amc" }] });
      if (url.includes("/v1beta1/news")) return r({ news: [{ headline: "Apple beats", summary: "Revenue up 8%.", created_at: "2026-11-18T13:00:00Z", source: "benzinga" }] });
      if (url.includes("corporate-actions")) return r({ corporate_actions: { forward_splits: [{ symbol: "AAPL", ex_date: "2026-12-01" }] } });
      return r({});
    }) as typeof fetch;
    const facts = await gather("AAPL", DB_FACTS, { FINNHUB_API_KEY: "fh-secret", ALPACA_API_KEY: "ak", ALPACA_API_SECRET: "as-secret" }, f, NOW);
    expect(calls.join(" ")).not.toMatch(/fh-secret|as-secret/);
    expect(hdrs.some((h) => h["X-Finnhub-Token"] === "fh-secret")).toBe(true);
    expect(facts.earnings).toEqual({ date: "2027-01-29", hour: "amc", asOf: "2026-11-18" });
    expect(facts.actions).toEqual([{ action: "split", on: "2026-12-01" }]);
    expect(facts.news[0].headline).toBe("Apple beats");
    const c = compose(facts, null);
    expect(c.text).toContain("Next earnings: Jan 29, 2027, after the close.");
    expect(c.text).toContain("Corporate actions: split on Dec 1, 2026.");
    expect(c.sources.map((x) => x.name)).toEqual(["Finnhub profile", "Alpaca news", "Finnhub earnings calendar", "Alpaca corporate actions"]);
  });
  it("keeps the latest three headlines of the last 7 days, cleaned", () => {
    const rows = Array.from({ length: 9 }, (_, i) => ({ headline: `Item ${i}‮`, created_at: `2026-11-${String(10 + i).padStart(2, "0")}T12:00:00Z` }));
    const n = newsFrom(rows, NOW, "x");
    expect(n.map((x) => x.headline)).toEqual(["Item 8", "Item 7", "Item 6"]);
  });
  it("with no news, the card says so", async () => {
    const f = await gather("AAPL", { ...DB_FACTS, news: [] }, {}, stub().f, NOW);
    expect(compose(f, null).text).toContain("No news about AAPL in the last 7 days.");
    expect(worstCase(f)).toBeLessThan(0.002);
  });
  it("the card text has a hard maximum length", async () => {
    const f = await gather("AAPL", DB_FACTS, {}, stub().f, NOW);
    expect(compose({ ...f, profile: { ...f.profile!, name: "A".repeat(2000) } }, null).text.length).toBeLessThanOrEqual(1200);
  });
});

describe("deployment", () => {
  it("the SPA rewrite leaves /api/* to the functions", async () => {
    const v = (await import("../vercel.json")).default as { rewrites: { source: string }[]; functions: Record<string, unknown> };
    const re = new RegExp(`^${v.rewrites[0].source.replace("/(", "/(")}$`);
    expect(re.test("/api/summary")).toBe(false);
    expect(re.test("/positions/7")).toBe(true);
    expect(v.functions["api/summary.ts"]).toBeTruthy();
  });
});

describe("/api/summary: the reservation is an honest upper bound (#324)", () => {
  const bound = (est: number, maxTokens: number) => (est * PRICE.input + maxTokens * PRICE.output) / 1e6;
  it("sends max_tokens sized from the reservation: input (over-estimated) plus all output fits it", async () => {
    const s = stub();
    await run(s);
    const reserved = s.to("kb_summary_reserve")[0].body!.p_reserved as number;
    const sent = s.to("anthropic")[0].body!;
    const f = await gather("AAPL", DB_FACTS, {}, stub().f, NOW);
    expect(sent.max_tokens).toBe(budgetFor(inputEstimate(f))!.maxTokens);
    expect(sent.max_tokens as number).toBeLessThanOrEqual(MAX_TOKENS);
    expect(bound(inputEstimate(f), sent.max_tokens as number)).toBeLessThanOrEqual(reserved);
  });
  it("a reservation is never above the SQL's per-call limit: max_tokens shrinks instead", () => {
    for (const est of [100, 2_000, 50_000, 150_000, 190_000, 199_000]) {
      const b = budgetFor(est)!;
      expect(b.reserved).toBeLessThanOrEqual(MAX_RESERVED);
      expect(bound(est, b.maxTokens)).toBeLessThanOrEqual(b.reserved);
    }
    expect(budgetFor(199_000)!.maxTokens).toBeLessThan(MAX_TOKENS); // $0.0199 of input leaves 200 tokens
    expect(budgetFor(210_000)).toBeNull(); // the input alone would not fit: no call at all
  });
  it("real facts, even at their longest, are far below the per-call limit", async () => {
    const s = stub();
    const long = { ...DB_FACTS, news: Array.from({ length: 3 }, (_, i) => ({ ...DB_FACTS.news[0], headline: "x".repeat(300) + i })) };
    const f = await gather("AAPL", long, {}, s.f, NOW);
    expect(budgetFor(inputEstimate(f))).not.toBeNull(); // real facts are far below the limit
  });
  it("records the true cost when it comes out above the reservation, and says so", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    const s = stub({ usage: { input_tokens: 40_000, output_tokens: 300 } }); // the input was far denser than estimated
    await run(s);
    const reserved = s.to("kb_summary_reserve")[0].body!.p_reserved as number;
    const st = s.to("kb_summary_settle")[0].body!;
    expect(st.p_actual).toBeCloseTo((40_000 * 0.10 + 300 * 0.50) / 1e6, 9); // the true cost, not the reservation
    expect(st.p_actual as number).toBeGreaterThan(reserved);
    expect(warn.mock.calls.some((c) => String(c[0]).includes("above its reservation"))).toBe(true);
    warn.mockRestore();
  });
});

describe("/api/summary: the card's licence (#722, migration 0052)", () => {
  it("a card with news text (Benzinga, through Alpaca's API or the news desk) is owner_only, in the card and its body", async () => {
    const f = await gather("AAPL", DB_FACTS, {}, stub().f, NOW);
    expect(f.newsSource).toBe("the news desk");
    const c = compose(f, null);
    expect(c.licence).toBe("owner_only");
    expect(c.body.licence).toBe("owner_only");
    expect(licenceOf(f)).toBe("owner_only");
    const alp = { ...f, newsSource: "Alpaca news" };
    expect(licenceOf(alp)).toBe("owner_only");
  });
  it("the stored card asks kb_summary_settle for owner_only when the model's gists are in it", async () => {
    const s = stub();
    await run(s);
    const st = s.to("kb_summary_settle")[0].body!;
    expect((st.p_body as Record<string, unknown>).licence).toBe("owner_only");
    expect(String(st.p_text)).toContain("Apple raised its dividend");
  });
  it("a card with no news item, only facts, is public; a news source that gave no item adds no text", async () => {
    const f = await gather("AAPL", { ...DB_FACTS, news: [] }, {}, stub().f, NOW);
    expect(f.news).toEqual([]);
    expect(licenceOf(f)).toBe("public");
    expect(compose(f, null).body.news).toEqual([]);
    expect(licenceOf({ ...f, newsSource: "Somebody's wire" })).toBe("public"); // the news source gave no text
    expect(licenceOf({ ...f, news: [{ id: "n1", headline: "x", summary: "", date: "2026-11-18", source: "benzinga" }] })).toBe("owner_only");
  });
  it("every source the function can name has a licence, and both news sources are owner_only", () => {
    expect(SOURCE_LICENCE["Alpaca news"]).toBe("owner_only");
    expect(SOURCE_LICENCE["the news desk"]).toBe("owner_only");
    expect(Object.keys(SOURCE_LICENCE).sort()).toEqual(["Alpaca corporate actions", "Alpaca news", "Finnhub earnings calendar", "Finnhub profile", "the news desk"]);
  });
});
