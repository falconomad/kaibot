// The /api/logo-domain serverless function (api/logo-domain.ts) with fetch stubbed: no network.
// Stakeholder, 18 Nov 2026: company logos come from AllInvestView's ticker-logo CDN, keyed by the company's website. This
// function turns a ticker into that website with their search API, server-side, and lets Vercel's CDN keep each answer.
// 1. Only a ticker (^[A-Z]{1,5}([.][A-Z])?$) is looked up; anything else is refused before any request goes out.
// 2. Only the result whose symbol equals the ticker exactly is taken, a US listing first; never a fuzzy match.
// 3. The website must be a plain hostname; it is returned as a bare domain (no scheme, path or www.).
// 4. A found domain is cached by the CDN for 30 days; a miss for a day; a refusal, a rate limit or an upstream error never.
// 5. Each instance limits callers (per IP) and its own calls to the search API (60 a minute per IP upstream).
import { describe, expect, it, vi } from "vitest";
import { CACHE_FOUND, CACHE_MISS, SEARCH_URL, cleanDomain, handle, newLimits, pickWebsite } from "../api/logo-domain";
import { pickWebsite as scriptPick, cleanDomain as scriptClean } from "../scripts/logo_domains.mjs";
import VERCEL from "../vercel.json";

const R = (symbol: string, website: string | null, exchange: string | null = "NASDAQ", name = symbol) => ({ symbol, name, website, exchange });
const body = (results: unknown[], query = "X") => ({ query, count: results.length, results });

function upstream(answer: unknown, status = 200) {
  return vi.fn(async (_url: string | URL | Request, _init?: RequestInit) =>
    new Response(typeof answer === "string" ? answer : JSON.stringify(answer), { status, headers: { "content-type": "application/json" } }));
}
const get = (q: string, ip = "198.51.100.7") => new Request(`https://kaibo.example/api/logo-domain${q}`, { headers: { "x-forwarded-for": ip } });

describe("picking the website", () => {
  it("takes only the exact symbol, preferring a US exchange", () => {
    const res = [R("AAPL.MX", "apple.mx", "BMV"), R("AAPL", "apple.de", "XETRA"), R("AAPL", "apple.com", "NASDAQ"), R("AAPLX", "x.com")];
    expect(pickWebsite("AAPL", res)).toBe("apple.com");
    for (const ex of ["NASDAQ", "NYSE", "NYSE ARCA", "AMEX", "BATS", "CBOE"])
      expect(pickWebsite("SPY", [R("SPY", "spy.de", "XETRA"), R("SPY", "ssga.com", ex)]), ex).toBe("ssga.com");
  });
  it("never a fuzzy match: a prefix, a different case, a class suffix or a name hit is not the ticker", () => {
    expect(pickWebsite("META", [R("METV", "roundhill.com"), R("META.TO", "meta.ca", "TSX"), R("meta", "meta.com")])).toBeNull();
    expect(pickWebsite("BRK.B", [R("BRK.A", "berkshirehathaway.com", "NYSE"), R("BRK-B", "berkshirehathaway.com", "NYSE")])).toBeNull();
    expect(pickWebsite("V", [R("VZ", "verizon.com", "NYSE"), R("VISA", "visa.com", "NYSE")])).toBeNull();
    expect(pickWebsite("AAPL", [])).toBeNull();
  });
  it("falls back to an exact symbol on another exchange only when no US listing matches", () => {
    expect(pickWebsite("NBIS", [R("NBIS", "nebius.com", "XAMS")])).toBe("nebius.com");
  });
  it("skips an exact match whose website is missing or not a hostname, and takes the next exact one", () => {
    expect(pickWebsite("AAPL", [R("AAPL", null, "NASDAQ"), R("AAPL", "javascript:alert(1)", "NYSE"), R("AAPL", "apple.com", "XETRA")])).toBe("apple.com");
  });
  it("returns a bare, lowercase domain; refuses anything that is not a hostname", () => {
    expect(cleanDomain("https://www.Apple.com/investor?x=1")).toBe("apple.com");
    expect(cleanDomain("http://t-mobile.com:443/")).toBe("t-mobile.com");
    expect(cleanDomain("ir.example.co.uk")).toBe("ir.example.co.uk");
    for (const bad of ["", "   ", "localhost", "10.0.0.1", "apple", "apple..com", "-apple.com", "apple-.com", "ap ple.com",
      "javascript:alert(1)", "user@apple.com", "apple.com#x", "xn--.com", "a".repeat(64) + ".com", "apple.c0m", 42, null, undefined, {}])
      expect(cleanDomain(bad as never), String(bad)).toBeNull();
  });
  it("the domains script picks exactly as the function does", () => {
    const cases: [string, ReturnType<typeof R>[]][] = [
      ["AAPL", [R("AAPL.MX", "apple.mx", "BMV"), R("AAPL", "apple.de", "XETRA"), R("AAPL", "https://www.apple.com/", "NASDAQ")]],
      ["META", [R("METV", "roundhill.com"), R("meta", "meta.com")]],
      ["NBIS", [R("NBIS", "nebius.com", "XAMS")]],
      ["XLK", [R("XLK", "sectorspdrs.com", "NYSE ARCA")]],
    ];
    for (const [s, res] of cases) expect(scriptPick(s, res), s).toBe(pickWebsite(s, res));
    for (const d of ["https://www.Apple.com/x", "10.0.0.1", "apple..com"]) expect(scriptClean(d)).toBe(cleanDomain(d));
  });
});

describe("GET /api/logo-domain", () => {
  it("answers {domain} with the 30-day CDN cache header, after one search call for the exact ticker", async () => {
    const f = upstream(body([R("AAPL", "apple.com", "NASDAQ")], "AAPL"));
    const r = await handle(get("?symbol=AAPL"), f, newLimits());
    expect(r.status).toBe(200);
    expect(await r.json()).toEqual({ domain: "apple.com" });
    expect(r.headers.get("cache-control")).toBe("public, s-maxage=2592000, stale-while-revalidate=604800");
    expect(CACHE_FOUND).toBe("public, s-maxage=2592000, stale-while-revalidate=604800");
    expect(r.headers.get("content-type")).toContain("application/json");
    expect(f).toHaveBeenCalledTimes(1);
    expect(String(f.mock.calls[0][0])).toBe(`${SEARCH_URL}?q=AAPL`);
    expect(SEARCH_URL).toBe("https://www.allinvestview.com/api/logo-search/");
  });
  it("a class share is passed encoded and matched exactly", async () => {
    const f = upstream(body([R("BRK.B", "berkshirehathaway.com", "NYSE")]));
    const r = await handle(get("?symbol=BRK.B"), f, newLimits());
    expect(await r.json()).toEqual({ domain: "berkshirehathaway.com" });
    expect(String(f.mock.calls[0][0])).toBe(`${SEARCH_URL}?q=BRK.B`);
  });
  it("404 when there is no exact match (cached for a day, not 30)", async () => {
    const r = await handle(get("?symbol=NBIS"), upstream(body([R("NBISX", "x.com")])), newLimits());
    expect(r.status).toBe(404);
    expect(r.headers.get("cache-control")).toBe(CACHE_MISS);
    expect(CACHE_MISS).toMatch(/^public, s-maxage=86400/);
  });
  it("refuses non-ticker input before any request goes out, and never caches the refusal", async () => {
    const f = upstream(body([]));
    for (const q of ["", "?symbol=", "?symbol=aapl", "?symbol=TOOLONG", "?symbol=BRK.BB", "?symbol=BRK-B", "?symbol=A1",
      "?symbol=../x", "?symbol=AAPL%0A", "?symbol=%20AAPL", "?symbol=AAPL&symbol=MSFT", "?symbol=AAPL&x=1", "?q=AAPL"]) {
      const r = await handle(get(q), f, newLimits());
      expect(r.status, q).toBe(400);
      expect(r.headers.get("cache-control"), q).toBe("no-store");
    }
    expect(f).not.toHaveBeenCalled();
  });
  it("only GET (and HEAD)", async () => {
    const r = await handle(new Request("https://kaibo.example/api/logo-domain?symbol=AAPL", { method: "POST" }), upstream(body([])), newLimits());
    expect(r.status).toBe(405);
  });
  it("an upstream error, bad JSON or an invalid website is a 502 that is never cached", async () => {
    for (const f of [upstream("nope", 500), upstream("<html>", 200), upstream({ results: "x" }),
      vi.fn(async () => { throw new Error("timeout"); })]) {
      const r = await handle(get("?symbol=AAPL"), f as never, newLimits());
      expect(r.status).toBe(502);
      expect(r.headers.get("cache-control")).toBe("no-store");
    }
    const evil = await handle(get("?symbol=AAPL"), upstream(body([R("AAPL", "<script>", "NASDAQ")])), newLimits());
    expect(evil.status).toBe(404);
  });
  it("rate-limits each caller per instance, with 429 and no cache", async () => {
    const limits = newLimits({ perIp: 3, upstream: 100 });
    const f = upstream(body([R("AAPL", "apple.com")]));
    const codes: number[] = [];
    for (let i = 0; i < 5; i++) codes.push((await handle(get("?symbol=AAPL", "203.0.113.5"), f, limits)).status);
    expect(codes).toEqual([200, 200, 200, 429, 429]);
    const late = await handle(get("?symbol=AAPL", "203.0.113.5"), f, limits);
    expect(late.headers.get("cache-control")).toBe("no-store");
    expect(late.headers.get("retry-after")).toMatch(/^\d+$/);
    expect((await handle(get("?symbol=AAPL", "203.0.113.9"), f, limits)).status).toBe(200);  // another caller is unaffected
  });
  it("keeps its own calls to the search API under their 60 a minute", async () => {
    const limits = newLimits();
    const f = upstream(body([R("AAPL", "apple.com")]));
    let ok = 0, limited = 0;
    for (let i = 0; i < 80; i++) {
      const s = (await handle(get("?symbol=AAPL", `192.0.2.${i}`), f, limits)).status;
      if (s === 200) ok++; else if (s === 429) limited++;
    }
    expect(f.mock.calls.length).toBeLessThanOrEqual(55);
    expect(ok).toBe(f.mock.calls.length);
    expect(limited).toBe(80 - ok);
  });
  it("is registered with a short timeout and is not rewritten to the app", () => {
    expect((VERCEL.functions as Record<string, { maxDuration: number }>)["api/logo-domain.ts"].maxDuration).toBeLessThanOrEqual(10);
    expect(new RegExp(`^${VERCEL.rewrites[0].source}$`).test("/api/logo-domain")).toBe(false);
  });
});
