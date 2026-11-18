// Logos from AllInvestView's ticker-logo CDN (stakeholder, 18 Nov 2026).
// - Source order: tickerlogos (a known domain, or one from /api/logo-domain), then our Finnhub URL, then FMP, then the
//   initials. The lazy load, async decoding, no-referrer and the fade-in stay.
// - Domain lookups: cached in memory and in localStorage (which may throw), never more than 6 at once.
// - Tile colour for any ticker: the tickerlogos image is sampled on a small canvas after it loads, with the rule of
//   scripts/logo_colors.py; until then, or when the canvas is tainted or throws, the soft white tile. Precomputed colours
//   in logo_colors.json win, but only for the very image they were sampled from.
// - Attribution: a visible, plain link to AllInvestView in the app shell, on every page that shows logos.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import css from "./app.css?raw";
import mainSrc from "./main.tsx?raw";
import indexHtml from "../index.html?raw";
import symbols from "./data/symbols.json";
import tiles from "./data/logo_colors.json";
import { fmpLogo, logoSources, StockBadge, TILES } from "./components/StockBadge";
import { LogoCredit, Shell } from "./components/Shell";
import {
  CREDIT_URL, MAX_LOOKUPS, TICKERLOGOS, _resetLogoCaches, cachedTile, lookupDomain, peekDomain, sampleAndStore, sampleImage,
  tickerLogo, tileFromPixels, type CanvasLike,
} from "./lib/logos";
import { paperRun, render } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

type Meta = Record<string, { name: string; logo: string | null; industry: string | null; domain: string | null }>;
const META = symbols as Meta;
const pageSources = import.meta.glob("./{pages,components}/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>;

/** A localStorage that works, or one that throws on every call (private mode, a full quota, storage off). */
function storage(broken = false) {
  const m = new Map<string, string>();
  const boom = () => { throw new DOMException("denied", "SecurityError"); };
  return { m, api: broken ? { getItem: boom, setItem: boom, removeItem: boom }
    : { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v), removeItem: (k: string) => void m.delete(k) } };
}
beforeEach(() => { _resetLogoCaches(); vi.stubGlobal("localStorage", storage().api); });
afterEach(() => { vi.unstubAllGlobals(); _resetLogoCaches(); });

const ok = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("domains for the known symbols", () => {
  it("every symbol has a domain field: a bare hostname or null (left to the runtime lookup)", () => {
    for (const [s, m] of Object.entries(META)) {
      expect("domain" in m, s).toBe(true);
      if (m.domain !== null) expect(m.domain, s).toMatch(/^(?!www\.)[a-z0-9-]+(\.[a-z0-9-]+)+$/);
    }
    expect(META.AAPL.domain).toBe("apple.com");
    expect(META.MSFT.domain).toBe("microsoft.com");
    expect(Object.values(META).filter((m) => m.domain).length).toBeGreaterThanOrEqual(50);
  });
});

describe("source order", () => {
  it("tickerlogos first, then our Finnhub logo, then FMP; the initials when all fail", () => {
    expect(TICKERLOGOS).toBe("https://cdn.tickerlogos.com/");
    expect(tickerLogo("apple.com")).toBe("https://cdn.tickerlogos.com/apple.com");
    expect(logoSources("AAPL", "apple.com")).toEqual(["https://cdn.tickerlogos.com/apple.com", META.AAPL.logo, fmpLogo("AAPL")]);
    expect(logoSources("AAPL", null)).toEqual([META.AAPL.logo, fmpLogo("AAPL")]);
    expect(logoSources("NBIS", "nebius.com")).toEqual(["https://cdn.tickerlogos.com/nebius.com", fmpLogo("NBIS")]);
    expect(logoSources("NBIS", null)).toEqual([fmpLogo("NBIS")]);
    expect(logoSources("1234", null)).toEqual([]);
  });
  it("a known symbol renders its tickerlogos image at once: lazy, async, no referrer, CORS for sampling, faded in", () => {
    const html = renderToStaticMarkup(<StockBadge symbol="AAPL" size={36} />);
    const img = html.match(/<img [^>]*>/)![0];
    expect(img).toContain('src="https://cdn.tickerlogos.com/apple.com"');
    for (const a of ['loading="lazy"', 'decoding="async"', 'referrerPolicy="no-referrer"', 'crossorigin="anonymous"', 'alt=""', 'width="39"', 'height="39"'])
      expect(img, a).toContain(a);
    expect(img).not.toContain('class="loaded"');
    expect(css).toMatch(/\.badge\.logo img \{[^}]*opacity: 0; transition: opacity/);
  });
  it("only the tickerlogos image asks for CORS: Finnhub's and FMP's do not", () => {
    const html = renderToStaticMarkup(<StockBadge symbol="DIS" />);  // no domain known yet: still looking it up
    expect(html).not.toContain("<img");
    expect(html).toMatch(/class="badge logo"[^>]*--tile-bg:var\(--grey-50\)/);   // the soft white tile while it looks
    // the lookup answered "none": our Finnhub logo, on its precomputed colour, without crossorigin
    localStorage.setItem("kb.logo-domains.v1", JSON.stringify({ DIS: [null, Date.now()] }));
    _resetLogoCaches({ keepStorage: true });
    const dis = renderToStaticMarkup(<StockBadge symbol="DIS" />);
    expect(dis).toContain(`src="${META.DIS.logo}"`);
    expect(dis).not.toContain("crossorigin");
    expect(dis).toContain(`--tile-bg:${TILES.DIS.bg}`);
  });
  it("an invalid ticker makes no request and shows the initials", () => {
    expect(peekDomain("1234")).toEqual({ domain: null });
    expect(renderToStaticMarkup(<StockBadge symbol="1234" size={48} />)).toContain(">1234</span>");
  });
});

describe("tile colours", () => {
  it("precomputed colours belong to the image they were sampled from (the `from` URL)", () => {
    for (const [s, t] of Object.entries(TILES)) {
      if (!t.sampled) continue;
      expect(t.from, s).toBeTruthy();
      expect([META[s].logo, META[s].domain && tickerLogo(META[s].domain!)], s).toContain(t.from);
    }
    expect(Object.values(tiles).filter((t) => (t as { sampled: boolean }).sampled).length).toBe(59);
  });
  it("a precomputed colour sampled from Finnhub is not used for the tickerlogos image: that waits for the browser's sample", () => {
    expect(TILES.AAPL.from).toBe(META.AAPL.logo);
    const html = renderToStaticMarkup(<StockBadge symbol="AAPL" />);
    expect(html).toMatch(/--tile-bg:var\(--grey-50\);--tile-pad:12%/);
  });
  it("a precomputed colour sampled from the tickerlogos image wins over any browser sample", () => {
    const was = TILES.AAPL;
    TILES.AAPL = { bg: "#123456", pad: 0, sampled: true, from: "https://cdn.tickerlogos.com/apple.com" };
    try {
      localStorage.setItem("kb.logo-tiles.v1", JSON.stringify({ "apple.com": { bg: "#F4F5F7", pad: 12 } }));
      _resetLogoCaches({ keepStorage: true });
      expect(renderToStaticMarkup(<StockBadge symbol="AAPL" />)).toContain("--tile-bg:#123456;--tile-pad:0%");
    } finally { TILES.AAPL = was; }
  });
  it("a browser sample cached for the domain colours the tickerlogos tile", () => {
    localStorage.setItem("kb.logo-tiles.v1", JSON.stringify({ "apple.com": { bg: "#1B1E23", fg: "#FAFAFA", pad: 12 } }));
    _resetLogoCaches({ keepStorage: true });
    expect(cachedTile("apple.com")).toEqual({ bg: "#1B1E23", fg: "#FAFAFA", pad: 12 });
    expect(renderToStaticMarkup(<StockBadge symbol="AAPL" />)).toContain("--tile-bg:#1B1E23;--tile-pad:12%");
  });
  it("FMP's logo is never coloured (it is not sampled): the soft white tile", () => {
    localStorage.setItem("kb.logo-domains.v1", JSON.stringify({ NBIS: [null, Date.now()] }));
    _resetLogoCaches({ keepStorage: true });
    const html = renderToStaticMarkup(<StockBadge symbol="NBIS" />);
    expect(html).toContain(`src="${fmpLogo("NBIS")}"`);
    expect(html).toMatch(/--tile-bg:var\(--grey-50\)/);
  });
});

describe("the sampling rule (same as scripts/logo_colors.py)", () => {
  const img = (w: number, h: number, at: (x: number, y: number) => [number, number, number, number]) => {
    const px = new Uint8ClampedArray(w * h * 4);
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) px.set(at(x, y), (y * w + x) * 4);
    return px;
  };
  it("a solid coloured background goes full-bleed on that colour", () => {
    expect(tileFromPixels(img(64, 64, () => [200, 16, 46, 255]), 64, 64)).toEqual({ bg: "#C8102E", pad: 0 });
  });
  it("a solid white or near-white background is the soft white tile with padding", () => {
    expect(tileFromPixels(img(64, 64, (x, y) => (x > 20 && x < 40 && y > 20 && y < 40 ? [0, 0, 0, 255] : [250, 250, 250, 255])), 64, 64)).toEqual({ bg: "#F4F5F7", pad: 12 });
  });
  it("a transparent edge: dark ink on the soft white tile, light ink on the dark tile", () => {
    const ink = (c: number) => img(64, 64, (x, y) => (x > 16 && x < 48 && y > 16 && y < 48 ? [c, c, c, 255] : [0, 0, 0, 0]));
    expect(tileFromPixels(ink(20), 64, 64)).toEqual({ bg: "#F4F5F7", fg: "#141414", pad: 12 });
    expect(tileFromPixels(ink(240), 64, 64)).toEqual({ bg: "#1B1E23", fg: "#F0F0F0", pad: 12 });
  });
  it("an uneven opaque edge is treated by its ink, as the script does", () => {
    const noisy = img(64, 64, (x, y) => ((x + y) % 2 ? [255, 255, 255, 255] : [0, 0, 255, 255]));
    expect(tileFromPixels(noisy, 64, 64)).toMatchObject({ pad: 12 });
  });
  it("an empty image gives nothing (the soft white tile stays)", () => {
    expect(tileFromPixels(img(32, 32, () => [0, 0, 0, 0]), 32, 32)).toBeNull();
  });
});

describe("sampling in the browser: never blocks, never breaks", () => {
  const fakeImg = { naturalWidth: 200, naturalHeight: 100, complete: true } as unknown as HTMLImageElement;
  const canvas = (ctx: unknown): CanvasLike => ({ width: 0, height: 0, getContext: () => ctx as never });
  it("draws at most 96 px on a small canvas and reads it back", () => {
    let drawn: number[] = [];
    const c = canvas({
      drawImage: (_i: unknown, x: number, y: number, w: number, h: number) => { drawn = [x, y, w, h]; },
      getImageData: (_x: number, _y: number, w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4).fill(255), width: w, height: h }),
    });
    expect(sampleImage(fakeImg, () => c)).toEqual({ bg: "#F4F5F7", pad: 12 });
    expect(drawn).toEqual([0, 0, 96, 48]);
  });
  it("a tainted canvas (SecurityError) or a throwing context gives null and caches nothing", () => {
    const tainted = canvas({ drawImage: () => {}, getImageData: () => { throw new DOMException("tainted", "SecurityError"); } });
    expect(sampleImage(fakeImg, () => tainted)).toBeNull();
    expect(sampleImage(fakeImg, () => canvas(null))).toBeNull();
    expect(sampleImage(fakeImg, () => { throw new Error("no canvas"); })).toBeNull();
    expect(sampleImage({ naturalWidth: 0, naturalHeight: 0 } as HTMLImageElement, () => tainted)).toBeNull();
    expect(sampleAndStore("apple.com", fakeImg, () => tainted)).toBeNull();
    expect(cachedTile("apple.com")).toBeUndefined();
  });
  it("a good sample is cached per domain in localStorage, and survives storage that throws", () => {
    const white = canvas({ drawImage: () => {}, getImageData: (_x: number, _y: number, w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4).fill(255) }) });
    expect(sampleAndStore("apple.com", fakeImg, () => white)).toEqual({ bg: "#F4F5F7", pad: 12 });
    expect(JSON.parse(localStorage.getItem("kb.logo-tiles.v1")!)).toEqual({ "apple.com": { bg: "#F4F5F7", pad: 12 } });
    vi.stubGlobal("localStorage", storage(true).api);
    _resetLogoCaches({ keepStorage: true });
    expect(cachedTile("apple.com")).toBeUndefined();
    expect(sampleAndStore("apple.com", fakeImg, () => white)).toEqual({ bg: "#F4F5F7", pad: 12 });   // kept in memory
    expect(cachedTile("apple.com")).toEqual({ bg: "#F4F5F7", pad: 12 });
  });
  it("StockBadge samples only after the image has loaded, off the render, and only a tickerlogos image", async () => {
    const src = (await import("./components/StockBadge.tsx?raw")).default;
    expect(src).toMatch(/onLoad=\{[^}]*\bscheduleSample\(/);
    expect(src).toMatch(/requestIdleCallback/);
  });
});

describe("domain lookups", () => {
  it("asks /api/logo-domain once per symbol, then answers from memory and localStorage", async () => {
    const f = vi.fn(async () => ok({ domain: "nebius.com" }));
    expect(peekDomain("NBIS")).toBeUndefined();
    expect(await lookupDomain("NBIS", f)).toBe("nebius.com");
    expect(await lookupDomain("NBIS", f)).toBe("nebius.com");
    expect(f).toHaveBeenCalledTimes(1);
    expect(f.mock.calls[0]).toEqual(["/api/logo-domain?symbol=NBIS", expect.anything()]);
    expect(peekDomain("NBIS")).toEqual({ domain: "nebius.com" });
    _resetLogoCaches({ keepStorage: true });                                        // a new page load
    expect(peekDomain("NBIS")).toEqual({ domain: "nebius.com" });
  });
  it("a known domain never asks", async () => {
    const f = vi.fn();
    expect(peekDomain("AAPL")).toEqual({ domain: "apple.com" });
    expect(await lookupDomain("AAPL", f)).toBe("apple.com");
    expect(f).not.toHaveBeenCalled();
  });
  it("a 404 is remembered as none; an error or a bad answer is none for this page only", async () => {
    expect(await lookupDomain("ZZZZ", async () => ok({ error: "not found" }, 404))).toBeNull();
    expect(JSON.parse(localStorage.getItem("kb.logo-domains.v1")!).ZZZZ[0]).toBeNull();
    for (const [s, f] of [["YYYY", async () => ok({}, 502)], ["XXXX", async () => ok({ domain: "javascript:alert(1)" })],
      ["WWWW", async () => new Response("<html>", { status: 200 })], ["VVVV", async () => { throw new TypeError("offline"); }]] as const) {
      expect(await lookupDomain(s, f as never), s).toBeNull();
      expect(JSON.parse(localStorage.getItem("kb.logo-domains.v1") ?? "{}")[s], s).toBeUndefined();
      expect(peekDomain(s), s).toEqual({ domain: null });
    }
  });
  it("refuses non-ticker input without a request", async () => {
    const f = vi.fn();
    for (const s of ["aapl", "TOOLONG", "BRK-B", "", "A1", "../x"]) expect(await lookupDomain(s, f), s).toBeNull();
    expect(f).not.toHaveBeenCalled();
  });
  it("works when localStorage throws on every call", async () => {
    vi.stubGlobal("localStorage", storage(true).api);
    _resetLogoCaches({ keepStorage: true });
    expect(peekDomain("NBIS")).toBeUndefined();
    expect(await lookupDomain("NBIS", async () => ok({ domain: "nebius.com" }))).toBe("nebius.com");
    expect(peekDomain("NBIS")).toEqual({ domain: "nebius.com" });
  });
  it("an old answer is asked again (30 days for a domain, a day for none)", () => {
    const day = 864e5;
    localStorage.setItem("kb.logo-domains.v1", JSON.stringify({ OLDA: ["a.com", Date.now() - 31 * day], NEWA: ["a.com", Date.now() - 29 * day],
      OLDN: [null, Date.now() - 2 * day], NEWN: [null, Date.now() - 3600e3], BADX: ["not a host", Date.now()] }));
    _resetLogoCaches({ keepStorage: true });
    expect(peekDomain("OLDA")).toBeUndefined();
    expect(peekDomain("NEWA")).toEqual({ domain: "a.com" });
    expect(peekDomain("OLDN")).toBeUndefined();
    expect(peekDomain("NEWN")).toEqual({ domain: null });
    expect(peekDomain("BADX")).toBeUndefined();
  });
  it(`never more than ${MAX_LOOKUPS} at once on a page with many tiles; every one is answered`, async () => {
    expect(MAX_LOOKUPS).toBe(6);
    let active = 0, peak = 0;
    const release: (() => void)[] = [];
    const f = vi.fn((url: string) => {
      active++; peak = Math.max(peak, active);
      return new Promise<Response>((done) => release.push(() => { active--; done(ok({ domain: `${url.split("=")[1].toLowerCase()}.com` })); }));
    });
    const syms = Array.from({ length: 40 }, (_, i) => `T${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26))}`);
    const all = Promise.all([...syms, ...syms].map((s) => lookupDomain(s, f as never)));   // the same symbol twice: one request
    for (let i = 0; i < 200; i++) {
      await new Promise((r) => setTimeout(r, 0));
      expect(active).toBeLessThanOrEqual(MAX_LOOKUPS);
      if (f.mock.calls.length === 40 && !release.length) break;
      release.splice(0).forEach((go) => go());
    }
    const got = await all;
    expect(peak).toBe(MAX_LOOKUPS);
    expect(f).toHaveBeenCalledTimes(40);
    expect(got.slice(0, 40)).toEqual(syms.map((s) => `${s.toLowerCase()}.com`));
  });
});

describe("attribution: Logos by AllInvestView", () => {
  const credit = (html: string) => html.match(/<a [^>]*href="https:\/\/www\.allinvestview\.com\/tools\/ticker-logos\/"[^>]*>[^<]*<\/a>/g) ?? [];
  it("is one plain, visible link: no nofollow, sponsored or ugc, not hidden", () => {
    expect(CREDIT_URL).toBe("https://www.allinvestview.com/tools/ticker-logos/");
    const html = renderToStaticMarkup(<LogoCredit />);
    const a = credit(html)[0] ?? "";
    expect(a).toBeTruthy();
    expect(html).toMatch(/Logos by <a [^>]*>AllInvestView<\/a>/);
    const rel = a.match(/rel="([^"]*)"/)?.[1] ?? "";
    for (const bad of ["nofollow", "sponsored", "ugc"]) expect(rel.split(/\s+/), bad).not.toContain(bad);
    expect(a).not.toMatch(/hidden|aria-hidden|style=|class="sr-only"/);
    expect(html).not.toMatch(/hidden|sr-only[^"]*"[^>]*>Logos/);
  });
  it("its CSS keeps it visible on every width, as a legible muted line with a 44 px tap target", () => {
    const rules = [...css.matchAll(/([^{}]*\.credit[^{}]*)\{([^}]*)\}/g)].map((m) => m[2]).join(";");
    expect(rules).toMatch(/color: var\(--stale\)/);
    expect(rules).toMatch(/font-size: var\(--t-cap\)/);
    expect(rules).toMatch(/min-height: 44px/);
    expect(rules).not.toMatch(/display: none|visibility: hidden|opacity: 0|clip|font-size: 0|width: 0|height: 0/);
    expect(css).not.toMatch(/\.credit[^{]*\{[^}]*display: none/);
  });
  const paths = ["/", "/positions", "/positions/12", "/activity", "/more", "/report", "/health", "/gate", "/strategy", "/strategy/fine-tune", "/settings"];
  for (const path of paths)
    it(`is in the shell on ${path}`, () => {
      vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
      const run = paperRun();
      const html = render(<Shell />, { path, route: "*", seeds: [{ key: ["runs"], data: [run] },
        { key: ["overview", run.id], data: { run, daily: [], heartbeat: [], pending_commands: [], ledger: null, today: null, halts_today: [] } }] });
      expect(credit(html), path).toHaveLength(1);
    });
  it("every page or component that shows logos is routed inside the shell", () => {
    const shows = Object.entries(pageSources).filter(([f, s]) => !f.includes("StockBadge") && /<Stock(Badge|Cell)\b/.test(s)).map(([f]) => f);
    expect(shows.length).toBeGreaterThan(5);
    const outside = mainSrc.slice(mainSrc.lastIndexOf("</Route>", mainSrc.indexOf('path="start"')));
    for (const f of shows) {
      const name = f.split("/").pop()!.replace(".tsx", "");
      expect(outside, name).not.toContain(`<${name}`);
    }
    expect(pageSources["./pages/Onboarding.tsx"]).not.toMatch(/StockBadge|StockCell/);
  });
  it("the pages send no referrer (vercel.json), and the logo host is preconnected for CORS", async () => {
    const vercel = (await import("../vercel.json")).default as { headers: { headers: { key: string; value: string }[] }[] };
    expect(vercel.headers[0].headers).toContainEqual({ key: "Referrer-Policy", value: "no-referrer" });
    expect(indexHtml).toContain('<link rel="preconnect" href="https://cdn.tickerlogos.com" crossorigin />');
  });
});
