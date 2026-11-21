// #378 (M8 S104): the market notices on Home, "New on the market" and "Big day" (docs/design/m8_market_notices.md).
import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import css from "./components/notices.css?raw";
import tokens from "./tokens.css?raw";
import live from "./pages/Live.tsx?raw";
import MarketNotices, { NoticeRows, NoticeSheetBody } from "./components/Notices";
import { type Notices, homeNotices, noticeLine, parseNotices, untilText } from "./lib/notices";
import { hasAlert, hasStatus, render, text, wrongName } from "./pages/testkit";

vi.mock("./lib/api", () => import("./pages/testkit-api"));

type Rgb = [number, number, number];
const lum = (c: Rgb) => { const [r, g, b] = c.map((v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
const ratio = (a: Rgb, b: Rgb) => { const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05); };

const BERLIN = "Europe/Berlin";
const ANT = { kind: "new_listing", flag: "ipo", tier: "major", session: "2026-11-19", symbol: "ANT", title: "Anthropic (ANT) starts trading",
  starts: null, first_eligible: "2027-11-19", source: "publisher",
  detail: { name: "Anthropic", rules: [
    { rule: "orb_stocks_in_play", state: "shadow", first_eligible: "2026-12-10", needs_sp500: false },
    { rule: "owner_tickers", state: "the owner's own ticker list", first_eligible: "2027-02-17", needs_sp500: false },
    { rule: "trend_momentum", state: "on paper", first_eligible: "2027-11-19", needs_sp500: true }] } };
const ANT_BIG = { kind: "big_day", flag: "mega_listing", tier: "major", session: "2026-11-19", symbol: "ANT", title: "A very large company starts trading",
  starts: null, first_eligible: null, source: "engine", detail: { what: ["ANT"] } };
const EXBI = { ...ANT, tier: "notice", session: "2026-11-24", symbol: "EXBI", title: "Example Bio (EXBI) starts trading", detail: { name: "Example Bio" } };
const FED = { kind: "big_day", flag: "fed_day", tier: "major", session: "2026-12-09", symbol: null, title: "The Fed announces its interest-rate decision",
  starts: "2026-12-09T19:00:00+00:00", first_eligible: null, source: "publisher", detail: {} };
const CPI = { ...FED, flag: "cpi_day", tier: "notice", session: "2026-12-10", starts: "2026-12-10T13:30:00+00:00" };
const IDX = { ...FED, flag: "index_reshuffle", tier: "notice", session: "2026-12-09", starts: "2026-12-09T21:00:00+00:00", detail: { index: ["russell_recon"] } };

/** kb_market_notices as migration 0035 answers for 19 Nov 2026 (tests/test_market_notices_sql.py seeds the same). */
const NOV19 = parseNotices({ today: "2026-11-19", from: "2026-11-13", to: "2026-11-27",
  sessions: ["2026-11-19", "2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27"], notices: [ANT, ANT_BIG, EXBI] })!;
const day = (today: string, sessions: string[], notices: unknown[], from = sessions[0]): Notices =>
  parseNotices({ today, from, to: sessions.at(-1), sessions, notices })!;
const DEC = ["2026-12-08", "2026-12-09", "2026-12-10", "2026-12-11", "2026-12-14", "2026-12-15", "2026-12-16"];

describe("19 Nov 2026 replayed", () => {
  it("Home shows the ANT notice, one row for the listing and its big day together", () => {
    const rows = homeNotices(NOV19);
    expect(rows.map((r) => [r.kind, r.symbol])).toEqual([["new_listing", "ANT"]]);
    expect(rows[0].alsoBigDay).toBe(true);
    const html = renderToStaticMarkup(<NoticeRows data={NOV19} tz={BERLIN} />);
    expect(html).toContain('aria-label="New on the market: Anthropic (ANT) started trading today"');
    expect(text(html)).toContain("Anthropic (ANT) started trading today");
  });
  it("the sheet says what kaibo did and when its rules could first look, and nothing to act on", () => {
    const html = renderToStaticMarkup(<NoticeSheetBody n={homeNotices(NOV19)[0]} data={NOV19} tz={BERLIN} />);
    const t = text(html);
    expect(t).toContain("kaibo does not buy brand-new listings: they have no price history to judge, and on average they have done worse than the market in their first years.");
    expect(t).toContain("Its rules could first look at ANT in about 12 months.");
    expect(t).toContain("Trend momentum, on paper Nov 19, 2027 at the earliest");
    expect(t).toContain("Your own ticker list Feb 17, 2027");
    expect(t).toContain("kaibo is also recording how the rest of the market moves today.");
    expect(t).toContain("Tue, Nov 24 Example Bio (EXBI) is expected to start trading");
    expect(t).toContain("A notice only informs. kaibo changes nothing because of it.");
  });
});

describe("no notice can be clicked into an order, a setting or a promise of a trade", () => {
  const all = day("2026-12-09", DEC, [FED, CPI, IDX, { ...ANT, session: "2026-12-09" }]);
  const pages = [renderToStaticMarkup(<NoticeRows data={all} tz={BERLIN} />),
    ...homeNotices(all).map((n) => renderToStaticMarkup(<NoticeSheetBody n={n} data={all} tz={BERLIN} />))];
  it("no link, no form, no button inside a sheet; the rows only open their sheet", () => {
    for (const h of pages) {
      expect(h).not.toMatch(/<a\b|<form|<input|<select|href=/);
      expect([...h.matchAll(/<button\b[^>]*>/g)].every((m) => /aria-haspopup="dialog"/.test(m[0]))).toBe(true);
    }
    for (const h of pages.slice(1)) expect(h).not.toMatch(/<button/);
  });
  it("no price, no buying words but the one that says no, never a warning or a live region", () => {
    for (const h of pages) {
      const t = text(h);
      expect(t).not.toMatch(/\$|\bbuy (it|now)\b|\bshould\b|\border\b|\bopportunit|\bwarning\b|\balert\b|\brisk\b/i);
      expect(t.match(/\bbuy\b/gi)?.length ?? 0).toBe((t.match(/does not buy/g) ?? []).length);
      expect(hasAlert(h) || hasStatus(h)).toBe(false);
      expect(wrongName(h)).toEqual([]);
    }
  });
});

describe("which notices are on Home, and when they go", () => {
  it("a big day on its day, in the viewer's time; a major one also the session before", () => {
    expect(homeNotices(day("2026-12-09", DEC, [FED])).map((n) => noticeLine(n, day("2026-12-09", DEC, [FED]), { tz: BERLIN })))
      .toEqual(["Fed decision at 20:00 your time"]);
    expect(noticeLine(homeNotices(day("2026-12-09", DEC, [FED]))[0], day("2026-12-09", DEC, [FED]), { tz: "America/New_York" }))
      .toBe("Fed decision at 14:00 your time");
    const before = day("2026-12-08", DEC, [FED]);
    expect(homeNotices(before).map((n) => noticeLine(n, before, { tz: BERLIN }))).toEqual(["Tomorrow: Fed decision, 20:00"]);
    expect(homeNotices(day("2026-12-10", DEC.slice(2), [FED], "2026-12-04"))).toEqual([]);
  });
  it("a smaller big day only on its day", () => {
    expect(homeNotices(day("2026-12-09", DEC.slice(1), [CPI]))).toEqual([]);
    const d = day("2026-12-10", DEC.slice(2), [CPI]);
    expect(homeNotices(d).map((n) => noticeLine(n, d, { tz: BERLIN }))).toEqual(["Inflation report at 14:30 your time"]);
  });
  it("a major listing stays through its first week; an ordinary one is never a row", () => {
    const fri = day("2026-11-20", ["2026-11-20", "2026-11-23", "2026-11-24", "2026-11-25", "2026-11-27", "2026-11-30"], [ANT, EXBI], "2026-11-16");
    expect(homeNotices(fri).map((n) => noticeLine(n, fri))).toEqual(["Anthropic (ANT) started trading on Thu, Nov 19"]);
    const later = day("2026-11-27", ["2026-11-27", "2026-11-30"], [ANT], "2026-11-20");
    expect(homeNotices(later)).toEqual([]);  // the window starts after it: its fifth session has passed
  });
  it("two rows at most, then one row that opens the list", () => {
    const busy = day("2026-12-09", DEC, [FED, IDX, { ...CPI, session: "2026-12-09" }]);
    const html = renderToStaticMarkup(<NoticeRows data={busy} tz={BERLIN} />);
    expect(html.match(/class="nt-row"/g)).toHaveLength(2);
    expect(text(html)).toContain("1 more market notice");
  });
  it("the words for the other kinds, and the wait in weeks or months", () => {
    const d = day("2026-12-09", DEC, []);
    expect(noticeLine(parseNotices({ ...d, notices: [IDX] })!.notices[0], d)).toBe("Index change at the close (Russell)");
    const mega = { ...FED, flag: "megacap_report", detail: { megacaps: ["NVDA", "AAPL", "MSFT", "AMZN"] } };
    expect(noticeLine(parseNotices({ ...d, notices: [mega] })!.notices[0], d)).toBe("NVDA, AAPL, MSFT and 1 more report results");
    expect(untilText("2026-11-19", "2027-11-19")).toBe("in about 12 months");
    expect(untilText("2026-11-19", "2026-12-10")).toBe("in about 3 weeks");
  });
  it("a malformed notice is left out, not guessed", () => {
    const p = parseNotices({ today: "2026-11-19", sessions: [], notices: [{ ...ANT, kind: "buy_now" }, { ...ANT, session: "soon" }, ANT] })!;
    expect(p.notices).toHaveLength(1);
    expect(parseNotices(null)).toBeNull();
  });
});

describe("loading, empty, not applied and error", () => {
  it("nothing while loading, on an empty day or before migration 0035 (null); one quiet line on an error", () => {
    expect(text(render(<MarketNotices />))).toBe("");
    expect(text(render(<MarketNotices />, { seeds: [{ key: ["market-notices"], data: null }] }))).toBe("");
    expect(text(render(<MarketNotices />, { seeds: [{ key: ["market-notices"], data: day("2026-12-07", DEC, []) }] }))).toBe("");
    const err = render(<MarketNotices />, { seeds: [{ key: ["market-notices"], error: new Error("boom") }] });
    expect(text(err)).toBe("Market notices could not load. Try again");
    expect(hasAlert(err)).toBe(false);
  });
});

describe("calm on a phone, and the colour check", () => {
  const T = Object.fromEntries([...tokens.slice(0, tokens.indexOf(':root[data-theme="light"]')).matchAll(/--([a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})\b/g)]
    .map((m) => [m[1], [1, 3, 5].map((i) => parseInt(m[2].slice(i, i + 2), 16)) as Rgb]));
  it("text and icons meet AA on the row and the sheet (dark)", () => {
    expect(ratio(T.ink, T.surface)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(T.slate, T.surface)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(T.stale, T.surface)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(T["sea-ink"], T.paper)).toBeGreaterThanOrEqual(4.5);
    expect(ratio(T["sea-ink"], T.surface)).toBeGreaterThanOrEqual(4.5);
  });
  it("no colour of meaning: the stylesheet uses only neutral tokens (and the link colour for Try again)", () => {
    expect(css).not.toMatch(/var\(--(warn|loss|gain|down|up|amber|ai|exp|accent|sea(?!-ink))[a-z-]*\)/);
    expect(css.replace(/\/\*[\s\S]*?\*\//g, "")).not.toMatch(/#[0-9A-Fa-f]{3,6}\b|animation|@keyframes/);
  });
  it("a line that wraps on a narrow phone instead of being cut, and never pushes the page wider", () => {
    const rule = (sel: string) => new RegExp(`${sel.replace(/\./g, "\\.")} \\{([^}]*)\\}`).exec(css)?.[1] ?? "";
    expect(rule(".nt-text")).toMatch(/min-width: 0;.*white-space: normal;.*overflow-wrap: anywhere/);
    expect(css).not.toMatch(/text-overflow|line-clamp/);
    expect(rule(".nt-row")).toContain("min-width: 0");
    expect(rule(".nt-row")).toContain("min-height: 48px");
    expect(rule(".nt-list li span:last-child")).toContain("overflow-wrap: anywhere");
  });
  it("Home loads the notices on demand: not in the first load", () => {
    expect(live).toMatch(/lazy\(\(\) => import\("\.\.\/components\/Notices"\)\)/);
    expect(live).not.toMatch(/from "\.\.\/components\/Notices"|from "\.\.\/lib\/notices"/);
  });
});
