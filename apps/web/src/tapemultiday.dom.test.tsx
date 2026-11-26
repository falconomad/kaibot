// @vitest-environment jsdom
// Stakeholder, 26 Nov 2026: Activity's "Buys & sells" showed nothing. kb_tape read only kaibot.fill (the retired
// intraday path); migration 0050 adds one fill item (`source: "multi_day"`) per multi_day_fill engine event. These
// rows use the same row and sheet as an intraday fill; the fields the engine does not send (shadow price, fees, the
// trade's result) are left out rather than shown as dashes. Before 0050 the tape simply has no such items.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activate, all, mount, q, reducedMotionOn, unmount } from "./a11y/dom";
import type { TapeItem } from "./lib/api";
import { Tape } from "./pages/Tape";
import { ctxFor, emptyOverview, paperRun } from "./pages/testkit";

vi.mock("./lib/api", async () => {
  const kit = await import("./pages/testkit-api");
  const never = () => new Promise<never>(() => {});
  return { ...kit, api: new Proxy({}, { get: () => never }) };
});
vi.stubGlobal("localStorage", { getItem: () => null, setItem: () => {} });

const run = paperRun();
const md = (o: Partial<Extract<TapeItem, { kind: "fill" }>>): TapeItem => ({ kind: "fill", source: "multi_day", ts: "2026-11-25T20:51:00Z",
  side: "sell", qty: 20, venue_px: 158.862, shadow_px: null, fees: null, tag: null, symbol: "XOM", strategy: "trend_momentum",
  coid: "kb128381296941-trend_momentum-XOM-20261125-sell-1", trade_id: null, exit_reason: "below_average", pnl_usd: null, ...o });
const XOM = md({});
const AAPL = md({ ts: "2026-11-24T20:50:00Z", side: "buy", qty: 3, venue_px: 231.4, symbol: "AAPL", exit_reason: null, coid: "kb1-trend_momentum-AAPL-20261124-buy-1" });
const HAL = md({ ts: "2026-11-24T20:40:00Z", symbol: "HAL", qty: 4, venue_px: 50.25, shadow_px: 50.1, fees: 0.02, exit_reason: null });
const NVDA: TapeItem = { kind: "fill", ts: "2026-11-16T15:06:00Z", side: "sell", qty: 5, venue_px: 142.18, shadow_px: 142.1, fees: 0.02, tag: null,
  symbol: "NVDA", strategy: "trend_momentum", trade_id: 7, exit_reason: "target", pnl_usd: 38.2 };
const SKIP: TapeItem = { kind: "signal", ts: "2026-11-25T15:00:00Z", mode: "live", strategy: "dip_reversal", symbol: "AMD", entry: 150, stop: 145,
  target: 160, reasons: [], veto: "company news in 24 h", approved: null, qty: null, binding: null, trade_id: null, counterfactual: null };

const start = (items: TapeItem[]) => mount(<Tape />, { ctx: ctxFor(run, { overview: emptyOverview(run) }),
  seeds: [{ key: ["tape", run.id], data: items, infinite: true }] });
const norm = (el: Element | null | undefined) => (el?.textContent ?? "").replace(/\s+/g, " ").trim();
// a row as "head | line | value"
const rows = () => all<HTMLButtonElement>("ul.act-rows button.row")
  .map((b) => [norm(b.querySelector(".row-mid b")), norm(b.querySelector(".row-mid .muted")), norm(b.querySelector(".row-end"))].join(" | "));
const row = (sym: string) => all<HTMLButtonElement>("ul.act-rows button.row").find((b) => (b.textContent ?? "").includes(sym))!;
const chip = (label: string) => all<HTMLButtonElement>("button.chip-btn").find((b) => norm(b) === label)!;
const sheet = () => norm(q('[role="dialog"]'));

beforeEach(() => { reducedMotionOn(); });
afterEach(async () => { await unmount(); });

describe("Activity: the multi-day rule's buys and sells (0050)", () => {
  it("Buys & sells lists them with the same row as an intraday fill; the skipped signal is filtered out", async () => {
    await start([XOM, SKIP, AAPL, HAL, NVDA]);
    await activate(chip("Buys & sells"));
    const r = rows();
    expect(r).toHaveLength(4);
    expect(r[0]).toMatch(/^Sold XOM \| fell below its average · 20 at \$158\.86 · [^|]+ \| \$3,177\.24$/);
    expect(r[1]).toMatch(/^Bought AAPL \| 3 at \$231\.40 · Trend momentum · [^|]+ \| \$694\.20$/);
    expect(r[2]).toMatch(/^Sold HAL \| sold · 4 at \$50\.25 · [^|]+ \| \$201\.00$/); // no reason sent: "sold"
    expect(r[3]).toMatch(/^Sold NVDA \| target reached · 5 at \$142\.18 · [^|]+ \| \+\$38\.20$/); // intraday: unchanged
    expect(r.join(" ")).not.toMatch(/AMD|—|null|undefined|NaN/);
    // the sale's value is its proceeds, not a gain or loss: no tint
    expect(row("XOM").querySelector(".row-end")!.className).not.toMatch(/gain|loss/);
  });

  it("All shows them too, among the other rows", async () => {
    await start([XOM, SKIP, AAPL]);
    expect(rows().map((t) => t.split(" | ")[0])).toEqual(["Sold XOM", "Skipped AMD", "Bought AAPL"]);
  });

  it("the sheet of a multi-day sale hides what the engine does not send (shadow price, fees, result)", async () => {
    await start([XOM]);
    await activate(row("XOM"));
    const t = sheet();
    expect(t).toContain("Sold20 at $158.86");
    expect(t).toContain("Proceeds$3,177.24");
    expect(t).toContain("RuleTrend momentum");
    expect(t).toContain("Why it soldfell below its average");
    expect(t).toContain("The result of the holding is under Positions, Closed.");
    expect(t).not.toMatch(/Shadow price|Fees|Costs vs the shadow model|Result|Full trade details|—/);
    expect(q<HTMLAnchorElement>('[role="dialog"] a[href="/positions"]')).not.toBeNull();
  });

  it("a multi-day fill that does carry a shadow price and fees shows them; a buy has no 'Why it sold'", async () => {
    await start([HAL, AAPL]);
    await activate(row("HAL"));
    expect(sheet()).toContain("Shadow price$50.10");
    expect(sheet()).toContain("Fees$0.02");
    expect(sheet()).toContain("Costs vs the shadow model$0.62"); // |50.25 - 50.10| x 4 + 0.02
    await unmount();
    await start([AAPL]);
    await activate(row("AAPL"));
    expect(sheet()).toContain("Bought3 at $231.40");
    expect(sheet()).toContain("Cost$694.20");
    expect(sheet()).not.toMatch(/Why it sold|Shadow price|Fees|Positions, Closed/);
  });

  it("a payload without the keys at all (not even null) is hidden the same way", async () => {
    const { shadow_px: _s, fees: _f, pnl_usd: _p, ...bare } = XOM as Extract<TapeItem, { kind: "fill" }>;
    await start([bare as TapeItem]);
    expect(rows()[0]).toMatch(/^Sold XOM \| fell below its average · 20 at \$158\.86 · [^|]+ \| \$3,177\.24$/);
    await activate(row("XOM"));
    expect(sheet()).not.toMatch(/Shadow price|Fees|Costs vs the shadow model|Result|—|NaN/);
  });

  it("an intraday fill's sheet is as before (shadow price, fees, costs, result, trade link)", async () => {
    await start([NVDA]);
    await activate(row("NVDA"));
    const t = sheet();
    for (const s of ["Shadow price$142.10", "Fees$0.02", "Costs vs the shadow model$0.42", "Why it soldtarget reached", "Result+$38.20", "Full trade details"])
      expect(t).toContain(s);
    expect(t).not.toContain("Proceeds");
  });

  it("before 0050 (no multi-day items) Buys & sells is empty in words, as before", async () => {
    await start([SKIP]);
    await activate(chip("Buys & sells"));
    expect(rows()).toEqual([]);
    expect(norm(document.body)).toContain("Nothing of this kind in the loaded part.");
  });
});
