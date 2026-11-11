# M5 contract: config body and RPCs (decision 12)

The backend (migration 0011, `engine/userconfig.py`) and the frontend (`/strategy`) both build against this.
**Principle:** a config can only make Kaibot pickier or smaller. Any field that is missing means "the level's value".
The server validates every save and apply. The engine validates again at the session open and refuses the session
if anything is off (fail closed).

## Config body (`schema: 1`)
```jsonc
{
  "schema": 1,
  "preset": "default" | "big_tech" | "calm" | "custom",   // UI only, informational
  "boldness": 0..4,                                        // UI only: a shortcut that wrote the risk fields below
  "focus": {
    "themes": ["hyperscalers", "semiconductors"],          // keys of config/universe_seed.yaml `themes`
    "symbols": ["CRM"],                                    // extra symbols from the 59-name universe
    "exclude": ["INTC"],                                   // removed from the focus set
    "mode": "all" | "prefer" | "only"                      // all = no focus; only needs >= 5 symbols after exclude
  },
  "risk": {                       // each value must be >= its floor and <= the level's value
    "risk_per_trade_pct": 0.5,    // floor 0.10
    "max_positions": 4,           // floor 1
    "max_position_pct": 12,       // floor 5
    "max_entries_per_day": 15,    // floor 1
    "daily_stop_pct": 1.0,        // floor 0.25 (engine: max_daily_loss_pct)
    "run_drawdown_end_pct": 6     // floor 2
  },
  "strategies": { "dip_reversal": true, "spy_late_momentum": false },  // >= 1 on; SPY only if the level has it
  "timing": {
    "entry_start": "09:35", "entry_end": "15:45",   // HH:MM ET inside 09:35-15:45, start < end, >= 30 min apart
    "pause_losses": 3,           // 2 or 3 losses in a row trigger a pause (level: 3)
    "pause_minutes": 60,         // 60..240 (level: 60)
    "vix_max": 35,               // 20..35: no new trades when the prior VIX close is above (35 = crisis gate)
    "event_window_min": 30       // 30..90: Fed/CPI quiet window, +/- minutes (always on)
  },
  "advanced": {
    "min_price_usd": 5,          // >= 5
    "min_adv_usd": 50000000,     // >= 50,000,000
    "max_spread_bp": 5           // 1..5
  }
}
```
Locked, never configurable: the news veto, the 15:55 flatten, brackets, quote freshness, no shorts/leverage/overnight,
and the strategy entry thresholds.

## RPCs (owner-checked `security definer`; the browser has no direct table access)
| RPC | Args | Returns |
|---|---|---|
| `kb_get_config(p_run bigint)` | run id | `{ level, bounds, themes, universe, applied, pending, draft, versions }` (below) |
| `kb_save_config(p_run bigint, p_body jsonb)` | full body | `{ ok: true, draft: {body, updated_at} }`; invalid → raises `22023` with the reason in the message |
| `kb_apply_config(p_run bigint, p_idem uuid)` | idempotency key | `{ version, applies_from }`; validates the draft first; supersedes any not-yet-effective version |
| `kb_reset_config(p_run bigint)` | | clears the draft (back to level defaults) |
| `kb_chapters(p_run bigint)` | | `[{version, from, to, n_trades, pnl_usd, mean_r, t_stat, too_early}]` (version 0 = level defaults) |

`kb_get_config` fields:
- `level`: `"conservative" | "balanced" | "aggressive"`.
- `bounds`: `{ risk_per_trade_pct: {min, max}, max_positions: {...}, max_position_pct, max_entries_per_day, daily_stop_pct,
  run_drawdown_end_pct, pause_losses, pause_minutes, vix_max, event_window_min, min_price_usd, min_adv_usd, max_spread_bp,
  entry: {start: "09:35", end: "15:45"}, strategies: ["dip_reversal", ...level's] }`. Each max is the level's value.
- `themes`: `{ key: { label, symbols: [...] } }`. `universe`: the 59 symbols.
- `applied`: the version in effect now, or null for level defaults. `pending`: one applied version that starts at
  the next open, or null. Each is `{version, body, applies_from, created_at}`. `draft`: `{body, updated_at}` or null.
- `versions`: `[{version, status, applies_from, created_at}]`, newest first. `status` is `applied | superseded`.

Timing: `applies_from` is the next session open (09:30 ET) after the apply. On weekends it is the next Monday. On a
market holiday the engine simply applies it at the next real open. At the open of day D the engine uses the latest
non-superseded version created before D 09:30 ET. Positions already open keep the rules they were opened under.

## Estimator (static, `apps/web/src/data/estimator.json`)
- `dip_per_month[symbol]`: shrunk per-symbol dip trades a month, from the 187 hold-out replay trades.
- `entry_share_by_half_hour["0".."360"]`: share of entries by session half-hour.

Estimate = Σ over tradable symbols of `dip_per_month` × the share of the entry window that is selected (dip on).
- **Only** mode: tradable = the focus set.
- **All** and **Prefer**: tradable = all 59. Prefer only ranks focus names first when slots compete.

SPY momentum has no stored replay yet, so the page says "not estimated yet".

Show `≈ N trades/month`, and the evidence pace as months to 900 trades. Rough guide: < 15/month "very slow
evidence", < 40 "slow", otherwise "normal".
