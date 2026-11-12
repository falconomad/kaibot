// S41 go/no-go rows. Split from derive.ts so the first load stays small.
import type { GateOverall, GateStrategy } from "./api";

// ---------- S41 go/no-go ----------
export type GateRowState = "pass" | "fail" | "unknown" | "provisional" | "pending";
const GATE_ROWS: { key: string; label: string; fmt: (v: number) => string; lim: (l: number) => string }[] = [
  { key: "trades", label: "Trades on frozen code", fmt: (v) => v.toLocaleString("en-US"), lim: (l) => `≥ ${l}` },
  { key: "months", label: "Months of forward paper", fmt: (v) => v.toFixed(1), lim: (l) => `≥ ${l}` },
  { key: "t_stat", label: "Expectancy after costs (t-stat)", fmt: (v) => v.toFixed(2), lim: (l) => `> ${l}` },
  { key: "deflated_sharpe", label: "Deflated Sharpe (probability)", fmt: (v) => v.toFixed(2), lim: (l) => `> ${l}` },
  { key: "random_pct", label: "Beats random entries (percentile)", fmt: (v) => `${v.toFixed(1)}%`, lim: (l) => `≥ ${l}%` },
  { key: "sharpe_vs_spy", label: "Sharpe vs SPY (annualised)", fmt: (v) => v.toFixed(2), lim: (l) => `> SPY ${l.toFixed(2)}` },
  { key: "drawdown", label: "Max drawdown", fmt: (v) => `${(v * 100).toFixed(1)}%`, lim: (l) => `≤ ${(l * 100).toFixed(0)}%` },
];

/** Rows for one strategy. Below the bar's sample size a met row is never shown as a pass ("can only kill"). */
export function gateRows(st: GateStrategy): { key: string; label: string; value: string; limit: string; state: GateRowState }[] {
  const provisional = st.status === "provisional" || st.status === "kill";
  return GATE_ROWS.map((r) => {
    const c = st.criteria[r.key];
    const value = c?.value == null ? "—" : r.fmt(Number(c.value));
    const limit = c?.limit == null ? "—" : r.lim(Number(c.limit));
    let state: GateRowState = c?.met == null ? "unknown" : c.met ? "pass" : "fail";
    if (provisional && state === "pass" && r.key !== "drawdown") state = "provisional";
    if (st.status === "provisional" && state === "fail") state = "pending"; // not enough data yet: slate, not red
    return { key: r.key, label: r.label, value, limit, state };
  });
}

export function gateStatusText(s: GateStrategy["status"]): { label: string; tone: "pass" | "fail" | "warn" | "muted" } {
  switch (s) {
    case "pass": return { label: "Cleared the bar", tone: "pass" };
    case "kill": return { label: "Kill", tone: "fail" };
    case "fail": return { label: "Not yet", tone: "warn" };
    default: return { label: "Provisional: can only kill", tone: "muted" };
  }
}

export function gateOverallText(o: GateOverall | undefined): string {
  if (o === "discuss") return "A strategy cleared the pre-registered bar. That opens a written go-live discussion. Nothing switches automatically.";
  if (o === "kill") return "Every live strategy met a kill criterion. Stop or redesign before collecting more data.";
  return "Keep collecting. No strategy has cleared the bar yet.";
}
