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
    "exclude": ["INTC"],                                   // never traded, in every mode (also removed from the focus set)
    "mode": "all" | "prefer" | "only",                     // all = no focus; only needs >= 5 symbols after exclude
    "custom": {                                            // optional (S68): a custom ticker list, stored BY VALUE
      "name": "AI picks",                                  // <= 40 characters, informational
      "tickers": [{"symbol": "PLTR", "etf": "XLK"}]        // 1..40 accepted tickers outside the 59, see below
    }
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
  entry: {start: "09:35", end: "15:45"}, strategies: ["dip_reversal", ...level's] }`. Where a smaller value is stricter
  (risk, positions, stops, pause_losses, vix_max, max_spread_bp), max = the level's value. Where a larger value is
  stricter (pause_minutes, event_window_min, min_price_usd, min_adv_usd), min = the level's value. Steps are UI-only.
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
- In every mode, excluded stocks are never tradable.

SPY momentum has no stored replay yet, so the page says "not estimated yet".

Show `≈ N trades/month`, and the evidence pace as months to 900 trades. Rough guide: < 15/month "very slow
evidence", < 40 "slow", otherwise "normal".

## Custom ticker lists (S68, #96, migration 0013: waits for the stakeholder)

The owner may type any US stock ticker. Kaibot checks it, and only **accepted** tickers can be put in a config.
The safety filters are the same as for every catalogue stock and **cannot be overridden**.

### Checking a ticker: `kb_check_ticker(p_symbol text)`
Owner-checked. The symbol is trimmed and upper-cased. Returns:
```jsonc
{
  "symbol": "PLTR",
  "status": "accepted" | "refused" | "pending" | "error",
  "reason": null | "plain sentence",        // always set for refused and error
  "etf": "XLK",                             // accepted only: the sector fund for the sector-adjusted features
  "in_catalogue": false,                    // true = one of the 59: put it in focus.symbols, not in focus.custom
  "name": "Palantir Technologies Inc", "industry": "Technology",
  "checked_on": "2026-11-12",               // ET day; the answer is cached for that day
  "detail": {"sessions": 72, "adv20_usd": 3.1e9, "last_close": 61.2, "exchange": "NASDAQ", "as_of": "2026-11-12"}
}
```
- **`pending`**: the check is queued (the database cannot call the data vendors; a worker answers within a few
  seconds). Call `kb_check_ticker` again every 2 seconds, for at most 30 seconds, then show "still checking".
- **`error`**: the vendors could not be reached. Calling again queues a new attempt.
- Catalogue stocks, reference funds and badly formed symbols are answered at once, without a vendor call.
- At most 200 new checks a day (free data limits). Past that the call raises `22023`.

A ticker is **accepted** when all of these hold, on completed sessions before today (point-in-time):
1. listed on NYSE, NASDAQ, ARCA, AMEX or BATS, and active and tradable at the broker;
2. at least 60 trading days of price history;
3. last close of $5 or more;
4. average daily $ volume over the last 20 sessions above $50,000,000.

The spread and quote-freshness limits need a live quote, so they stay where they are: checked at every entry.
The sector fund comes from the company's Finnhub industry; an unknown industry uses SPY (the market).

Refusal reasons (shown as they are; `X` is the ticker):
| Case | `reason` |
|---|---|
| not a ticker | That does not look like a US stock ticker (1 to 5 letters, for example PLTR). |
| SPY or a sector fund | X is a reference fund Kaibot compares stocks against, so it cannot be traded. |
| unknown at the broker | We could not find X at the broker. Check the spelling. |
| OTC, crypto, not a stock | X is not a stock listed on a major US exchange. |
| halted or not tradable | X cannot be traded at the broker right now. |
| too new | X has only N trading days of price history; Kaibot needs at least 60. |
| price under $5 | X closed at $P; Kaibot only trades stocks above $5. |
| too thinly traded | X trades about $NM a day; Kaibot needs more than $50M so orders fill at a fair price. |
| vendors unreachable (`error`) | We could not check X right now. Try again later. |

### Named lists (per owner)
A list is a saved selection of symbols. It gives no permission by itself: tickers are checked when used.
| RPC | Args | Returns |
|---|---|---|
| `kb_ticker_lists()` | | `{ max_tickers: 40, max_lists: 20, min_only: 5, lists: [{name, updated_at, symbols: [...]}] }` |
| `kb_save_ticker_list(p_name text, p_symbols text[])` | name, symbols | `{ ok, name, symbols }`; same name = replace |
| `kb_delete_ticker_list(p_name text)` | name | `{ ok: true }` |

Each entry of `symbols` in `kb_ticker_lists` is `{symbol, status, in_catalogue, reason?, etf?, ...}` with the
latest check of the last 7 days, or `status: "unchecked"`. Symbols are trimmed, upper-cased and de-duplicated; a
list may mix catalogue stocks and other tickers.

Errors (`22023`, message shown as it is):
- Give the list a name of 1 to 40 characters.
- A list needs at least one ticker.
- A list can hold at most 40 tickers.
- One of the tickers does not look like a US stock symbol.
- Reference funds (SPY and the sector funds) cannot be in a list.
- You can keep at most 20 lists. Delete one first.

### Using a list in a config
The config stores the list **by value**: editing or deleting a named list later never changes an applied version.
To use a list, the page writes it into the draft:
- symbols with `in_catalogue: true` go to `focus.symbols`;
- every other **accepted** symbol goes to `focus.custom.tickers` as `{symbol, etf}`, with the `etf` that
  `kb_check_ticker` returned;
- `focus.custom.name` is the list's name; `focus.mode` is usually `"only"`.

Rules, checked by `kb_save_config` and `kb_apply_config` (and again by the engine):
- 1 to **40** custom tickers; no duplicates; no catalogue stocks; no reference funds; `etf` is SPY or a sector fund;
- every custom ticker has an **accepted** check from the last 7 days, with the same `etf`;
- *Only these* still needs at least **5** stocks in total (themes + `symbols` + custom tickers, after `exclude`);
- `exclude` is for catalogue stocks; to drop a custom ticker, remove it from the list;
- with mode `all` or `prefer`, custom tickers are traded **in addition to** the 59.

Save and apply errors (`22023`):
- X has not been checked yet. Check it before saving.
- X cannot be used: *the refusal reason*
- X has a different sector fund than its check
- X is already in the catalogue: pick it as a stock instead
- X is a reference fund and cannot be traded
- X has an unknown sector fund
- a custom ticker is not a valid US stock symbol
- a custom ticker is listed twice
- a custom list needs at least one ticker
- a custom list can hold at most 40 tickers
- the list name must be text of at most 40 characters
- 'Only these' needs at least 5 stocks

### What the engine does
- The applied version takes effect at the **next session open**, like every config change. The history of new
  tickers is downloaded before that open (from 45 minutes ahead; the first load of 40 tickers takes a few minutes).
- At every open the engine runs the acceptance check again on the data it loaded. A ticker that no longer passes
  is **not scanned that day** and can never be entered; a `custom_tickers` event lists what was accepted and
  refused, with the reason. The rest of the list trades as usual.
- Shorts, leverage and overnight holds stay off. A new list is a new config version: a new report chapter, a
  trial row and a queued replay. The replay waits (`waiting_for_data`) until the minute history of the custom
  tickers is on the replay machine.
- Estimator: there is no stored trade rate for a custom ticker. Show "not estimated yet" for them.
