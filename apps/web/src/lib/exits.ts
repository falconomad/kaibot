// Which positions are meant to carry a protective stop at the broker (stakeholder, 18 Nov 2026: "7 stocks have no
// safety stop" was a false alarm). Pure: unit-tested in nightfix.test.tsx.
//
// The momentum rules have no per-stock stop by design: a held name that drops out of the rule's target list is sold
// at a close, and the whole book has its stop line (src/kaibot/strategies/momentum.py, "there is no stop to manage";
// docs/prereg/trend_momentum.md, "No fixed stop"; engine/live_daily.py, PREREG_STOP_ATR). The engine still writes
// `stop_state: "missing"` for their ledger_position rows, so until Backend exposes each rule's exit kind in kb_book
// (a position's `exit`), this list says which rules sell by list instead of by stop. Every other rule (intraday ones,
// or a future multi-day rule with a stop in its pre-registration) is expected to carry one, so the alarm stays.
export const NO_STOP_RULES: readonly string[] = ["trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"];

/** Exit kinds a source may send (kb_book position `exit`): only "stop" carries a broker stop. */
const STOP_EXITS = ["stop", "stop_loss", "protective_stop"];

/** True when this rule's positions should have a protective stop resting at the broker. A neighbour of a rule
 *  (`trend_momentum~reg60`) counts as the rule. `exit`, when the source sends it, decides. */
export function needsStop(rule: string, exit?: string | null): boolean {
  if (typeof exit === "string" && exit.trim()) return STOP_EXITS.includes(exit.trim().toLowerCase());
  const base = rule.split("~")[0].trim();
  return !NO_STOP_RULES.includes(base);
}
