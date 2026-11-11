/** Deep-coerce numeric strings (Postgres numerics) in known numeric keys. */
const NUMERIC = new Set(["pnl", "pnl_usd", "pnl_venue", "pnl_shadow", "spy_ret", "cash_ret", "mean_r", "sd_r", "t", "ci_lo", "ci_hi",
  "qty", "entry_px", "exit_px", "risk_usd", "entry", "stop", "target", "venue_px", "shadow_px", "fees", "win", "r",
  "start_equity", "r_multiple"]);
export function coerce<T>(v: T): T {
  if (Array.isArray(v)) return v.map(coerce) as T;
  if (v && typeof v === "object") {
    const o: Record<string, unknown> = {};
    for (const [k, x] of Object.entries(v as Record<string, unknown>))
      o[k] = NUMERIC.has(k) && typeof x === "string" ? Number(x) : coerce(x);
    return o as T;
  }
  return v;
}
