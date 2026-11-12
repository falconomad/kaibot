// CONTRACT DRAFT, replace when #117 defines kb_* RPCs.
//
// Types and a mock for the multi-day book that the Ledger (#117, S77) will provide: positions by rule, the pot marked
// to market, and exposure. No screen uses this yet: the multi-day screens (docs/design/m7_multiday_dashboard.md,
// #130) wait for the stakeholder's design approval, and the real shapes are Backend-quant's to define in #117.
// Field names follow decision 15 section 3 (`Ledger`: positions per (strategy, symbol), cash, daily mark-to-market
// equity, dividends and splits) and the data check in section 4 of the design doc. When #117 lands: replace these
// types with the RPC's, delete the mock, and keep `bookTotals` only if it still matches the server's numbers.
//
// Money is in US dollars, shares are whole numbers (v3.0), times are ISO 8601, sessions are YYYY-MM-DD (New York).

/** One holding of one rule. Two rules can hold the same stock: the key is (rule, symbol). */
export interface MultidayPosition {
  rule: string;            // strategy code, e.g. "trend_momentum" (plain name via explain.ruleName)
  symbol: string;
  qty: number;             // whole shares
  avg_cost: number;        // per share, after fees
  last: number | null;     // latest price; null when no price yet
  last_ts: string | null;  // time of `last`
  opened_session: string;  // first buy
  /** Protective stop resting at the broker (decision 15: a far GTC stop as insurance). null = missing. */
  stop: { price: number; state: "resting" | "missing" | "re-arming" } | null;
}

/** The pot at today's prices. Fallback when no intraday value is kept: the last close, with `at_close: true`. */
export interface MarkToMarket {
  ts: string;
  at_close: boolean;
  cash: number;
  invested: number;        // sum of qty x last over all positions
  equity: number;          // cash + invested
  start_equity: number;    // the run's starting pot
  prev_close_equity: number | null;  // yesterday's mark-to-market equity (start of today's day stop)
  open_equity: number | null;        // value at today's open, for "overnight" vs "since the open"; null if not kept
  dividends_to_date: number;
}

/** Exposure against the limits in force (PortfolioBuilder). Fractions of equity, 0..1; never above 1 (no leverage). */
export interface Exposure {
  gross: number;
  overnight: number;
  cap: number;             // the exposure cap of the level (open until the stakeholder confirms the level limits)
  by_rule: { rule: string; value: number; share: number }[];
  /** Index trend filter per rule: SPY's distance from its 200-day average, in percent. */
  market_filter: { rule: string; above: boolean; distance_pct: number }[];
}

export interface MultidayBook {
  run_id: number;
  session: string;
  mtm: MarkToMarket;
  positions: MultidayPosition[];
  exposure: Exposure;
  /** The last comparison of cash and positions with the broker. */
  reconciled: { ok: boolean; ts: string } | null;
}

/** Totals a screen would show, computed from the positions (so a test can check the server's numbers agree). */
export function bookTotals(b: MultidayBook) {
  const value = (p: MultidayPosition) => p.qty * (p.last ?? p.avg_cost);
  const invested = b.positions.reduce((a, p) => a + value(p), 0);
  const byRule = new Map<string, number>();
  for (const p of b.positions) byRule.set(p.rule, (byRule.get(p.rule) ?? 0) + value(p));
  const equity = b.mtm.cash + invested;
  return {
    invested, equity,
    gross: equity > 0 ? invested / equity : 0,
    byRule: [...byRule].map(([rule, v]) => ({ rule, value: v, share: equity > 0 ? v / equity : 0 })),
    unrealised: b.positions.reduce((a, p) => a + (p.last === null ? 0 : (p.last - p.avg_cost) * p.qty), 0),
    stopsInPlace: b.positions.filter((p) => p.stop?.state === "resting").length,
  };
}

/** Mock book for tests and a future Storybook-style preview. Invented numbers, set in the future on purpose
 *  (like the design boards' sample) so nobody reads it as a track record. */
export const MOCK_BOOK: MultidayBook = (() => {
  const positions: MultidayPosition[] = [
    { rule: "trend_momentum", symbol: "AAPL", qty: 4, avg_cost: 231.4, last: 238.1, last_ts: "2027-03-11T21:00:00Z", opened_session: "2027-01-19",
      stop: { price: 196.7, state: "resting" } },
    { rule: "trend_momentum", symbol: "MSFT", qty: 2, avg_cost: 448.2, last: 441.9, last_ts: "2027-03-11T21:00:00Z", opened_session: "2027-02-02",
      stop: { price: 381.0, state: "resting" } },
    { rule: "mom_12_1_band", symbol: "AAPL", qty: 3, avg_cost: 228.9, last: 238.1, last_ts: "2027-03-11T21:00:00Z", opened_session: "2027-01-04",
      stop: { price: 194.6, state: "resting" } },
    { rule: "mom_12_1_band", symbol: "COST", qty: 1, avg_cost: 905.0, last: 921.5, last_ts: "2027-03-11T21:00:00Z", opened_session: "2027-02-01",
      stop: null },
  ];
  const invested = positions.reduce((a, p) => a + p.qty * (p.last ?? p.avg_cost), 0);
  const cash = 1450;
  const equity = cash + invested;
  const byRule = ["trend_momentum", "mom_12_1_band"].map((rule) => {
    const value = positions.filter((p) => p.rule === rule).reduce((a, p) => a + p.qty * (p.last ?? p.avg_cost), 0);
    return { rule, value, share: value / equity };
  });
  return {
    run_id: 0, session: "2027-03-11",
    mtm: { ts: "2027-03-11T21:00:00Z", at_close: true, cash, invested, equity, start_equity: 5000, prev_close_equity: equity - 12.4,
      open_equity: null, dividends_to_date: 3.1 },
    positions,
    exposure: { gross: invested / equity, overnight: invested / equity, cap: 1, by_rule: byRule,
      market_filter: [{ rule: "trend_momentum", above: true, distance_pct: 4.2 }, { rule: "mom_12_1_band", above: true, distance_pct: 4.2 }] },
    reconciled: { ok: true, ts: "2027-03-11T21:05:00Z" },
  };
})();
