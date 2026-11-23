// @vitest-environment jsdom
// The headline line on Home (migration 0043; lib/headlines.ts, components/Headlines.tsx):
// - plain text only: a headline that looks like HTML is shown as those characters, never as markup;
// - links: https only, opened with rel="noopener noreferrer"; anything else gives no link at all;
// - the licence guard: owner_only rows never reach a non-owner view, and a row that lies about its licence is owner_only;
// - held and decided stocks first; one headline at a time, the next after 6 s; a tap pauses;
// - reduced motion: a short static list and no timer;
// - accessible: no serious axe finding in either mode; the card's name is its visible text (no aria-label), the pause
//   state is aria-pressed; it holds still while hidden, while a keyboard has focus on it and while a mouse is over it;
// - the card is its own chunk (pages/Live.tsx imports it lazily), so the first load does not grow.
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { auditNode, lines } from "./a11y/axe";
import { HeadlineCard } from "./components/Headlines";
import cardSrc from "./components/Headlines.tsx?raw";
import homeSrc from "./components/Home.tsx?raw";
import linesSrc from "./components/Lines.tsx?raw";
import shellSrc from "./components/Shell.tsx?raw";
import libSrc from "./lib/headlines.ts?raw";
import mainSrc from "./main.tsx?raw";
import budget from "../size-budget.json";
import liveSrc from "./pages/Live.tsx?raw";
import { DWELL_MS, type Headline, httpsOnly, nextIndex, ordered, parseHeadline, parseHeadlines, plain, visibleTo } from "./lib/headlines";

vi.mock("./lib/api", () => ({ supa: { rpc: vi.fn() } }));
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const NOW = Date.parse("2026-11-23T16:00:00Z");
const row = (o: Record<string, unknown> = {}) => ({ id: 1, symbols: ["AAPL"], headline: "Apple names a new CFO", source: "alpaca_benzinga",
  licence: "owner_only", url: "https://www.benzinga.com/a", tags: ["management_change"], focus: false, created_at: "2026-11-23T15:00:00Z", ...o });
const hl = (o: Record<string, unknown> = {}) => parseHeadline(row(o)) as Headline;

describe("parsing and the licence guard", () => {
  it("keeps https links only", () => {
    expect(httpsOnly("https://www.sec.gov/Archives/x-index.htm")).toBe("https://www.sec.gov/Archives/x-index.htm");
    for (const bad of ["http://x.com/a", "javascript:alert(1)", "data:text/html,<b>", "https://x.com/a b", "//x.com", "https:/x", 5, null, "https://x.com/\"><script>"])
      expect(httpsOnly(bad)).toBeNull();
  });

  it("makes untrusted text one plain line of at most 140 characters", () => {
    expect(plain("a\u0000b\nc‮d")).toBe("a b c d");
    expect(plain("x".repeat(300))).toHaveLength(140);
    expect(plain(7)).toBe("");
  });

  it("skips rows it cannot read and drops unknown tags and bad symbols", () => {
    expect(parseHeadline(null)).toBeNull();
    expect(parseHeadline(row({ source: "reuters" }))).toBeNull();
    expect(parseHeadline(row({ symbols: ["not a symbol"] }))).toBeNull();
    expect(parseHeadline(row({ created_at: "soon" }))).toBeNull();
    expect(hl({ tags: ["analyst_up", "results", "<x>"], symbols: ["AAPL", "bad", "MSFT"] })).toMatchObject({ tags: ["analyst_up"], symbols: ["AAPL", "MSFT"] });
  });

  it("never shows an owner_only headline to anyone but the owner", () => {
    const data = { items: [row({ id: 1 }), row({ id: 2, source: "sec_edgar", licence: "public", headline: "AAPL filed results (8-K item 2.02)" })] };
    expect(parseHeadlines(data, "owner").map((h) => h.id).sort()).toEqual([1, 2]);
    expect(parseHeadlines(data, "other").map((h) => h.id)).toEqual([2]);
    expect(visibleTo([hl()], "other")).toEqual([]);
  });

  it("an Alpaca row that says public is still owner_only (fail closed)", () => {
    const liar = parseHeadline(row({ licence: "public" }))!;
    expect(liar.licence).toBe("owner_only");
    expect(visibleTo([liar], "other")).toEqual([]);
    expect(parseHeadline(row({ source: "sec_edgar", licence: "owner_only" }))!.licence).toBe("owner_only");
  });

  it("puts held and decided stocks first, then the newest", () => {
    const out = ordered([hl({ id: 1, created_at: "2026-11-23T15:50:00Z" }), hl({ id: 2, focus: true, created_at: "2026-11-23T10:00:00Z" }),
      hl({ id: 3, created_at: "2026-11-23T15:55:00Z" })]);
    expect(out.map((h) => h.id)).toEqual([2, 3, 1]);
    expect(nextIndex(2, 3)).toBe(0);
  });
});

let root: Root | null = null;
// four, so that three dwells never wrap back to the first
const FOUR = ["First", "Second", "Third", "Fourth"].map((headline, i) => hl({ id: i + 1, headline }));
const motion = (reduce: boolean) => {
  window.matchMedia = ((q: string) => ({ matches: reduce && /prefers-reduced-motion/.test(q), media: q, onchange: null,
    addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false })) as typeof window.matchMedia;
};
async function show(items: Headline[]) {
  document.body.innerHTML = '<main id="app"></main>';
  root = createRoot(document.getElementById("app")!);
  await act(async () => { root!.render(<HeadlineCard items={items} now={NOW} />); });
}
const text = () => document.querySelector(".hl-text")?.textContent;

describe("the card", () => {
  beforeEach(() => { vi.useFakeTimers(); motion(false); });
  afterEach(async () => { await act(async () => root?.unmount()); root = null; vi.useRealTimers(); });

  it("shows a headline as text, never as HTML", async () => {
    await show([hl({ headline: '<img src=x onerror="alert(1)"><b>Apple</b> <script>x()</script>' })]);
    expect(text()).toBe('<img src=x onerror="alert(1)"><b>Apple</b> <script>x()</script>');
    expect(document.querySelector(".hl img, .hl b, .hl script")).toBeNull();
    expect(document.querySelector(".hl-chip")?.textContent).toBe("Management");
  });

  it("links out over https only, with noopener noreferrer", async () => {
    await show([hl()]);
    const a = document.querySelector<HTMLAnchorElement>("a.hl-open")!;
    expect(a.getAttribute("href")).toBe("https://www.benzinga.com/a");
    expect(a.getAttribute("rel")).toBe("noopener noreferrer");
    expect(a.getAttribute("target")).toBe("_blank");
    await show([hl({ url: "javascript:alert(1)" })]);
    expect(document.querySelector("a")).toBeNull();
    await show([hl({ url: "http://www.benzinga.com/a" })]);
    expect(document.querySelector("a")).toBeNull();
  });

  it("cycles every 6 s and a tap pauses it", async () => {
    await show([hl({ id: 1, headline: "First" }), hl({ id: 2, headline: "Second" }), hl({ id: 3, headline: "Third" })]);
    expect(text()).toBe("First");
    await act(async () => { vi.advanceTimersByTime(DWELL_MS + 400); });
    expect(text()).toBe("Second");
    await act(async () => { document.querySelector<HTMLButtonElement>("button.hl-card")!.click(); });
    expect(document.querySelector("button.hl-card")!.getAttribute("aria-pressed")).toBe("true");
    await act(async () => { vi.advanceTimersByTime(DWELL_MS * 5); });
    expect(text()).toBe("Second");
    await act(async () => { document.querySelector<HTMLButtonElement>("button.hl-card")!.click(); });
    await act(async () => { vi.advanceTimersByTime(DWELL_MS + 400); });
    expect(text()).toBe("Third");
  });

  it("one headline: no timer at all", async () => {
    await show([hl()]);
    expect(vi.getTimerCount()).toBe(0);
  });

  it("holds still while the tab is hidden (3 dwells), and goes on when shown", async () => {
    await show(FOUR);
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
    try {
      await act(async () => { vi.advanceTimersByTime(DWELL_MS * 3 + 400); });
      expect(text()).toBe("First");
      expect(document.querySelector("button.hl-card")!.className).not.toMatch(/fading/);
    } finally {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "visible" });
    }
    await act(async () => { vi.advanceTimersByTime(DWELL_MS + 400); });
    expect(text()).toBe("Second");
  });

  it("its name is the visible text, its state aria-pressed, its hint a description", async () => {
    await show([hl({ id: 1 }), hl({ id: 2, headline: "Second" })]);
    const b = document.querySelector<HTMLButtonElement>("button.hl-card")!;
    expect(b.hasAttribute("aria-label")).toBe(false);
    expect(b.getAttribute("aria-pressed")).toBe("false");
    expect(document.getElementById(b.getAttribute("aria-describedby")!)?.textContent).toBe("Press to pause the headlines.");
    await act(async () => { b.click(); });
    expect(b.getAttribute("aria-pressed")).toBe("true");
    expect(document.getElementById(b.getAttribute("aria-describedby")!)?.textContent).toBe("Paused. Press to go on.");
  });

  it("holds still while a keyboard has focus on it, and goes on after blur; a tap's focus is no hold", async () => {
    await show(FOUR);
    const b = () => document.querySelector<HTMLButtonElement>("button.hl-card")!;
    await act(async () => { b().focus(); });
    await act(async () => { vi.advanceTimersByTime(DWELL_MS * 3 + 400); });
    expect(text()).toBe("First");
    await act(async () => { b().blur(); });
    await act(async () => { vi.advanceTimersByTime(DWELL_MS + 400); });
    expect(text()).toBe("Second");
    // a tap: pointerdown, focus, click (pause), and a second tap (go on): it cycles again with focus still on it
    await act(async () => { b().dispatchEvent(new Event("pointerdown", { bubbles: true })); b().focus(); b().click(); });
    await act(async () => { b().dispatchEvent(new Event("pointerdown", { bubbles: true })); b().click(); });
    expect(document.activeElement).toBe(b());
    await act(async () => { vi.advanceTimersByTime(DWELL_MS + 400); });
    expect(text()).toBe("Third");
  });

  it("holds still while a mouse is over it; a touch is no hover", async () => {
    await show(FOUR);
    const sec = document.querySelector(".hl")!;
    const ptr = (type: string, pointerType: string) => {
      const e = new MouseEvent(type, { bubbles: true, relatedTarget: type === "pointerover" ? document.body : null });
      Object.defineProperty(e, "pointerType", { value: pointerType });
      return e;
    };
    await act(async () => { sec.dispatchEvent(ptr("pointerover", "touch")); });
    await act(async () => { vi.advanceTimersByTime(DWELL_MS + 400); });
    expect(text()).toBe("Second");
    await act(async () => { sec.dispatchEvent(ptr("pointerover", "mouse")); });
    await act(async () => { vi.advanceTimersByTime(DWELL_MS * 3 + 400); });
    expect(text()).toBe("Second");
    await act(async () => { sec.dispatchEvent(ptr("pointerout", "mouse")); });
    await act(async () => { vi.advanceTimersByTime(DWELL_MS + 400); });
    expect(text()).toBe("Third");
  });

  it("axe: no serious or critical finding, cycling (playing and paused) and as the reduced-motion list", async () => {
    vi.useRealTimers();
    const items = [hl({ id: 1 }), hl({ id: 2, source: "sec_edgar", licence: "public", tags: [], headline: "AAPL filed results (8-K item 2.02)",
      url: "https://www.sec.gov/Archives/x-index.htm" })];
    await show(items);
    expect(lines(await auditNode(document.getElementById("app")!))).toEqual([]);
    await act(async () => { document.querySelector<HTMLButtonElement>("button.hl-card")!.click(); });
    expect(lines(await auditNode(document.getElementById("app")!))).toEqual([]);
    motion(true);
    await show(items);
    expect(document.querySelector(".hl-list")).not.toBeNull();
    expect(lines(await auditNode(document.getElementById("app")!))).toEqual([]);
  });

  it("reduced motion: a static list of three, nothing moves", async () => {
    motion(true);
    await show([1, 2, 3, 4, 5].map((id) => hl({ id, headline: `H${id}` })));
    expect([...document.querySelectorAll(".hl-text")].map((e) => e.textContent)).toEqual(["H1", "H2", "H3"]);
    expect(document.querySelector("button.hl-card")).toBeNull();
    expect(vi.getTimerCount()).toBe(0);
    await act(async () => { vi.advanceTimersByTime(DWELL_MS * 3); });
    expect(text()).toBe("H1");
  });
});

describe("loading", () => {
  it("the card is lazy: pages/Live.tsx loads it only through the Lines chunk, with the market notices", () => {
    const live = liveSrc;
    expect(live).toMatch(/lazy\(\(\) => import\("\.\.\/components\/Lines"\)\)/);
    expect(live).not.toMatch(/^import .*components\/(Headlines|Lines|Notices)/m);
    expect(linesSrc).toMatch(/^import Headlines from "\.\/Headlines";/m);
    for (const s of [mainSrc, shellSrc, homeSrc]) expect(s).not.toMatch(/headlines/i);
  });

  it("leaves the first-load budget as it is (the build's size check measures it)", () => {
    expect(budget.budget.first_load_js_gzip).toBe(204000);
    expect(budget.measured.on).toBe("2026-11-18");
  });

  it("never renders HTML from a headline (no dangerouslySetInnerHTML in its files)", () => {
    for (const f of [cardSrc, libSrc])
      expect(f).not.toMatch(/dangerouslySetInnerHTML|innerHTML/);
  });

  it("polls only while the tab is visible", () => {
    expect(cardSrc).toMatch(/\.\.\.poll\(2 \* MIN\)/);
  });
});
