// Plain language for the engine's codes. Every screen shows these words; the code itself stays in a tooltip
// (`title`), so nothing is lost for debugging. Pure: no React, unit-tested in explain.test.ts.
//
// explainSignal: plain-language "why" for one Tape setup: what the rule saw and, if nothing was bought, the reason.
import type { TapeItem } from "./api";

/** Engine v3 test phase (decision 15). One sentence for Live and Report, with the plan. */
export const TEST_PHASE = {
  text: "The engine is in a test phase while Engine v3 is built: the current rules trade on paper only to test the systems, not to make money.",
  url: "https://claude.ai/artifact/VSQymBFJQznrFhw5W3Gu7Q",
  link: "Read the Engine v3 plan",
} as const;

/** "retired": decision 15 retired the rule, it keeps trading on paper only as a systems (plumbing) test.
 *  "shadow": measured, never traded. "candidate": an Engine v3 rule in research (ten-year test), not on paper yet. */
export type RuleStatus = "retired" | "shadow" | "candidate";
export const RETIRED_TEXT = "retired, systems test only";
export const RULES: Record<string, { name: string; status: RuleStatus; what: string }> = {
  dip_reversal: { name: "Dip rule", status: "retired", what: "buys a sharp stock-specific fall with no company news, after a 5-minute higher low" },
  spy_late_momentum: { name: "SPY late-day rule", status: "retired", what: "buys SPY in the last 30 minutes when the day is already strong" },
  orb_stocks_in_play: { name: "Opening-breakout rule", status: "shadow", what: "a break above the first 5-minute high on heavy volume" },
  news_continuation: { name: "News rule", status: "shadow", what: "follows a stock that moves on fresh news" },
  trend_momentum: { name: "Trend momentum", status: "candidate", what: "holds the strongest steady uptrends for weeks" },
  mom_12_1_band: { name: "12-month momentum", status: "candidate", what: "holds last year's winners for months" },
  spy_noise_band: { name: "SPY noise band", status: "candidate", what: "buys SPY when it leaves its usual intraday range" },
  spy_noise_band_carry: { name: "SPY noise band, held overnight", status: "candidate", what: "the noise band, holding to the next open" },
};
const STATUS_TEXT: Record<RuleStatus, string> = { retired: RETIRED_TEXT, shadow: "test only, never traded", candidate: "Engine v3 candidate, research only" };
const words = (code: string) => code.replace(/_/g, " ");
export const ruleName = (code: string) => RULES[code]?.name ?? words(code);
export const ruleStatus = (code: string): RuleStatus | null => RULES[code]?.status ?? null;
export const ruleStatusText = (code: string) => { const s = ruleStatus(code); return s ? STATUS_TEXT[s] : null; };
/** "Dip rule (retired, systems test only)": for places that can only hold text, such as an <option> or a tooltip. */
export const ruleLabel = (code: string) => { const t = ruleStatusText(code); return t ? `${ruleName(code)} (${t})` : ruleName(code); };

/** Why a trade closed (trade.exit_reason). */
export const EXIT_TEXT: Record<string, string> = {
  stop: "stop hit", target: "target reached", trail: "trailing stop", time: "closed at 15:55 ET (end of day)",
  moc: "sold at the close", manual: "closed by you", daily_stop: "daily loss stop: everything sold",
  run_drawdown: "run loss limit: everything sold", carried_over: "closed: left over from an earlier day",
  watchdog: "closed by the watchdog", deadman: "closed by the dead-man check",
};
export const exitText = (code: string | null | undefined) => (code ? EXIT_TEXT[code] ?? words(code) : "open");

/** The risk gate's checks (risk/gate.py), as questions answered "yes" when they pass. */
export const CHECK_TEXT: Record<string, string> = {
  kill_switch: "Kill switch is off", reconciled: "Positions match the broker", market_open: "Market is open",
  entry_window: "Inside the hours for new trades", event_blackout: "Not near a Fed or CPI release",
  data_ok: "Price data is fresh and complete", pump_risk: "No pump-like price move", symbol_halted: "Stock is not halted",
  duplicate_symbol: "No position in this stock already", entries_today: "New trades today under the limit",
  run_drawdown_pct: "Run loss under the limit (%)", quote_age_s: "Quote fresh enough (seconds)", no_short: "Buying, not shorting",
  stop_side: "Stop is below the buy price", daily_loss_pct: "Today's loss under the daily stop (%)",
  max_positions: "Open positions under the limit", qty_positive: "At least one share", position_pct: "Position within the size cap (% of pot)",
  open_risk_pct: "Open risk within the budget (% of pot)",
};
export const checkText = (code: string) => CHECK_TEXT[code] ?? words(code);

/** What limited the size of a trade (gate `binding`). */
export const BINDING_TEXT: Record<string, string> = {
  risk_per_trade: "the loss limit per trade", position_cap: "the largest-position cap", open_risk_headroom: "the risk budget for open trades",
  buying_power: "the cash available", invalid_stop: "an invalid stop",
};
export const bindingText = (code: string | null | undefined) => (code ? BINDING_TEXT[code] ?? words(code) : "—");

/** Commands waiting for the engine (kb_command kinds). */
export const COMMAND_TEXT: Record<string, string> = {
  pause: "pause new trades", resume: "resume new trades", flatten_and_stop: "sell everything and stop", set_level: "change the level",
  new_run: "start a new run",
};
export const commandText = (code: string) => COMMAND_TEXT[code] ?? words(code);

/** The agent room's and the news triage's cause codes. */
export const CAUSE_TEXT: Record<string, string> = {
  company_news: "company news", sector: "sector move", macro: "macro", noise: "no identifiable cause (noise)", unknown: "unknown",
};
export const causeText = (code: string | null | undefined) => (code ? CAUSE_TEXT[code] ?? words(code) : "—");

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

/** A rule veto or a Report "setup not taken" reason, in plain words. */
export function vetoText(v: string): string {
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
  const rule = ruleName(it.strategy);
  const seen = it.reasons.map((r) => SAW[r] ?? r.replace(/_/g, " "));
  const saw = seen.length ? `${rule}: the stock ${seen.join(", ")}.` : `${rule}.`;
  let outcome: string;
  if (it.mode === "shadow") outcome = "Test only: this rule is being measured, never traded. Nothing was bought.";
  else if (opts.taken) outcome = `Bought ${it.qty ?? 0} shares.`;
  else if (it.veto) outcome = `Not bought: ${vetoText(it.veto)}.`;
  else if (opts.roomWhy) outcome = `Not bought: the agent room said no (${opts.roomWhy}).`;
  else if (it.approved === false) outcome = `Not bought: a risk check failed${it.binding && GATE[it.binding] ? ` (${GATE[it.binding]})` : ""}.`;
  else if ((it.qty ?? 0) === 0 && it.approved) outcome = `Not bought: ${GATE[it.binding ?? ""] ?? "the size came out at 0 shares"}.`;
  else outcome = "Not bought.";
  const detail = [it.reasons.join(" · "), it.veto && `veto: ${it.veto}`, it.binding && `size limit: ${it.binding}`]
    .filter(Boolean).join(" — ");
  return { saw, outcome, detail };
}
