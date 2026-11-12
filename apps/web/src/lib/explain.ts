// Plain-language "why" for one Tape setup: what the rule saw and, if nothing was bought, the reason.
// The technical codes stay available as a tooltip (`detail`).
import type { TapeItem } from "./api";

type Signal = Extract<TapeItem, { kind: "signal" }>;

const SAW: Record<string, string> = {
  "no-news dip": "fell sharply with no news",
  "5-min higher low": "stopped falling in the last 5 minutes",
  "sector-adjusted": "fell more than its sector",
  stock_in_play: "traded unusually heavily in the first 5 minutes",
  "5m_range_up": "closed its first 5 minutes higher",
  close_above_or_high: "broke above its first-5-minute high",
};

const GATE: Record<string, string> = {
  risk_per_trade: "the loss limit per trade left room for 0 shares",
  position_cap: "one share costs more than the most one stock may take of the pot",
  open_risk_headroom: "the open trades already use the risk allowed for the day",
  buying_power: "there was not enough cash left",
  invalid_stop: "the stop price was not below the buy price",
};

const STRAT_PLAIN: Record<string, string> = {
  dip_reversal: "Dip rule",
  spy_late_momentum: "SPY late-day rule",
  orb_stocks_in_play: "Opening-breakout rule",
  news_continuation: "News rule",
};

function veto(v: string): string {
  if (v === "company news in 24 h") return "the company had news in the last 24 hours, and this rule only buys drops without news";
  if (v === "extra rules unavailable") return "a safety check could not run, so it stayed out to be safe";
  if (v.startsWith("pump")) return "the price move looked like a possible pump, so it stayed out";
  if (v.startsWith("VIX above your limit")) return `market fear (VIX) was above your limit${v.slice("VIX above your limit".length)}`;
  if (v.startsWith("price below your minimum")) return `the price was below your minimum${v.slice("price below your minimum".length)}`;
  if (v.startsWith("spread above your limit")) return `the gap between buy and sell price was above your limit${v.slice("spread above your limit".length)}`;
  return v;
}

export interface Explained { saw: string; outcome: string; detail: string }

export function explainSignal(it: Signal, opts: { taken: boolean; roomWhy?: string | null }): Explained {
  const rule = STRAT_PLAIN[it.strategy] ?? it.strategy;
  const seen = it.reasons.map((r) => SAW[r] ?? r.replace(/_/g, " "));
  const saw = seen.length ? `${rule}: the stock ${seen.join(", ")}.` : `${rule}.`;
  let outcome: string;
  if (it.mode === "shadow") outcome = "Test only: this rule is being measured, never traded. Nothing was bought.";
  else if (opts.taken) outcome = `Bought ${it.qty ?? 0} shares.`;
  else if (it.veto) outcome = `Not bought: ${veto(it.veto)}.`;
  else if (opts.roomWhy) outcome = `Not bought: the agent room said no (${opts.roomWhy}).`;
  else if (it.approved === false) outcome = `Not bought: a risk check failed${it.binding && GATE[it.binding] ? ` (${GATE[it.binding]})` : ""}.`;
  else if ((it.qty ?? 0) === 0 && it.approved) outcome = `Not bought: ${GATE[it.binding ?? ""] ?? "the size came out at 0 shares"}.`;
  else outcome = "Not bought.";
  const detail = [it.reasons.join(" · "), it.veto && `veto: ${it.veto}`, it.binding && `size limit: ${it.binding}`]
    .filter(Boolean).join(" — ");
  return { saw, outcome, detail };
}
