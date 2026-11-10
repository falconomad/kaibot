import type { Daily, Level, Trade } from "./api";

// Mirrors config/profiles.yaml. The engine enforces these; the dashboard only displays them.
export const LEVELS: Record<Level, { name: string; risk: number; stop: number; pos: number; maxPositions: number;
  entries: number; runDd: number; strategies: string }> = {
  conservative: { name: "Steady", risk: 0.25, stop: 0.75, pos: 10, maxPositions: 3, entries: 10, runDd: 4,
    strategies: "Dip reversal only" },
  balanced: { name: "Balanced", risk: 0.5, stop: 1.5, pos: 15, maxPositions: 5, entries: 20, runDd: 8,
    strategies: "Dip reversal + SPY end-of-day momentum" },
  aggressive: { name: "Hunter", risk: 1.0, stop: 3.0, pos: 20, maxPositions: 8, entries: 30, runDd: 12,
    strategies: "Dip reversal + SPY end-of-day momentum" },
};

// Go-live bar (docs/decisions): fixed in advance, shown as progress, never relaxed in the UI.
export const BAR = { trades: 900, months: 3, t: 3, randomPct: 95 };

export interface Curve { session: string; pot: number; spy: number }

export function curve(start: number, daily: Daily[]): Curve[] {
  let pot = start, spy = start;
  return daily.map((d) => {
    pot += d.pnl;
    spy *= 1 + (d.spy_ret ?? 0);
    return { session: d.session, pot, spy };
  });
}

export function stats(start: number, daily: Daily[], trades: Trade[]) {
  const pnl = trades.map((t) => t.pnl_usd ?? 0);
  const n = pnl.length;
  const mean = n ? pnl.reduce((a, b) => a + b, 0) / n : 0;
  const sd = n > 1 ? Math.sqrt(pnl.reduce((a, b) => a + (b - mean) ** 2, 0) / (n - 1)) : 0;
  const wins = pnl.filter((p) => p > 0);
  const losses = pnl.filter((p) => p < 0);
  const gw = wins.reduce((a, b) => a + b, 0), gl = -losses.reduce((a, b) => a + b, 0);
  const c = curve(start, daily);
  let peak = start, maxDd = 0;
  for (const p of c) { peak = Math.max(peak, p.pot); maxDd = Math.max(maxDd, (peak - p.pot) / peak); }
  const tradingDays = daily.filter((d) => d.trades > 0);
  const end = c.length ? c[c.length - 1] : { pot: start, spy: start };
  return {
    trades: n,
    t: sd > 0 ? (mean / sd) * Math.sqrt(n) : 0,
    hitRate: n ? wins.length / n : 0,
    profitFactor: gl > 0 ? gw / gl : null,
    maxDdPct: maxDd * 100,
    months: daily.length / 21,
    netUsd: end.pot - start,
    netPct: ((end.pot - start) / start) * 100,
    spyPct: ((end.spy - start) / start) * 100,
    losingDayShare: tradingDays.length ? tradingDays.filter((d) => d.pnl < 0).length / tradingDays.length : 0,
  };
}
