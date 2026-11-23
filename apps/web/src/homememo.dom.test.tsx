// @vitest-environment jsdom
// A streamed price redraws Home about once a second; the parts that do not depend on prices must not draw again
// (frontend performance pass, 23 Nov 2026, #611): HomeStatus (the rows above the pot) and the limit meters.
// - Home is mounted on an open market, a few relay messages move the pot, and the render functions of HomeStatus and
//   Meter are counted: none of them runs again. The count wraps the component's own function and keeps its memo, so
//   removing `memo` from either (or HomeStatus' comparator letting the engine chip's `sub` through) fails here.
// - HomeStatus still draws again when what it reads of the engine chip changes (`asleep`, `note`), and only then.
// Home is mounted in jsdom with api.marks and api.prices replaced by recorders (no network, no keys).
import { act, memo, useState, type ComponentProps } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { mount, unmount } from "./a11y/dom";
import type { Overview } from "./lib/api";
import { parseBook, parseSeries } from "./lib/book";
import type { Marks } from "./lib/marks";
import { HomeStatus } from "./components/Home";
import { Live } from "./pages/Live";
import { ctxFor, emptyOverview, paperRun } from "./pages/testkit";

const renders = vi.hoisted(() => {
  const n = { HomeStatus: 0, Meter: 0 };
  /** The component with its render function counted; a memo stays a memo (same compare), a plain function stays plain. */
  const counted = (c: unknown, name: keyof typeof n): unknown => {
    const bump = (inner: (p: unknown) => unknown) => function Counted(p: unknown) { n[name]++; return inner(p); };
    const m = c as { $$typeof?: symbol; type: (p: unknown) => unknown };
    if (m && typeof m === "object" && m.$$typeof === Symbol.for("react.memo")) return { ...m, type: bump(m.type) };
    return bump(c as (p: unknown) => unknown);
  };
  return { n, counted };
});
vi.mock("./components/Home", async (orig) => {
  const m = await orig<typeof import("./components/Home")>();
  return { ...m, HomeStatus: renders.counted(m.HomeStatus, "HomeStatus") };
});
vi.mock("./components/Shell", async (orig) => {
  const m = await orig<typeof import("./components/Shell")>();
  return { ...m, Meter: renders.counted(m.Meter, "Meter") };
});

const asked = vi.hoisted(() => ({ marks: vi.fn<() => Promise<unknown>>(), prices: vi.fn<(on: (s: unknown) => void) => () => void>() }));
vi.mock("./lib/api", async () => {
  const kit = await import("./pages/testkit-api");
  const never = () => new Promise<never>(() => {});
  return { ...kit, api: new Proxy({}, { get: (_t, k) => (k === "marks" ? asked.marks : k === "prices" ? asked.prices : never) }) };
});
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const SESSION = "2026-11-18";
const BOOK = parseBook({ run_id: 7, session: SESSION,
  mtm: { ts: "2026-11-18T21:05:00Z", at_close: true, cash: 91784.86, invested: 3212.4, equity: 94997.26, equity_reported: 94997.26,
    start_equity: 95000, prev_close_equity: 95000, open_equity: null, dividends_to_date: 0 },
  positions: [{ rule: "trend_momentum", symbol: "XOM", qty: 20, avg_cost: 160.59, mark: 160.62, last: 160.62, opened_session: SESSION, dividends: 0,
    stop: null, stop_state: "none_by_design", exit: "target_list" }],
  exposure: { gross: 0.03, overnight: 0.03, cap: null, by_rule: [], market_filter: [] },
  reconciled: { ok: true, ts: "2026-11-18T21:06:00Z", positions: {}, cash_diff: 0 } }, 7);
const SERIES = parseSeries([{ session: "2026-11-17", close_equity: 95000, marked: true }, { session: SESSION, close_equity: 94997.26, marked: true }]);
const run = paperRun({ id: 7, amount_usd: 95000 });
const overview: Overview = { ...emptyOverview(run),
  daily: [{ session: "2026-11-17", pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.012294, trades: 0 },
    { session: SESSION, pnl: 0, pnl_venue: 0, pnl_shadow: 0, spy_ret: -0.009749, trades: 0 }],
  heartbeat: [{ process: "engine", ts: "2026-11-19T17:39:55Z", detail: { state: "trading", next_open: "2026-11-20T09:30:00-05:00" } }],
  ledger: { ts: "2026-11-19T17:39:21Z", cash: 94960, positions: {}, equity_venue: 94960, equity_shadow: 94960 }, today: { trades: 0, pnl: 0 } };
const seeds = [{ key: ["trades-all", 7], data: { total: 0, rows: [] } }, { key: ["book", 7], data: BOOK }, { key: ["equity_series", 7], data: SERIES }];
const OPEN = "2026-11-19T17:40:00Z"; // Thursday 12:40 in New York
const meters = { dayPct: -0.4, dd: 1.2, replay: false };

const pass = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });
const page = () => (document.body.textContent ?? "").replace(/\s+/g, " ");

beforeEach(() => {
  renders.n.HomeStatus = 0; renders.n.Meter = 0;
  asked.marks.mockReset(); asked.prices.mockReset();
  asked.marks.mockImplementation(async (): Promise<Marks> => ({ at: Date.now() - 2_000, by: { XOM: { qty: 20, price: 158.68, prevClose: 160.58 } } }));
  vi.useFakeTimers({ now: new Date(OPEN), toFake: ["Date", "setTimeout", "clearTimeout", "setInterval", "clearInterval"] });
});
afterEach(async () => { await unmount(); vi.useRealTimers(); });

describe("a streamed price redraws the pot, not Home's status rows or limit meters", () => {
  it("five relay messages move the pot; HomeStatus and the meters do not render again", async () => {
    const holders = new Set<(s: unknown) => void>();
    asked.prices.mockImplementation((on) => { holders.add(on); return () => { holders.delete(on); }; });
    await mount(<Live />, { ctx: ctxFor(run, { overview, meters }), seeds });
    await pass(50);
    expect(holders.size).toBe(1);
    expect(page()).toContain("Day vs stop");                   // both meter pairs (desk and phone) are on the page
    expect(renders.n.Meter).toBeGreaterThan(0);
    expect(renders.n.HomeStatus).toBeGreaterThan(0);
    const before = { ...renders.n };
    for (const [i, price] of [159.18, 159.68, 160.18, 160.68, 161.18].entries()) {
      await act(async () => { for (const on of holders) on({ at: Date.now(), asOf: new Date().toISOString(), prices: { XOM: price } }); });
      await pass(1_000);
      expect(page(), `message ${i + 1}`).toContain(`Paper pot$${(94997.26 + 20 * (price - 160.62)).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`);
    }
    expect(renders.n).toEqual(before);
  });
});

describe("HomeStatus draws again when what it reads of the engine chip changes", () => {
  type Props = ComponentProps<typeof HomeStatus>;
  let setProps: (p: Props) => void = () => {};
  const Harness = memo(function Harness({ first }: { first: Props }) {
    const [p, set] = useState(first);
    setProps = set;
    return <HomeStatus {...p} />;
  });
  const asleep = (note: string, sub: string, sleeping = true) => ({ asleep: sleeping, note, sub, dot: "", text: "Engine asleep", live: false });
  const base: Props = { run, overview, risk: undefined, engineLive: false, book: undefined, engine: asleep("Starts at 15:30 CET", "starts 15:30") };
  const again = (p: Props) => act(async () => { setProps(p); });

  it("a new `sub` (or a new chip object) is no redraw; a new `note` or `asleep` is, and the row shows it", async () => {
    await mount(<Harness first={base} />);
    expect(page()).toContain("Starts at 15:30 CET");
    const n0 = renders.n.HomeStatus;
    await again({ ...base, engine: asleep("Starts at 15:30 CET", "starts 15:30 · 1 s") });
    expect(renders.n.HomeStatus).toBe(n0);
    await again({ ...base, engine: asleep("Starts on Monday at 15:30 CET", "starts Monday") });
    expect(renders.n.HomeStatus).toBe(n0 + 1);
    expect(page()).toContain("Starts on Monday at 15:30 CET");
    await again({ ...base, engine: asleep("Starts on Monday at 15:30 CET", "starts Monday", false) });
    expect(renders.n.HomeStatus).toBe(n0 + 2);
    expect(page()).not.toContain("The engine is asleep");
  });
});
