// GET /api/logo-domain?symbol=AAPL -> {"domain":"apple.com"}, or 404 (stakeholder, 18 Nov 2026).
//
// Company logos come from AllInvestView's free ticker-logo CDN, https://cdn.tickerlogos.com/{domain}, keyed by the
// company's website. This turns a ticker into that website with their search API (at most 60 calls a minute per IP),
// server-side, so the browser makes one cached call per symbol instead of hitting the search API itself.
//
// - Only a ticker is looked up (^[A-Z]{1,5}([.][A-Z])?$, the only query parameter); anything else is a 400, and nothing
//   is sent upstream for it.
// - Only the result whose symbol equals the ticker exactly is taken, a US listing first. Never a fuzzy match.
// - The website must be a plain hostname; the answer is the bare domain (no scheme, port, path or leading www.).
// - Vercel's CDN keeps a found domain for 30 days and a miss for a day: no database, no migration. Refusals, rate
//   limits and upstream errors are never cached.
// - Public data, so no auth; instead each instance limits each caller, and its own calls to the search API.
// Copied, not imported, so the function stays one self-contained file on Vercel; scripts/logo_domains.mjs has the same
// pick, and apiLogoDomain.test.ts checks the two agree.

type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export const SEARCH_URL = "https://www.allinvestview.com/api/logo-search/";
export const CACHE_FOUND = "public, s-maxage=2592000, stale-while-revalidate=604800";
export const CACHE_MISS = "public, s-maxage=86400, stale-while-revalidate=86400";
const TICKER = /^[A-Z]{1,5}([.][A-Z])?$/;
// NASDAQ, NYSE, NYSE Arca, NYSE American (AMEX), Cboe BZX (BATS), as the search API and the usual feeds spell them.
const US = new Set(["NASDAQ", "NASDAQGS", "NASDAQGM", "NASDAQCM", "NYSE", "NYSE ARCA", "NYSEARCA", "ARCA", "NYSE AMERICAN",
  "NYSE MKT", "NYSEAMERICAN", "AMEX", "BATS", "CBOE", "CBOE BZX", "BZX", "CBOE BZX EXCHANGE"]);
const LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;

/** A website as the search API gives it -> its bare hostname, or null when it is not a plain public hostname. */
export function cleanDomain(website: unknown): string | null {
  if (typeof website !== "string") return null;
  let s = website.trim().toLowerCase();
  if (!s || /[\s@#\\]/.test(s)) return null;
  s = s.replace(/^https?:\/\//, "");
  s = s.split(/[/?]/)[0].replace(/:\d{1,5}$/, "").replace(/^www\./, "");
  if (s.length > 253 || s.includes(":")) return null;
  const labels = s.split(".");
  if (labels.length < 2 || !labels.every((l) => LABEL.test(l))) return null;
  if (!/^[a-z]{2,63}$/.test(labels[labels.length - 1])) return null;          // a real TLD: no IP addresses, no "c0m"
  if (labels.some((l) => l.startsWith("xn--") && l.length < 6)) return null;
  return s;
}

/** The website of the result whose symbol is exactly `symbol`: a US listing first, then any other exact one. */
export function pickWebsite(symbol: string, results: unknown): string | null {
  if (!Array.isArray(results)) return null;
  const exact = results.filter((r): r is Record<string, unknown> => !!r && typeof r === "object" && (r as Record<string, unknown>).symbol === symbol);
  const us = (r: Record<string, unknown>) => typeof r.exchange === "string" && US.has(r.exchange.trim().toUpperCase());
  for (const r of [...exact.filter(us), ...exact.filter((r) => !us(r))]) {
    const d = cleanDomain(r.website);
    if (d) return d;
  }
  return null;
}

/** Sliding-window counters, per instance (a cold start begins empty; good enough to stop a loop or a scraper). */
export function newLimits({ perIp = 30, upstream = 50, windowMs = 60_000 } = {}) {
  const hits = new Map<string, number[]>();
  const take = (key: string, max: number, now: number): number => {   // 0 = allowed, else seconds to wait
    const ts = (hits.get(key) ?? []).filter((t) => now - t < windowMs);
    if (ts.length >= max) { hits.set(key, ts); return Math.max(1, Math.ceil((ts[0] + windowMs - now) / 1000)); }
    ts.push(now);
    hits.set(key, ts);
    if (hits.size > 5000) for (const [k, v] of hits) if (!v.some((t) => now - t < windowMs)) hits.delete(k);
    return 0;
  };
  return { caller: (ip: string, now: number) => take(`ip:${ip}`, perIp, now), upstream: (now: number) => take("upstream", upstream, now) };
}
type Limits = ReturnType<typeof newLimits>;
const LIMITS = newLimits();

const answer = (body: unknown, status: number, cache: string, extra: Record<string, string> = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", "cache-control": cache, ...extra } });

export async function handle(req: Request, fetchFn: Fetch = fetch, limits: Limits = LIMITS, now = Date.now()): Promise<Response> {
  if (req.method !== "GET" && req.method !== "HEAD") return answer({ error: "method" }, 405, "no-store", { allow: "GET, HEAD" });
  const q = new URL(req.url).searchParams;
  const keys = [...q.keys()];
  const symbol = q.get("symbol") ?? "";
  if (keys.length !== 1 || keys[0] !== "symbol" || !TICKER.test(symbol)) return answer({ error: "not a ticker" }, 400, "no-store");

  const ip = (req.headers.get("x-real-ip") || req.headers.get("x-forwarded-for")?.split(",")[0] || "unknown").trim();
  const wait = limits.caller(ip, now) || limits.upstream(now);
  if (wait) return answer({ error: "rate limited" }, 429, "no-store", { "retry-after": String(wait) });

  let results: unknown;
  try {
    const r = await fetchFn(`${SEARCH_URL}?q=${encodeURIComponent(symbol)}`, {
      headers: { accept: "application/json" }, signal: AbortSignal.timeout(6000) });
    if (!r.ok) throw new Error(`search ${r.status}`);
    results = ((await r.json()) as { results?: unknown })?.results;
    if (!Array.isArray(results)) throw new Error("unexpected answer");
  } catch (e) {
    console.warn("logo-domain: search failed", symbol, (e as Error).message.slice(0, 120));
    return answer({ error: "search unavailable" }, 502, "no-store");
  }
  const domain = pickWebsite(symbol, results);
  return domain ? answer({ domain }, 200, CACHE_FOUND) : answer({ error: "not found" }, 404, CACHE_MISS);
}

export async function GET(request: Request): Promise<Response> {
  return handle(request);
}
export const HEAD = GET;
