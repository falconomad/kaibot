// #604 on the page: the "Next" line under the pot, the late daily prices notice (#645) on Home and Engine, and the one
// freshness cue on the engine chip, the headlines and the Engine headline. The rules themselves are unit-tested in
// lib/nextaction.test.ts and lib/freshness.test.ts.
import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { Health as HealthResp, Heartbeat, LlmBudget, Overview } from "./lib/api";
import { parseBook, parseSeries } from "./lib/book";
import { Live } from "./pages/Live";
import { Health } from "./pages/Health";
import { HeadlineCard } from "./components/Headlines";
import { FreshNote } from "./components/Fresh";
import { dataLateText } from "./lib/healthwords";
import { ctxFor, emptyOverview, paperRun, render, text } from "./pages/testkit";
import liveSrc from "./pages/Live.tsx?raw";
import css from "./app.css?raw";

vi.mock("./lib/api", () => import("./pages/testkit-api"));
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });
afterEach(() => { vi.useRealTimers(); });

const SESSION = "2026-11-24";
const BOOK = parseBook({ run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-24T21:05:00Z", at_close: true, cash: 91784.86, invested: 3212.4, equity: 94997.26, equity_reported: 94997.26,
    start_equity: 95000, prev_close_equity: 95000, open_equity: null, dividends_to_date: 0 },
  positions: [{ rule: "trend_momentum", symbol: "XOM", qty: 20, avg_cost: 160.59, mark: 160.62, last: 160.62, opened_session: SESSION, dividends: 0,
    stop: null, stop_state: "none_by_design", exit: "target_list" }],
  exposure: { gross: 0.03, overnight: 0.03, cap: null, by_rule: [], market_filter: [] },
  reconciled: { ok: true, ts: "2026-11-24T21:06:00Z", positions: {}, cash_diff: 0 } }, 7);
const SERIES = parseSeries([{ session: "2026-11-23", close_equity: 95000, marked: true }, { session: SESSION, close_equity: 94997.26, marked: true }]);
const run = paperRun({ id: 7, amount_usd: 95000 });
const EVENING = "2026-11-25T22:00:00Z"; // Wed 17:00 New York, after the close (23:00 in Berlin)
const CAL = { today: "2026-11-25", from: "2026-11-19", to: "2026-12-03", sessions: ["2026-11-25", "2026-11-27", "2026-11-30", "2026-12-01", "2026-12-02", "2026-12-03"], notices: [] };

const beat = (ts: string, md: Record<string, unknown>, extra: Record<string, unknown> = {}): Heartbeat =>
  ({ process: "engine", ts, detail: { state: "market_closed", lease: "ok", next_open: "2026-11-27T09:30:00-05:00", multi_day: { book: "open", ...md }, ...extra } as Heartbeat["detail"] });
const overview = (hb: Heartbeat): Overview => ({ ...emptyOverview(run),
  daily: [{ session: "2026-11-23", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: 0, trades: 0 }, { session: SESSION, pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: 0, trades: 0 }],
  heartbeat: [hb], today: { trades: 0, pnl: 0 } });
const home = (hb: Heartbeat, extra: { key: unknown[]; data: unknown }[] = []) => render(<Live />, { ctx: ctxFor(run, { overview: overview(hb) }),
  seeds: [{ key: ["trades-all", 7], data: { total: 0, rows: [] } }, { key: ["book", 7], data: BOOK }, { key: ["equity_series", 7], data: SERIES }, ...extra] });
const met = { trend_momentum: { last_decision: SESSION, standing: { state: "met", decided: "2026-11-24", fill_session: "2026-11-25", sent_on: "2026-11-25", short: [] } } };

describe("Home: one 'Next' line under the pot", () => {
  it("after the close with stocks held: the stops at the next open, from the market calendar (Thanksgiving is closed)", () => {
    vi.useFakeTimers({ now: new Date(EVENING), toFake: ["Date"] });
    const html = home(beat("2026-11-25T21:59:50Z", { rules: met }), [{ key: ["market-notices"], data: CAL }]);
    expect(html).toContain('<p class="muted home-next">Next: stops checked at the open, Fri 27 Nov 09:30 ET</p>');
    // under the pot, in the hero
    expect(html.indexOf("home-next")).toBeGreaterThan(html.indexOf('class="hero num"'));
    expect(html.indexOf("home-next")).toBeLessThan(html.indexOf("home-chart"));
  });
  it("a decision due: when its orders go out; without the calendar, from the heartbeat's next open", () => {
    vi.useFakeTimers({ now: new Date(EVENING), toFake: ["Date"] });
    const due = { trend_momentum: { last_decision: SESSION, standing: { state: "due", fill_session: "2026-11-27" } } };
    expect(text(home(beat("2026-11-25T21:59:50Z", { rules: due })))).toContain("Next: orders go out on Fri 27 Nov at 15:50 ET");
  });
  it("no multi-day block: no line", () => {
    vi.useFakeTimers({ now: new Date(EVENING), toFake: ["Date"] });
    const hb: Heartbeat = { process: "engine", ts: "2026-11-25T21:59:50Z", detail: { state: "market_closed" } };
    expect(home(hb)).not.toContain("home-next");
  });
  it("Home reads the calendar from the notices' cache and never asks for it", () => {
    expect(liveSrc).toMatch(/queryKey: \["market-notices"\], queryFn: skipToken/);
  });
});

describe("late daily prices (#645)", () => {
  const late = { rules: { trend_momentum: { last_decision: "2026-11-23", note: "no decision today: daily store not refreshed" } },
    data_late: [{ session: SESSION, deadline: "2026-11-25T03:30:00-05:00", rules: ["trend_momentum"], why: "daily store not refreshed" }] };
  it("Home: one plain amber row (not the second 'no decision' row of the same cause), and the Next line waits for them", () => {
    vi.useFakeTimers({ now: new Date("2026-11-25T14:00:00Z"), toFake: ["Date"] });
    const html = home(beat("2026-11-25T13:59:50Z", late));
    const t = text(html);
    expect(html).toMatch(/class="status-row warn"[^>]*><span class="status-text">The prices for Tue 24 Nov haven&#x27;t arrived; kaibo is waiting<\/span>/);
    expect(t).not.toContain("No decision today: Trend momentum skipped its decision");
    expect(t).toContain("Next: decision once the prices for Tue 24 Nov arrive");
  });
  it("Engine: the same row, and the event in plain words in the operations log", () => {
    const now = new Date().toISOString();
    const hb = beat(now, late);
    const health: HealthResp = { now, heartbeat: [hb], halts: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] },
      events: [{ type: "multi_day_data_late", ts: now, run_id: null, payload: { session: SESSION, strategy: "trend_momentum", deadline: "2026-11-25T03:30:00-05:00" } }] } as HealthResp;
    const budget: LlmBudget = { day: "2026-11-25", day_usd: 0, month_usd: 0, state: "ok", alert: false,
      caps: { daily_soft: 1, monthly_alert: 15, monthly_hard: 20 }, calls_today: 0, open_reserved_usd: 0, by_purpose: {} };
    const t = text(render(<Health />, { ctx: ctxFor(run, { overview: overview(hb) }), seeds: [{ key: ["health"], data: health }, { key: ["llm_budget"], data: budget }] }));
    expect(t).toContain("The prices for Tue 24 Nov haven't arrived; kaibo is waiting");
    expect(t).toContain("The daily prices for Tue 24 Nov missed their deadline: no multi-day decision until they arrive");
  });
  it("the event alone, while the engine is not live, until the rule decides on that session", () => {
    vi.useFakeTimers({ now: new Date("2026-11-25T08:00:00Z"), toFake: ["Date"] }); // 03:00 New York: the engine is asleep
    const ev = { type: "multi_day_data_late", ts: "2026-11-25T08:31:00Z", run_id: null, payload: { session: SESSION, strategy: "trend_momentum" } };
    const asleep = beat("2026-11-25T03:20:00Z", { rules: { trend_momentum: { last_decision: "2026-11-23" } } });
    expect(text(home(asleep, [{ key: ["health"], data: { events: [ev] } }]))).toContain("The prices for Tue 24 Nov haven't arrived");
    const decided = beat("2026-11-25T03:20:00Z", { rules: { trend_momentum: { last_decision: SESSION } } });
    expect(text(home(decided, [{ key: ["health"], data: { events: [ev] } }]))).not.toContain("haven't arrived");
  });
  it("other events keep their own words", () => {
    expect(dataLateText("multi_day_waiting", { session: SESSION })).toBeNull();
    expect(dataLateText("multi_day_data_late", {})).toBe("The daily prices missed their deadline: no multi-day decision until they arrive");
  });
});

describe("one freshness cue", () => {
  it("nothing while live; quiet words when a few minutes old; amber with the age when stale", () => {
    expect(renderToStaticMarkup(<FreshNote f={{ state: "live", text: null }} what="Pot" />)).toBe("");
    expect(renderToStaticMarkup(<FreshNote f={null} />)).toBe("");
    expect(renderToStaticMarkup(<FreshNote f={{ state: "recent", text: "A few minutes old" }} what="Pot" />))
      .toBe('<p class="fresh recent" role="status"><span class="sr-only">Pot: </span>A few minutes old</p>');
    expect(renderToStaticMarkup(<FreshNote f={{ state: "stale", text: "Stale · 25 min old" }} inline />)).toBe('<span class="fresh stale" role="status">Stale · 25 min old</span>');
    expect(css).toMatch(/\.fresh\.stale \{ color: var\(--warn-text\); \}/);
  });
  it("the headlines say when the news feed is late, in the same words", () => {
    const items = [{ id: 1, symbols: ["XOM"], headline: "Exxon news", source: "alpaca_benzinga" as const, licence: "owner_only" as const, url: null, tags: [], focus: true,
      created_at: "2026-11-25T15:00:00Z" }];
    const NOW = Date.parse("2026-11-25T18:00:00Z");
    expect(renderToStaticMarkup(<HeadlineCard items={items} now={NOW} />)).not.toContain("fresh");
    expect(renderToStaticMarkup(<HeadlineCard items={items} now={NOW} fresh={{ state: "stale", text: "Stale · 3 h old" }} />))
      .toContain('<p class="fresh stale" role="status"><span class="sr-only">Headlines: </span>Stale · 3 h old</p>');
  });
  it("the Engine headline: no 'Checked in N s ago'; the cue only when the heartbeat is late", () => {
    const now = new Date().toISOString();
    const hb: Heartbeat = { process: "engine", ts: now, detail: { state: "trading", lease: "ok" } as Heartbeat["detail"] };
    const health = { now, heartbeat: [hb], halts: [], events: [], pending_commands: [], deadman: { armed: true, jobs: [], runs: [] } } as unknown as HealthResp;
    const budget = { day: "2026-11-25", day_usd: 0, month_usd: 0, state: "ok", alert: false, caps: { daily_soft: 1, monthly_alert: 15, monthly_hard: 20 },
      calls_today: 0, open_reserved_usd: 0, by_purpose: {} } as LlmBudget;
    const t = text(render(<Health />, { ctx: ctxFor(run, { overview: overview(hb) }), seeds: [{ key: ["health"], data: health }, { key: ["llm_budget"], data: budget }] }));
    expect(t).toMatch(/^Engine Running Trading:/);
    expect(t).not.toMatch(/Checked in/);
  });
});
