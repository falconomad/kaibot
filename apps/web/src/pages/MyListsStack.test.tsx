// @vitest-environment jsdom
// Stakeholder, 25 Nov 2026: a saved list's card shows its tickers' company logos, stacked. These tests mount the
// Strategy page in jsdom with the lists as the store keeps them (plain symbols), and check: the logos show after a
// reload from the domains cached in localStorage, with no lookup; a long list shows 4 logos and "+N"; a ticker with no
// logo gets the letter tile; changing the selected card asks nothing again and keeps the same images; the card's name
// lists the tickers for screen readers. Each test loads the modules afresh, so the logo caches start as a new page's do.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Route } from "react-router-dom";

const calls = vi.hoisted(() => ({ tickerLists: 0 }));
vi.mock("../lib/api", async () => {
  const never = () => new Promise<never>(() => {});
  const real = await import("./testkit-api");
  const api = new Proxy({}, { get: (_t, k) => (k === "tickerLists" ? () => { calls.tickerLists++; return never(); } : never) });
  return { ...real, api };
});

const DOMAINS_KEY = "kb.logo-domains.v1";
// Tickers outside symbols.json, so every logo goes through the /api/logo-domain lookup and its caches.
const FIVE = ["ZQAA", "ZQAB", "ZQAC", "ZQAD", "ZQAE"];
const EIGHT = ["ZQBA", "ZQBB", "ZQBC", "ZQBD", "ZQBE", "ZQBF", "ZQBG", "ZQBH"];
const dom = (s: string) => `${s.toLowerCase()}.example.com`;

let store: Map<string, string>;
let lookups: string[];
function stubs(answer: (symbol: string) => string | null) {
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); } });
  vi.stubGlobal("fetch", vi.fn(async (url: string) => {
    const symbol = decodeURIComponent(String(url).split("symbol=")[1] ?? "");
    if (String(url).includes("/api/logo-domain")) lookups.push(symbol);
    const d = answer(symbol);
    return d ? new Response(JSON.stringify({ domain: d }), { status: 200, headers: { "content-type": "application/json" } }) : new Response("", { status: 404 });
  }));
}

async function open(lists: { name: string; symbols: string[] }[]) {
  vi.resetModules();
  const k = await import("../a11y/dom");
  const { Strategy, StrategyHome } = await import("./Strategy");
  const { configLists } = await import("../a11y/fixtures");
  k.reducedMotionOn();
  await k.mount(<Strategy />, { path: "/strategy", route: "/strategy", children: <Route index element={<StrategyHome />} />,
    seeds: [{ key: ["config", 1], data: configLists() }, { key: ["chapters", 1], data: [] },
      { key: ["tickerLists"], data: { max_tickers: 40, max_lists: 20, min_only: 5, lists: lists.map((l) => ({ ...l, updated_at: null })) } }] });
  for (let i = 0; i < 4; i++) await k.settle();
  return k;
}
const cardOf = (name: string) => [...document.querySelectorAll<HTMLButtonElement>(".kb-lcard")].find((b) => b.querySelector(".kb-lcard-name")?.textContent === name)!;
const imgs = (card: Element) => [...card.querySelectorAll<HTMLImageElement>(".kb-lstack img")];

beforeEach(() => { store = new Map(); lookups = []; calls.tickerLists = 0; });
afterEach(async () => { (await import("../a11y/dom")).unmount(); vi.unstubAllGlobals(); });

describe("stacked logos on a saved list's card", () => {
  it("after a reload, a list stored as plain symbols shows its logos from the cached domains, with no lookup", async () => {
    store.set(DOMAINS_KEY, JSON.stringify(Object.fromEntries(FIVE.map((s) => [s, [dom(s), Date.now()]]))));
    stubs(() => { throw new Error("no lookup expected"); });
    await open([{ name: "High beta", symbols: FIVE }]);
    const card = cardOf("High beta");
    expect(imgs(card).map((i) => i.getAttribute("src"))).toEqual(FIVE.map((s) => `https://cdn.tickerlogos.com/${dom(s)}`));
    for (const i of imgs(card)) { expect(i.getAttribute("loading")).toBe("lazy"); expect(i.getAttribute("decoding")).toBe("async"); }
    expect(card.querySelector(".kb-lstack b")).toBeNull();
    expect(lookups.filter((s) => FIVE.includes(s))).toEqual([]);   // the editor's chips (the draft's tickers) look up their own
    // Read by its name: the list, then its tickers; the logos themselves are hidden.
    expect(card.getAttribute("aria-label")).toBe("High beta: ZQAA, ZQAB, ZQAC, ZQAD, ZQAE");
    expect(card.querySelector(".kb-lstack")!.getAttribute("aria-hidden")).toBe("true");
  });

  it("a list longer than 5 shows the first 4 logos and +N for the rest", async () => {
    stubs(dom);
    await open([{ name: "AI infrastructure", symbols: EIGHT }]);
    const card = cardOf("AI infrastructure");
    expect(card.querySelectorAll(".kb-lstack .badge")).toHaveLength(4);
    expect(card.querySelector(".kb-lstack b")!.textContent).toBe("+4");
    expect(lookups.filter((s) => EIGHT.includes(s)).sort()).toEqual(EIGHT.slice(0, 4));   // only the logos shown are looked up
    expect(card.textContent).toContain("8 tickers");
    expect(card.getAttribute("aria-label")).toBe(`AI infrastructure: ${EIGHT.join(", ")}`);
  });

  it("a ticker with no logo gets the letter tile", async () => {
    stubs((s) => (s === "ZQAE" ? null : dom(s)));             // 404: the lookup knows no company site
    await open([{ name: "High beta", symbols: FIVE }]);
    const card = cardOf("High beta");
    // No domain: the last source (FMP) is tried; when it fails too, the initials, as on every tile.
    const last = imgs(card).find((i) => i.src.includes("ZQAE"))!;
    const { doing } = await import("../a11y/dom");
    await doing(() => { last.dispatchEvent(new Event("error")); });
    const tiles = [...card.querySelectorAll(".kb-lstack .badge")];
    expect(tiles).toHaveLength(5);
    expect(tiles[4].classList.contains("logo")).toBe(false);
    expect(tiles[4].textContent).toBe("ZQA");
    expect(imgs(card)).toHaveLength(4);
  });

  it("changing the selected card looks nothing up again and keeps the same images", async () => {
    stubs(dom);
    const k = await open([{ name: "High beta", symbols: FIVE }, { name: "AI infrastructure", symbols: EIGHT }]);
    const before = [...document.querySelectorAll(".kb-lstack img")];
    expect(before).toHaveLength(9);
    const asked = [...lookups], lists = calls.tickerLists;
    for (const name of ["High beta", "AI infrastructure", "New list", "High beta"]) {
      await k.doing(() => { cardOf(name).click(); });
      await k.settle();
      expect(cardOf(name).getAttribute("aria-checked")).toBe("true");
    }
    const after = [...document.querySelectorAll(".kb-lstack img")];
    expect(after).toHaveLength(9);
    after.forEach((img, i) => expect(img).toBe(before[i]));  // the same elements: nothing remounted, nothing reloaded
    expect(lookups).toEqual(asked);
    expect(calls.tickerLists).toBe(lists);
  });
});
