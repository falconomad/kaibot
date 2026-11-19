// Which positions are meant to carry a protective stop at the broker (stakeholder, 18 Nov 2026: "7 stocks have no
// safety stop" was a false alarm). Pure: unit-tested in nightfix.test.tsx.
//
// The momentum rules have no per-stock stop by design: a held name that drops out of the rule's target list is sold
// at a close, and the whole book has its stop line (src/kaibot/strategies/momentum.py, "there is no stop to manage";
// docs/prereg/trend_momentum.md, "No fixed stop"; engine/live_daily.py, PREREG_STOP_ATR). The engine still writes
// `stop_state: "missing"` for their ledger_position rows until it runs the code of migration 0028, so this list is now
// only the FALLBACK for such older rows (see exitOf). The source decides first: kb_book's `stop_state`
// "none_by_design" (0028) and the heartbeat's multi_day.exits / rules[*].exit (engine/live_daily.exit_kind). Every
// other rule (intraday ones, or a future multi-day rule with a stop in its pre-registration) carries one.
export const NO_STOP_RULES: readonly string[] = ["trend_momentum", "mom_12_1_band", "mom_12_1_band_nofilter"];

/** Exit kinds a source may send (kb_book position `exit`): only "stop" carries a broker stop. */
const STOP_EXITS = ["stop", "stop_loss", "protective_stop", "broker_stop"];
const LIST_EXITS = ["target_list"];

/** True when this rule's positions should have a protective stop resting at the broker. A neighbour of a rule
 *  (`trend_momentum~reg60`) counts as the rule. `exit`, when the source sends it, decides. */
export function needsStop(rule: string, exit?: string | null): boolean {
  if (typeof exit === "string" && exit.trim()) return STOP_EXITS.includes(exit.trim().toLowerCase());
  const base = rule.split("~")[0].trim();
  return !NO_STOP_RULES.includes(base);
}

export type ExitKind = "target_list" | "broker_stop";
const kindOf = (v: unknown): ExitKind | null => {
  const k = typeof v === "string" ? v.trim().toLowerCase() : "";
  return LIST_EXITS.includes(k) ? "target_list" : STOP_EXITS.includes(k) ? "broker_stop" : null;
};

/** How a position leaves, from the source first (19 Nov 2026, after migration 0028 and #339):
 *  1. kb_book `stop_state` "none_by_design": the engine says it has no stop by design;
 *  2. the heartbeat's exit kind for the rule (multi_day.exits / rules[*].exit; a neighbour `rule~x` counts as its rule);
 *  3. the row's own `exit`, unless its stop_state is "missing": kb_book (0028) derives `exit` from stop_state ("broker_stop"
 *     for anything but none_by_design), so on a "missing" row it cannot tell an older momentum row (written before the
 *     engine ran 0028's code) from a real missing stop;
 *  4. otherwise (an older row: stop_state "missing" or absent, and no word from the heartbeat): NO_STOP_RULES above. */
export function exitOf(p: { rule: string; exit?: string | null; stopState?: string | null }, kinds: Record<string, string> = {}): ExitKind {
  if (p.stopState === "none_by_design") return "target_list";
  const hb = kindOf(kinds[p.rule]) ?? kindOf(kinds[p.rule.split("~")[0].trim()]);
  if (hb) return hb;
  const own = kindOf(p.exit);
  if (own === "target_list" || (own && p.stopState !== "missing")) return own;
  return needsStop(p.rule) ? "broker_stop" : "target_list";
}
